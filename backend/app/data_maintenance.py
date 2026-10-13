from contextlib import closing
import csv
from datetime import datetime, timezone
from io import BytesIO, StringIO
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
from typing import Callable, Literal
from uuid import UUID, uuid4
from zipfile import ZIP_DEFLATED, ZipFile

from pydantic import BaseModel

from app.database import SqliteDatabase


BACKUP_ID_PATTERN = re.compile(
    r"^(manual|pre-restore)-(\d{8}T\d{12}Z)-([0-9a-f]{8})$"
)


class BackupNotFoundError(Exception):
    pass


class InvalidBackupError(Exception):
    pass


class RestoreFailureError(Exception):
    def __init__(self, rollback_verified: bool) -> None:
        super().__init__("restore_failed")
        self.rollback_verified = rollback_verified


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
        staging_path = self._database_path.parent / f".restore-stage-{uuid4().hex}.sqlite3"
        staging_created = False
        try:
            with staging_path.open("xb"):
                staging_created = True
            self._copy_database(source_path, staging_path)
            try:
                if self._schema_version(staging_path) == "18":
                    SqliteDatabase(staging_path).initialize()
            except (OSError, sqlite3.Error, RuntimeError) as error:
                raise InvalidBackupError("backup_invalid") from error
            self._verify_path(staging_path, expected_version="19")

            safety_backup = self.create_backup("pre_restore")
            self._verify_path(self._path_for(safety_backup.id), expected_version="19")
            previous_digest = self._database_digest(self._database_path)
            try:
                self._copy_database(staging_path, self._database_path)
                self._verify_path(self._database_path, expected_version="19")
            except (OSError, sqlite3.Error, InvalidBackupError) as error:
                rollback_verified = False
                try:
                    self._copy_database(self._path_for(safety_backup.id), self._database_path)
                    self._verify_path(self._database_path, expected_version="19")
                    rollback_verified = self._database_digest(self._database_path) == previous_digest
                except (OSError, sqlite3.Error, InvalidBackupError):
                    rollback_verified = False
                raise RestoreFailureError(rollback_verified) from error
        finally:
            if staging_created:
                staging_path.unlink(missing_ok=True)
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
            closing(sqlite3.connect(DataMaintenance._read_only_uri(source_path), uri=True)) as source,
            closing(sqlite3.connect(destination_path)) as destination,
        ):
            source.backup(destination)

    @staticmethod
    def _read_only_uri(path: Path) -> str:
        return path.resolve().as_uri() + "?mode=ro"

    @staticmethod
    def _schema_version(path: Path) -> str:
        try:
            with closing(sqlite3.connect(DataMaintenance._read_only_uri(path), uri=True)) as connection:
                row = connection.execute(
                    "SELECT value FROM application_metadata WHERE key = 'schema_version'"
                ).fetchone()
        except (OSError, sqlite3.Error) as error:
            raise InvalidBackupError("backup_invalid") from error
        if row is None or str(row[0]) not in {"18", "19"}:
            raise InvalidBackupError("backup_invalid")
        return str(row[0])

    @staticmethod
    def _verify_path(path: Path, *, expected_version: str | None = None) -> None:
        try:
            uri = DataMaintenance._read_only_uri(path)
            with closing(sqlite3.connect(uri, uri=True)) as connection:
                integrity = connection.execute("PRAGMA integrity_check").fetchone()
                schema = connection.execute(
                    "SELECT value FROM application_metadata WHERE key = 'schema_version'"
                ).fetchone()
                foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchone()
                personal_schema_valid = (
                    schema is None or str(schema[0]) != "19"
                    or DataMaintenance._has_formal_personal_schema_19(connection)
                )
        except (OSError, sqlite3.Error) as error:
            raise InvalidBackupError("backup_invalid") from error
        if (integrity != ("ok",) or schema is None or str(schema[0]) not in {"18", "19"}
                or expected_version is not None and str(schema[0]) != expected_version
                or foreign_key_errors is not None or not personal_schema_valid):
            raise InvalidBackupError("backup_invalid")

    @staticmethod
    def _has_formal_personal_schema_19(connection: sqlite3.Connection) -> bool:
        def table_shape_is_valid(
            table_name: str,
            required_columns: dict[str, tuple[str, bool, bool]],
            required_checks: tuple[str, ...],
            runner_index: tuple[str, tuple[str, ...]] | None = None,
        ) -> bool:
            table = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table_name,),
            ).fetchone()
            if table is None or table[0] is None:
                return False
            normalized_sql = re.sub(r"\s+", "", str(table[0]).lower())
            if any(check not in normalized_sql for check in required_checks):
                return False

            columns = {
                str(row[1]): row
                for row in connection.execute(f'PRAGMA table_info("{table_name}")').fetchall()
            }
            for name, (declared_type, required, primary_key) in required_columns.items():
                column = columns.get(name)
                if (column is None or str(column[2]).upper() != declared_type
                        or bool(column[3]) != required or bool(column[5]) != primary_key):
                    return False

            runner_key = sorted(
                (int(row[1]), str(row[2]), str(row[3]), str(row[4]))
                for row in connection.execute(f'PRAGMA foreign_key_list("{table_name}")').fetchall()
            )
            if runner_key != [
                (0, "runners", "race_id", "race_id"),
                (1, "runners", "horse_number", "horse_number"),
            ]:
                return False

            if runner_index is not None:
                index_name, expected_columns = runner_index
                indexes = connection.execute(f'PRAGMA index_list("{table_name}")').fetchall()
                index = next((row for row in indexes if str(row[1]) == index_name and int(row[2]) == 0), None)
                if index is None:
                    return False
                quoted_name = str(index[1]).replace('"', '""')
                indexed_columns = tuple(str(row[2]) for row in connection.execute(
                    f'PRAGMA index_info("{quoted_name}")',
                ).fetchall())
                if indexed_columns != expected_columns:
                    return False
            return True

        if not table_shape_is_valid(
            "personal_runner_opinion_revisions",
            {
                "id": ("INTEGER", False, True), "race_id": ("INTEGER", True, False),
                "horse_number": ("INTEGER", True, False), "mark": ("TEXT", False, False),
                "memo": ("TEXT", False, False), "skip_reason": ("TEXT", False, False),
                "recorded_at": ("TEXT", True, False),
            },
            (
                "check(horse_number>0)",
                "check(markisnullormarkin('◎','○','▲','△','☆','消'))",
            ),
            ("personal_opinions_by_runner", ("race_id", "horse_number", "id")),
        ):
            return False
        if not table_shape_is_valid(
            "personal_runner_reflections",
            {
                "id": ("INTEGER", False, True), "race_id": ("INTEGER", True, False),
                "horse_number": ("INTEGER", True, False), "reflection": ("TEXT", True, False),
                "recorded_at": ("TEXT", True, False),
            },
            ("check(horse_number>0)",),
            ("personal_reflections_by_runner", ("race_id", "horse_number", "id")),
        ):
            return False
        if not table_shape_is_valid(
            "personal_operation_requests",
            {
                "operation_id": ("TEXT", True, True), "operation_type": ("TEXT", True, False),
                "race_id": ("INTEGER", True, False), "horse_number": ("INTEGER", True, False),
                "payload_sha256": ("TEXT", True, False), "record_id": ("INTEGER", True, False),
                "created_at": ("TEXT", True, False),
            },
            (
                "check(operation_typein('opinion','reflection'))",
                "check(horse_number>0)",
            ),
        ):
            return False

        for (operation_id,) in connection.execute(
            "SELECT operation_id FROM personal_operation_requests",
        ).fetchall():
            if not isinstance(operation_id, str):
                return False
            try:
                if str(UUID(operation_id)) != operation_id:
                    return False
            except (ValueError, TypeError, AttributeError):
                return False

        unique_constraints: set[tuple[str, ...]] = set()
        for index in connection.execute('PRAGMA index_list("personal_operation_requests")').fetchall():
            if int(index[2]) == 1:
                quoted_name = str(index[1]).replace('"', '""')
                indexed_columns = connection.execute(
                    f'PRAGMA index_info("{quoted_name}")',
                ).fetchall()
                unique_constraints.add(tuple(str(row[2]) for row in indexed_columns))
        if ("operation_type", "record_id") not in unique_constraints:
            return False

        invalid_mapping = connection.execute(
            """SELECT 1 FROM personal_operation_requests AS operation
                WHERE (operation.operation_type = 'opinion' AND NOT EXISTS (
                    SELECT 1 FROM personal_runner_opinion_revisions AS record
                    WHERE record.id = operation.record_id AND record.race_id = operation.race_id
                      AND record.horse_number = operation.horse_number
                )) OR (operation.operation_type = 'reflection' AND NOT EXISTS (
                    SELECT 1 FROM personal_runner_reflections AS record
                    WHERE record.id = operation.record_id AND record.race_id = operation.race_id
                      AND record.horse_number = operation.horse_number
                )) LIMIT 1""",
        ).fetchone()
        unmapped_record = connection.execute(
            """SELECT 1 FROM personal_runner_opinion_revisions AS record
                WHERE NOT EXISTS (
                    SELECT 1 FROM personal_operation_requests AS operation
                    WHERE operation.operation_type = 'opinion' AND operation.record_id = record.id
                      AND operation.race_id = record.race_id AND operation.horse_number = record.horse_number
                )
                UNION ALL
                SELECT 1 FROM personal_runner_reflections AS record
                WHERE NOT EXISTS (
                    SELECT 1 FROM personal_operation_requests AS operation
                    WHERE operation.operation_type = 'reflection' AND operation.record_id = record.id
                      AND operation.race_id = record.race_id AND operation.horse_number = record.horse_number
                ) LIMIT 1""",
        ).fetchone()
        return invalid_mapping is None and unmapped_record is None

    @staticmethod
    def _database_digest(path: Path) -> str:
        with closing(sqlite3.connect(DataMaintenance._read_only_uri(path), uri=True)) as connection:
            connection.row_factory = sqlite3.Row
            schema_rows = connection.execute(
                """SELECT type, name, sql FROM sqlite_master
                    WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name""",
            ).fetchall()
            table_names = [str(row[0]) for row in connection.execute(
                """SELECT name FROM sqlite_master WHERE type='table'
                    AND name NOT LIKE 'sqlite_%' ORDER BY name""",
            ).fetchall()]
            tables: dict[str, list[dict[str, object]]] = {}
            for name in table_names:
                rows = [dict(row) for row in connection.execute(f'SELECT * FROM "{name}"').fetchall()]
                rows.sort(key=lambda row: json.dumps(row, ensure_ascii=False, sort_keys=True, default=str))
                tables[name] = rows
            payload = {"schema": [tuple(row) for row in schema_rows], "tables": tables}
        serialized = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str,
        ).encode("utf-8")
        return hashlib.sha256(serialized).hexdigest()

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
