from contextlib import closing
import csv
from datetime import datetime, timezone
from io import BytesIO, StringIO
import json
import os
from pathlib import Path
import re
import sqlite3
from typing import Callable, Literal
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

from pydantic import BaseModel


BACKUP_ID_PATTERN = re.compile(
    r"^(manual|pre-restore)-(\d{8}T\d{12}Z)-([0-9a-f]{8})$"
)


class BackupNotFoundError(Exception):
    pass


class InvalidBackupError(Exception):
    pass


class BackupSummary(BaseModel):
    id: str
    created_at: str
    kind: Literal["manual", "pre_restore"]
    size_bytes: int
    verified: bool


class RestoreSummary(BaseModel):
    restored_backup_id: str
    safety_backup: BackupSummary


class ExportArtifact(BaseModel):
    content: bytes
    media_type: str
    filename: str


class DataMaintenance:
    """Creates, verifies, restores, and exposes exports for one local SQLite database."""

    def __init__(self, database_path: Path, now_provider: Callable[[], datetime]) -> None:
        self._database_path = database_path
        self._now_provider = now_provider
        self._backup_directory = database_path.parent / "backups"

    def create_backup(
        self, kind: Literal["manual", "pre_restore"] = "manual",
    ) -> BackupSummary:
        created_at = self._now_provider().astimezone(timezone.utc)
        backup_id = self._new_backup_id(kind, created_at)
        self._backup_directory.mkdir(parents=True, exist_ok=True)
        destination_path = self._path_for(backup_id)
        temporary_path = destination_path.with_suffix(".tmp")
        try:
            self._copy_database(self._database_path, temporary_path)
            self._verify_path(temporary_path)
            os.replace(temporary_path, destination_path)
        finally:
            temporary_path.unlink(missing_ok=True)
        return self._summary(backup_id, verified=True)

    def list_backups(self) -> list[BackupSummary]:
        if not self._backup_directory.exists():
            return []
        summaries = [
            self._summary(path.stem, verified=self._is_valid(path))
            for path in self._backup_directory.glob("*.sqlite3")
            if BACKUP_ID_PATTERN.fullmatch(path.stem)
        ]
        return sorted(summaries, key=lambda item: (item.created_at, item.id), reverse=True)

    def verify_backup(self, backup_id: str) -> BackupSummary:
        path = self._existing_path(backup_id)
        self._verify_path(path)
        return self._summary(backup_id, verified=True)

    def restore_backup(self, backup_id: str) -> RestoreSummary:
        source_path = self._existing_path(backup_id)
        self._verify_path(source_path)
        safety_backup = self.create_backup("pre_restore")
        try:
            self._copy_database(source_path, self._database_path)
            self._verify_path(self._database_path)
        except (OSError, sqlite3.Error, InvalidBackupError):
            self._copy_database(self._path_for(safety_backup.id), self._database_path)
            raise
        return RestoreSummary(
            restored_backup_id=backup_id,
            safety_backup=safety_backup,
        )

    def export(self, export_format: Literal["json", "csv"]) -> ExportArtifact:
        exported_at = self._now_provider().astimezone(timezone.utc)
        timestamp = exported_at.strftime("%Y%m%dT%H%M%SZ")
        tables = self._read_all_tables()
        if export_format == "json":
            metadata = tables.get("application_metadata", [])
            schema_version = next(
                str(row["value"]) for row in metadata if row["key"] == "schema_version"
            )
            payload = {
                "schema_version": schema_version,
                "exported_at": exported_at.isoformat().replace("+00:00", "Z"),
                "tables": tables,
            }
            return ExportArtifact(
                content=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
                media_type="application/json",
                filename=f"horse-racing-analytics-{timestamp}.json",
            )

        output = BytesIO()
        with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
            for table_name, rows in tables.items():
                archive.writestr(f"{table_name}.csv", self._csv_bytes(table_name, rows))
        return ExportArtifact(
            content=output.getvalue(),
            media_type="application/zip",
            filename=f"horse-racing-analytics-{timestamp}-csv.zip",
        )

    def _read_all_tables(self) -> dict[str, list[dict[str, object]]]:
        with closing(sqlite3.connect(self._database_path)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("BEGIN")
            table_names = [
                str(row[0]) for row in connection.execute(
                    """
                    SELECT name FROM sqlite_master
                    WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                    ORDER BY name
                    """
                ).fetchall()
            ]
            return {
                table_name: [
                    dict(row) for row in connection.execute(
                        f'SELECT * FROM "{table_name}" ORDER BY rowid'
                    ).fetchall()
                ]
                for table_name in table_names
            }

    def _csv_bytes(self, table_name: str, rows: list[dict[str, object]]) -> bytes:
        with closing(sqlite3.connect(self._database_path)) as connection:
            columns = [
                str(row[1]) for row in connection.execute(
                    f'PRAGMA table_info("{table_name}")'
                ).fetchall()
            ]
        output = StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
        return output.getvalue().encode("utf-8")

    @staticmethod
    def _copy_database(source_path: Path, destination_path: Path) -> None:
        with (
            closing(sqlite3.connect(source_path)) as source,
            closing(sqlite3.connect(destination_path)) as destination,
        ):
            source.backup(destination)

    @staticmethod
    def _verify_path(path: Path) -> None:
        try:
            uri = path.resolve().as_uri() + "?mode=ro"
            with closing(sqlite3.connect(uri, uri=True)) as connection:
                integrity = connection.execute("PRAGMA integrity_check").fetchone()
                schema = connection.execute(
                    "SELECT value FROM application_metadata WHERE key = 'schema_version'"
                ).fetchone()
                foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchone()
        except (OSError, sqlite3.Error) as error:
            raise InvalidBackupError("backup_invalid") from error
        if integrity != ("ok",) or schema != ("14",) or foreign_key_errors is not None:
            raise InvalidBackupError("backup_invalid")

    def _existing_path(self, backup_id: str) -> Path:
        if not BACKUP_ID_PATTERN.fullmatch(backup_id):
            raise BackupNotFoundError("backup_not_found")
        path = self._path_for(backup_id)
        if not path.is_file():
            raise BackupNotFoundError("backup_not_found")
        return path

    def _path_for(self, backup_id: str) -> Path:
        return self._backup_directory / f"{backup_id}.sqlite3"

    @staticmethod
    def _new_backup_id(kind: Literal["manual", "pre_restore"], created_at: datetime) -> str:
        prefix = "manual" if kind == "manual" else "pre-restore"
        timestamp = created_at.strftime("%Y%m%dT%H%M%S%fZ")
        return f"{prefix}-{timestamp}-{uuid4().hex[:8]}"

    def _summary(self, backup_id: str, *, verified: bool) -> BackupSummary:
        match = BACKUP_ID_PATTERN.fullmatch(backup_id)
        if match is None:
            raise BackupNotFoundError("backup_not_found")
        kind: Literal["manual", "pre_restore"] = (
            "manual" if match.group(1) == "manual" else "pre_restore"
        )
        created_at = datetime.strptime(
            match.group(2), "%Y%m%dT%H%M%S%fZ",
        ).replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
        return BackupSummary(
            id=backup_id,
            created_at=created_at,
            kind=kind,
            size_bytes=self._path_for(backup_id).stat().st_size,
            verified=verified,
        )

    def _is_valid(self, path: Path) -> bool:
        try:
            self._verify_path(path)
        except InvalidBackupError:
            return False
        return True
