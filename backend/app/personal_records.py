import sqlite3
from collections.abc import Callable
from contextlib import closing
import hashlib
import json
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


Mark = Literal["◎", "○", "▲", "△", "☆", "消"]


class OpinionRevisionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: UUID
    mark: Mark | None
    memo: str | None
    skip_reason: str | None

    @model_validator(mode="after")
    def validate_text_length(self) -> "OpinionRevisionCreate":
        if len(self.memo or "") + len(self.skip_reason or "") > 2000:
            raise ValueError("memoとskip_reasonの合計は2000文字以内にしてください。")
        return self

    def is_empty(self) -> bool:
        return self.mark is None and not self.memo and not self.skip_reason


class ReflectionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: UUID
    reflection: str = Field(max_length=2000)

    @field_validator("reflection")
    @classmethod
    def validate_reflection(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("振り返りを入力してください。")
        return value


class OpinionRevision(BaseModel):
    id: int
    race_id: int
    horse_number: int
    mark: Mark | None
    memo: str | None
    skip_reason: str | None
    recorded_at: str


class Reflection(BaseModel):
    id: int
    race_id: int
    horse_number: int
    reflection: str
    recorded_at: str


class RunnerPersonalRecord(BaseModel):
    horse_number: int
    current_revision: OpinionRevision | None
    revisions: list[OpinionRevision]
    reflections: list[Reflection]


class PersonalRaceRecords(BaseModel):
    race_id: int
    result_saved: bool
    runners: list[RunnerPersonalRecord]


class PersonalRecordNotFoundError(Exception):
    pass


class PersonalOpinionEmptyError(Exception):
    pass


class ReflectionResultNotSavedError(Exception):
    pass


class PersonalOperationConflictError(Exception):
    pass


class PersonalRecordStore:
    """Stores user-authored race notes separately from system decisions and bets."""

    def __init__(self, database_path: Path, now_provider: Callable[[], str]) -> None:
        self._database_path = database_path
        self._now_provider = now_provider

    def get_race_records(self, race_id: int) -> PersonalRaceRecords:
        with closing(sqlite3.connect(self._database_path)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("BEGIN")
            race = connection.execute("SELECT 1 FROM races WHERE id = ?", (race_id,)).fetchone()
            if race is None:
                raise PersonalRecordNotFoundError
            horse_numbers = [int(row[0]) for row in connection.execute(
                "SELECT horse_number FROM runners WHERE race_id = ? ORDER BY horse_number", (race_id,),
            ).fetchall()]
            opinions = connection.execute(
                """SELECT * FROM personal_runner_opinion_revisions
                    WHERE race_id = ? ORDER BY horse_number, recorded_at, id""", (race_id,),
            ).fetchall()
            reflections = connection.execute(
                """SELECT * FROM personal_runner_reflections
                    WHERE race_id = ? ORDER BY horse_number, recorded_at, id""", (race_id,),
            ).fetchall()
            result_saved = connection.execute(
                "SELECT 1 FROM result_versions WHERE race_id = ? AND status = 'active' LIMIT 1",
                (race_id,),
            ).fetchone() is not None

        opinions_by_horse: dict[int, list[OpinionRevision]] = {number: [] for number in horse_numbers}
        reflections_by_horse: dict[int, list[Reflection]] = {number: [] for number in horse_numbers}
        for row in opinions:
            opinion = OpinionRevision.model_validate(dict(row))
            opinions_by_horse[opinion.horse_number].append(opinion)
        for row in reflections:
            reflection = Reflection.model_validate(dict(row))
            reflections_by_horse[reflection.horse_number].append(reflection)
        return PersonalRaceRecords(
            race_id=race_id,
            result_saved=result_saved,
            runners=[RunnerPersonalRecord(
                horse_number=number,
                current_revision=max(opinions_by_horse[number], key=lambda item: item.id, default=None),
                revisions=opinions_by_horse[number],
                reflections=reflections_by_horse[number],
            ) for number in horse_numbers],
        )

    def append_opinion(
        self, race_id: int, horse_number: int, revision: OpinionRevisionCreate,
    ) -> OpinionRevision:
        with closing(sqlite3.connect(self._database_path)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("BEGIN IMMEDIATE")
            existing = self._get_operation_result(
                connection, revision.operation_id, "opinion", race_id, horse_number,
                self._payload_hash(revision.model_dump(mode="json", exclude={"operation_id"})),
            )
            if existing is not None:
                return OpinionRevision.model_validate(dict(existing))
            self._require_runner(connection, race_id, horse_number)
            previous = connection.execute(
                """SELECT id FROM personal_runner_opinion_revisions
                    WHERE race_id = ? AND horse_number = ? ORDER BY id DESC LIMIT 1""",
                (race_id, horse_number),
            ).fetchone()
            if revision.is_empty() and previous is None:
                raise PersonalOpinionEmptyError
            recorded_at = self._now_provider()
            cursor = connection.execute(
                """INSERT INTO personal_runner_opinion_revisions (
                    race_id, horse_number, mark, memo, skip_reason, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?)""",
                (race_id, horse_number, revision.mark, revision.memo, revision.skip_reason, recorded_at),
            )
            record_id = cursor.lastrowid
            if record_id is None:
                raise RuntimeError("SQLite did not return an inserted opinion revision ID")
            self._record_operation(
                connection, revision.operation_id, "opinion", race_id, horse_number,
                self._payload_hash(revision.model_dump(mode="json", exclude={"operation_id"})),
                record_id, recorded_at,
            )
            connection.commit()
            row = connection.execute(
                "SELECT * FROM personal_runner_opinion_revisions WHERE id = ?", (record_id,),
            ).fetchone()
        return OpinionRevision.model_validate(dict(row))

    def append_reflection(
        self, race_id: int, horse_number: int, reflection: ReflectionCreate,
    ) -> Reflection:
        with closing(sqlite3.connect(self._database_path)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("BEGIN IMMEDIATE")
            existing = self._get_operation_result(
                connection, reflection.operation_id, "reflection", race_id, horse_number,
                self._payload_hash(reflection.model_dump(mode="json", exclude={"operation_id"})),
            )
            if existing is not None:
                return Reflection.model_validate(dict(existing))
            self._require_runner(connection, race_id, horse_number)
            active_result = connection.execute(
                "SELECT 1 FROM result_versions WHERE race_id = ? AND status = 'active' LIMIT 1",
                (race_id,),
            ).fetchone()
            if active_result is None:
                raise ReflectionResultNotSavedError
            recorded_at = self._now_provider()
            cursor = connection.execute(
                """INSERT INTO personal_runner_reflections (
                    race_id, horse_number, reflection, recorded_at
                ) VALUES (?, ?, ?, ?)""",
                (race_id, horse_number, reflection.reflection, recorded_at),
            )
            record_id = cursor.lastrowid
            if record_id is None:
                raise RuntimeError("SQLite did not return an inserted reflection ID")
            self._record_operation(
                connection, reflection.operation_id, "reflection", race_id, horse_number,
                self._payload_hash(reflection.model_dump(mode="json", exclude={"operation_id"})),
                record_id, recorded_at,
            )
            connection.commit()
            row = connection.execute(
                "SELECT * FROM personal_runner_reflections WHERE id = ?", (record_id,),
            ).fetchone()
        return Reflection.model_validate(dict(row))

    @staticmethod
    def _require_runner(connection: sqlite3.Connection, race_id: int, horse_number: int) -> None:
        exists = connection.execute(
            """SELECT 1 FROM races AS race JOIN runners AS runner ON runner.race_id = race.id
                WHERE race.id = ? AND runner.horse_number = ?""",
            (race_id, horse_number),
        ).fetchone()
        if exists is None:
            raise PersonalRecordNotFoundError

    @staticmethod
    def _payload_hash(payload: dict[str, object]) -> str:
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _get_operation_result(
        connection: sqlite3.Connection, operation_id: UUID, operation_type: str,
        race_id: int, horse_number: int, payload_sha256: str,
    ) -> sqlite3.Row | None:
        request = PersonalRecordStore._fetch_row(
            connection,
            """SELECT * FROM personal_operation_requests WHERE operation_id = ?""",
            (str(operation_id),),
        )
        if request is None:
            return None
        if (request["operation_type"] != operation_type or request["race_id"] != race_id
                or request["horse_number"] != horse_number
                or request["payload_sha256"] != payload_sha256):
            raise PersonalOperationConflictError
        table = "personal_runner_opinion_revisions" if operation_type == "opinion" else "personal_runner_reflections"
        return PersonalRecordStore._fetch_row(
            connection,
            f"SELECT * FROM {table} WHERE id = ?",
            (request["record_id"],),
        )

    @staticmethod
    def _fetch_row(
        connection: sqlite3.Connection, query: str, parameters: tuple[object, ...],
    ) -> sqlite3.Row | None:
        row = connection.execute(query, parameters).fetchone()
        if row is not None and not isinstance(row, sqlite3.Row):
            raise RuntimeError("SQLite connection row factory is not configured")
        return row

    def _record_operation(
        self, connection: sqlite3.Connection, operation_id: UUID, operation_type: str,
        race_id: int, horse_number: int, payload_sha256: str, record_id: int, recorded_at: str,
    ) -> None:
        connection.execute(
            """INSERT INTO personal_operation_requests (
                operation_id, operation_type, race_id, horse_number, payload_sha256, record_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (str(operation_id), operation_type, race_id, horse_number, payload_sha256, record_id,
             recorded_at),
        )
