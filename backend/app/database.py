from pathlib import Path
import sqlite3
import json
from collections.abc import Sequence
from typing import Any

from app.analysis_tags import INITIAL_ANALYSIS_TAGS, build_tag_context, validate_tag_conditions


class RaceImportConflictError(Exception):
    pass


class SqliteDatabase:
    """Owns the small public surface needed to operate the local database."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def initialize(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS application_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO application_metadata (key, value)
                VALUES ('schema_version', '1')
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS races (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    organizer TEXT NOT NULL,
                    country TEXT NOT NULL,
                    racecourse TEXT NOT NULL,
                    race_date TEXT NOT NULL,
                    race_number INTEGER NOT NULL,
                    start_time TEXT NOT NULL,
                    timezone TEXT NOT NULL,
                    start_utc TEXT NOT NULL,
                    surface TEXT NOT NULL,
                    distance_m INTEGER NOT NULL,
                    going TEXT NOT NULL,
                    field_size INTEGER NOT NULL,
                    UNIQUE (organizer, country, racecourse, race_date, race_number)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS runners (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    race_id INTEGER NOT NULL REFERENCES races(id),
                    gate INTEGER NOT NULL,
                    horse_number INTEGER NOT NULL,
                    horse_name TEXT NOT NULL,
                    age INTEGER NOT NULL,
                    sex TEXT NOT NULL,
                    assigned_weight REAL NOT NULL,
                    status TEXT NOT NULL,
                    win_odds REAL NOT NULL,
                    place_odds_min REAL NOT NULL,
                    place_odds_max REAL NOT NULL,
                    raw_inverse_win_odds REAL NOT NULL,
                    normalized_win_market_share REAL NOT NULL,
                    UNIQUE (race_id, horse_number)
                )
                """
            )
            connection.execute(
                "UPDATE application_metadata SET value = '2' WHERE key = 'schema_version'"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS odds_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    race_id INTEGER NOT NULL REFERENCES races(id),
                    observed_at TEXT NOT NULL,
                    received_at TEXT NOT NULL,
                    source TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS odds_snapshot_runners (
                    snapshot_id INTEGER NOT NULL REFERENCES odds_snapshots(id),
                    horse_number INTEGER NOT NULL,
                    win_odds REAL NOT NULL,
                    place_odds_min REAL NOT NULL,
                    place_odds_max REAL NOT NULL,
                    PRIMARY KEY (snapshot_id, horse_number)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS prediction_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    race_id INTEGER NOT NULL REFERENCES races(id),
                    input_snapshot_id INTEGER NOT NULL REFERENCES odds_snapshots(id),
                    model_identifier TEXT NOT NULL,
                    model_version TEXT NOT NULL,
                    frozen_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    invalidation_reason TEXT,
                    replaces_prediction_id INTEGER REFERENCES prediction_runs(id),
                    official_evaluation_eligible INTEGER NOT NULL,
                    evaluation_exclusion_reason TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS runner_predictions (
                    prediction_run_id INTEGER NOT NULL REFERENCES prediction_runs(id),
                    horse_number INTEGER NOT NULL,
                    raw_inverse_win_odds REAL NOT NULL,
                    win_market_share REAL NOT NULL,
                    PRIMARY KEY (prediction_run_id, horse_number)
                )
                """
            )
            connection.execute(
                "UPDATE application_metadata SET value = '3' WHERE key = 'schema_version'"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS analysis_tag_versions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    rule_key TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    evidence_summary TEXT NOT NULL,
                    study_period TEXT NOT NULL,
                    population TEXT NOT NULL,
                    evidence_quality TEXT NOT NULL,
                    conditions_json TEXT NOT NULL,
                    enabled INTEGER NOT NULL,
                    probability_multiplier REAL,
                    created_at TEXT NOT NULL,
                    supersedes_rule_version_id INTEGER REFERENCES analysis_tag_versions(id),
                    UNIQUE (rule_key, version)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS analysis_tag_audit_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    rule_key TEXT NOT NULL,
                    rule_version_id INTEGER NOT NULL REFERENCES analysis_tag_versions(id),
                    action TEXT NOT NULL CHECK (action IN ('enabled', 'disabled', 'version_created')),
                    reason TEXT NOT NULL,
                    occurred_at TEXT NOT NULL
                )
                """
            )
            connection.executemany(
                """
                INSERT OR IGNORE INTO analysis_tag_versions (
                    rule_key, version, title, source_url, evidence_summary,
                    study_period, population, evidence_quality, conditions_json,
                    enabled, probability_multiplier, created_at
                ) VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, 0, NULL, '2026-08-15T00:00:00Z')
                """,
                [
                    (
                        tag["rule_key"], tag["title"], tag["source_url"],
                        tag["evidence_summary"], tag["study_period"], tag["population"],
                        tag["evidence_quality"], json.dumps(tag["conditions"], ensure_ascii=False),
                    )
                    for tag in INITIAL_ANALYSIS_TAGS
                ],
            )
            connection.execute(
                "UPDATE application_metadata SET value = '4' WHERE key = 'schema_version'"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS prediction_analysis_tags (
                    prediction_run_id INTEGER NOT NULL REFERENCES prediction_runs(id),
                    rule_version_id INTEGER NOT NULL REFERENCES analysis_tag_versions(id),
                    context_json TEXT NOT NULL,
                    PRIMARY KEY (prediction_run_id, rule_version_id)
                )
                """
            )
            connection.execute(
                "UPDATE application_metadata SET value = '5' WHERE key = 'schema_version'"
            )

    def check(self) -> None:
        with sqlite3.connect(self._path) as connection:
            row = connection.execute(
                "SELECT value FROM application_metadata WHERE key = 'schema_version'"
            ).fetchone()

        if row != ("5",):
            raise RuntimeError("SQLite schema is not ready")

    def import_race(self, race: dict[str, Any], runners: Sequence[dict[str, Any]]) -> tuple[int, bool]:
        with sqlite3.connect(self._path) as connection:
            connection.row_factory = sqlite3.Row
            existing = connection.execute(
                """
                SELECT * FROM races
                WHERE organizer = ? AND country = ? AND racecourse = ?
                  AND race_date = ? AND race_number = ?
                """,
                (
                    race["organizer"], race["country"], race["racecourse"],
                    race["race_date"], race["race_number"],
                ),
            ).fetchone()
            if existing is not None:
                stored_runners = connection.execute(
                    "SELECT * FROM runners WHERE race_id = ? ORDER BY horse_number",
                    (existing["id"],),
                ).fetchall()
                if self._same_content(existing, stored_runners, race, runners):
                    return int(existing["id"]), False
                raise RaceImportConflictError

            cursor = connection.execute(
                """
                INSERT INTO races (
                    organizer, country, racecourse, race_date, race_number,
                    start_time, timezone, start_utc, surface, distance_m, going,
                    field_size
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    race["organizer"], race["country"], race["racecourse"],
                    race["race_date"], race["race_number"], race["start_time"],
                    race["timezone"], race["start_utc"], race["surface"],
                    race["distance_m"], race["going"], len(runners),
                ),
            )
            race_id = cursor.lastrowid
            if race_id is None:
                raise RuntimeError("Race could not be saved")
            connection.executemany(
                """
                INSERT INTO runners (
                    race_id, gate, horse_number, horse_name, age, sex,
                    assigned_weight, status, win_odds, place_odds_min,
                    place_odds_max, raw_inverse_win_odds,
                    normalized_win_market_share
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        race_id, runner["gate"], runner["horse_number"],
                        runner["horse_name"], runner["age"], runner["sex"],
                        runner["assigned_weight"], runner["status"],
                        runner["win_odds"], runner["place_odds_min"],
                        runner["place_odds_max"], runner["raw_inverse_win_odds"],
                        runner["normalized_win_market_share"],
                    )
                    for runner in runners
                ],
            )
        return race_id, True

    @staticmethod
    def _same_content(
        stored_race: sqlite3.Row,
        stored_runners: Sequence[sqlite3.Row],
        race: dict[str, Any],
        runners: Sequence[dict[str, Any]],
    ) -> bool:
        race_fields = (
            "organizer", "country", "racecourse", "race_date", "race_number",
            "start_time", "timezone", "start_utc", "surface", "distance_m", "going",
        )
        runner_fields = (
            "gate", "horse_number", "horse_name", "age", "sex", "assigned_weight",
            "status", "win_odds", "place_odds_min", "place_odds_max",
            "raw_inverse_win_odds", "normalized_win_market_share",
        )
        incoming_runners = sorted(runners, key=lambda runner: int(runner["horse_number"]))
        return (
            all(stored_race[field] == race[field] for field in race_fields)
            and len(stored_runners) == len(incoming_runners)
            and all(
                all(stored[field] == incoming[field] for field in runner_fields)
                for stored, incoming in zip(stored_runners, incoming_runners, strict=True)
            )
        )

    def get_race(self, race_id: int) -> tuple[sqlite3.Row, list[sqlite3.Row]] | None:
        with sqlite3.connect(self._path) as connection:
            connection.row_factory = sqlite3.Row
            race = connection.execute(
                "SELECT * FROM races WHERE id = ?", (race_id,)
            ).fetchone()
            if race is None:
                return None
            runners = connection.execute(
                "SELECT * FROM runners WHERE race_id = ? ORDER BY horse_number",
                (race_id,),
            ).fetchall()
        return race, runners

    def create_odds_snapshot(
        self,
        race_id: int,
        observed_at: str,
        received_at: str,
        source: str,
        runners: Sequence[dict[str, Any]],
    ) -> int:
        with sqlite3.connect(self._path) as connection:
            expected = {
                int(row[0])
                for row in connection.execute(
                    "SELECT horse_number FROM runners WHERE race_id = ?",
                    (race_id,),
                ).fetchall()
            }
            supplied = {int(runner["horse_number"]) for runner in runners}
            if not expected:
                raise LookupError("race_not_found")
            if supplied != expected or len(supplied) != len(runners):
                raise ValueError("snapshot_runner_mismatch")
            cursor = connection.execute(
                """
                INSERT INTO odds_snapshots (race_id, observed_at, received_at, source)
                VALUES (?, ?, ?, ?)
                """,
                (race_id, observed_at, received_at, source),
            )
            snapshot_id = cursor.lastrowid
            if snapshot_id is None:
                raise RuntimeError("Snapshot could not be saved")
            connection.executemany(
                """
                INSERT INTO odds_snapshot_runners (
                    snapshot_id, horse_number, win_odds, place_odds_min, place_odds_max
                ) VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (
                        snapshot_id, runner["horse_number"], runner["win_odds"],
                        runner["place_odds_min"], runner["place_odds_max"],
                    )
                    for runner in runners
                ],
            )
        return snapshot_id

    def get_odds_snapshot(self, snapshot_id: int) -> tuple[sqlite3.Row, list[sqlite3.Row]] | None:
        with sqlite3.connect(self._path) as connection:
            connection.row_factory = sqlite3.Row
            snapshot = connection.execute(
                "SELECT * FROM odds_snapshots WHERE id = ?", (snapshot_id,)
            ).fetchone()
            if snapshot is None:
                return None
            runners = connection.execute(
                """
                SELECT * FROM odds_snapshot_runners
                WHERE snapshot_id = ? ORDER BY horse_number
                """,
                (snapshot_id,),
            ).fetchall()
        return snapshot, runners

    def list_odds_snapshots(self, race_id: int) -> list[tuple[sqlite3.Row, list[sqlite3.Row]]]:
        with sqlite3.connect(self._path) as connection:
            connection.row_factory = sqlite3.Row
            snapshots = connection.execute(
                "SELECT * FROM odds_snapshots WHERE race_id = ? ORDER BY id",
                (race_id,),
            ).fetchall()
            return [
                (
                    snapshot,
                    connection.execute(
                        "SELECT * FROM odds_snapshot_runners WHERE snapshot_id = ? ORDER BY horse_number",
                        (snapshot["id"],),
                    ).fetchall(),
                )
                for snapshot in snapshots
            ]

    def create_prediction(
        self,
        snapshot_id: int,
        model_identifier: str,
        model_version: str,
        frozen_at: str,
        replaces_prediction_id: int | None = None,
        correction_reason: str | None = None,
    ) -> int:
        with sqlite3.connect(self._path) as connection:
            connection.row_factory = sqlite3.Row
            snapshot = connection.execute(
                "SELECT * FROM odds_snapshots WHERE id = ?", (snapshot_id,)
            ).fetchone()
            if snapshot is None:
                raise LookupError("snapshot_not_found")
            race = connection.execute(
                "SELECT * FROM races WHERE id = ?", (snapshot["race_id"],)
            ).fetchone()
            if race is None:
                raise LookupError("race_not_found")
            if replaces_prediction_id is not None:
                original = connection.execute(
                    "SELECT * FROM prediction_runs WHERE id = ?", (replaces_prediction_id,)
                ).fetchone()
                if original is None:
                    raise LookupError("prediction_not_found")
                if original["status"] != "active":
                    raise ValueError("prediction_already_invalidated")
                if int(original["race_id"]) != int(snapshot["race_id"]):
                    raise ValueError("snapshot_race_mismatch")
                connection.execute(
                    """
                    UPDATE prediction_runs
                    SET status = 'invalidated', invalidation_reason = ?,
                        official_evaluation_eligible = 0,
                        evaluation_exclusion_reason = '理由付きで無効化された旧版'
                    WHERE id = ?
                    """,
                    (correction_reason, replaces_prediction_id),
                )
            eligible = frozen_at < str(race["start_utc"])
            exclusion = None
            if not eligible:
                exclusion = (
                    "発走後に固定されたため公式評価対象外"
                    if replaces_prediction_id is None
                    else "発走後に固定された事後訂正"
                )
            cursor = connection.execute(
                """
                INSERT INTO prediction_runs (
                    race_id, input_snapshot_id, model_identifier, model_version,
                    frozen_at, status, replaces_prediction_id,
                    official_evaluation_eligible, evaluation_exclusion_reason
                ) VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?)
                """,
                (
                    snapshot["race_id"], snapshot_id, model_identifier, model_version,
                    frozen_at, replaces_prediction_id, int(eligible), exclusion,
                ),
            )
            prediction_id = cursor.lastrowid
            if prediction_id is None:
                raise RuntimeError("Prediction could not be saved")
            odds = connection.execute(
                """
                SELECT horse_number, win_odds FROM odds_snapshot_runners
                WHERE snapshot_id = ? ORDER BY horse_number
                """,
                (snapshot_id,),
            ).fetchall()
            raw_values = [1 / float(row["win_odds"]) for row in odds]
            total = sum(raw_values)
            connection.executemany(
                """
                INSERT INTO runner_predictions (
                    prediction_run_id, horse_number, raw_inverse_win_odds, win_market_share
                ) VALUES (?, ?, ?, ?)
                """,
                [
                    (prediction_id, row["horse_number"], raw, raw / total)
                    for row, raw in zip(odds, raw_values, strict=True)
                ],
            )
            race_runners = connection.execute(
                "SELECT * FROM runners WHERE race_id = ? ORDER BY horse_number",
                (snapshot["race_id"],),
            ).fetchall()
            previous_snapshot = connection.execute(
                """
                SELECT id FROM odds_snapshots
                WHERE race_id = ? AND observed_at < ?
                ORDER BY observed_at DESC, id DESC LIMIT 1
                """,
                (snapshot["race_id"], snapshot["observed_at"]),
            ).fetchone()
            previous_odds = None
            previous_snapshot_id = None
            if previous_snapshot is not None:
                previous_snapshot_id = int(previous_snapshot["id"])
                previous_odds = connection.execute(
                    "SELECT * FROM odds_snapshot_runners WHERE snapshot_id = ? ORDER BY horse_number",
                    (previous_snapshot_id,),
                ).fetchall()
            enabled_tags = connection.execute(
                """
                SELECT tag.* FROM analysis_tag_versions AS tag
                JOIN (
                    SELECT rule_key, MAX(version) AS version
                    FROM analysis_tag_versions GROUP BY rule_key
                ) AS latest
                  ON latest.rule_key = tag.rule_key AND latest.version = tag.version
                WHERE tag.enabled = 1 ORDER BY tag.id
                """
            ).fetchall()
            for tag in enabled_tags:
                context = build_tag_context(
                    str(tag["rule_key"]), json.loads(str(tag["conditions_json"])),
                    dict(race), [dict(row) for row in race_runners], [dict(row) for row in odds],
                    dict(snapshot),
                    None if previous_odds is None else [dict(row) for row in previous_odds],
                    previous_snapshot_id,
                )
                if context is not None:
                    connection.execute(
                        """
                        INSERT INTO prediction_analysis_tags (
                            prediction_run_id, rule_version_id, context_json
                        ) VALUES (?, ?, ?)
                        """,
                        (prediction_id, tag["id"], json.dumps(context, ensure_ascii=False)),
                    )
        return prediction_id

    def get_prediction(
        self, prediction_id: int,
    ) -> tuple[sqlite3.Row, list[sqlite3.Row], list[sqlite3.Row]] | None:
        with sqlite3.connect(self._path) as connection:
            connection.row_factory = sqlite3.Row
            prediction = connection.execute(
                "SELECT * FROM prediction_runs WHERE id = ?", (prediction_id,)
            ).fetchone()
            if prediction is None:
                return None
            runners = connection.execute(
                """
                SELECT * FROM runner_predictions
                WHERE prediction_run_id = ? ORDER BY horse_number
                """,
                (prediction_id,),
            ).fetchall()
            tags = connection.execute(
                """
                SELECT tag.rule_key, tag.version, match.context_json
                FROM prediction_analysis_tags AS match
                JOIN analysis_tag_versions AS tag ON tag.id = match.rule_version_id
                WHERE match.prediction_run_id = ? ORDER BY tag.id
                """,
                (prediction_id,),
            ).fetchall()
        return prediction, runners, tags

    def list_predictions(
        self, race_id: int,
    ) -> list[tuple[sqlite3.Row, list[sqlite3.Row], list[sqlite3.Row]]]:
        with sqlite3.connect(self._path) as connection:
            connection.row_factory = sqlite3.Row
            predictions = connection.execute(
                "SELECT * FROM prediction_runs WHERE race_id = ? ORDER BY id",
                (race_id,),
            ).fetchall()
            return [
                (
                    prediction,
                    connection.execute(
                        "SELECT * FROM runner_predictions WHERE prediction_run_id = ? ORDER BY horse_number",
                        (prediction["id"],),
                    ).fetchall(),
                    connection.execute(
                        """
                        SELECT tag.rule_key, tag.version, match.context_json
                        FROM prediction_analysis_tags AS match
                        JOIN analysis_tag_versions AS tag ON tag.id = match.rule_version_id
                        WHERE match.prediction_run_id = ? ORDER BY tag.id
                        """,
                        (prediction["id"],),
                    ).fetchall(),
                )
                for prediction in predictions
            ]

    def list_analysis_tags(self) -> list[sqlite3.Row]:
        with sqlite3.connect(self._path) as connection:
            connection.row_factory = sqlite3.Row
            return connection.execute(
                """
                SELECT tag.* FROM analysis_tag_versions AS tag
                JOIN (
                    SELECT rule_key, MAX(version) AS version
                    FROM analysis_tag_versions GROUP BY rule_key
                ) AS latest
                  ON latest.rule_key = tag.rule_key AND latest.version = tag.version
                ORDER BY tag.id
                """
            ).fetchall()

    def get_analysis_tag(self, tag_id: int) -> sqlite3.Row | None:
        with sqlite3.connect(self._path) as connection:
            connection.row_factory = sqlite3.Row
            row: sqlite3.Row | None = connection.execute(
                "SELECT * FROM analysis_tag_versions WHERE id = ?", (tag_id,)
            ).fetchone()
            return row

    @staticmethod
    def _get_latest_analysis_tag(connection: sqlite3.Connection, tag_id: int) -> sqlite3.Row:
        tag: sqlite3.Row | None = connection.execute(
            """
            SELECT requested.*,
                   (SELECT id FROM analysis_tag_versions
                    WHERE rule_key = requested.rule_key
                    ORDER BY version DESC LIMIT 1) AS latest_id
            FROM analysis_tag_versions AS requested WHERE requested.id = ?
            """,
            (tag_id,),
        ).fetchone()
        if tag is None:
            raise LookupError("analysis_tag_not_found")
        if int(tag["latest_id"]) != int(tag["id"]):
            raise ValueError("analysis_tag_version_is_stale")
        return tag

    def set_analysis_tag_state(
        self, tag_id: int, enabled: bool, reason: str, occurred_at: str,
    ) -> None:
        with sqlite3.connect(self._path) as connection:
            connection.row_factory = sqlite3.Row
            tag = self._get_latest_analysis_tag(connection, tag_id)
            connection.execute(
                "UPDATE analysis_tag_versions SET enabled = ? WHERE id = ?",
                (int(enabled), tag_id),
            )
            connection.execute(
                """
                INSERT INTO analysis_tag_audit_events (
                    rule_key, rule_version_id, action, reason, occurred_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (tag["rule_key"], tag_id, "enabled" if enabled else "disabled", reason, occurred_at),
            )

    def create_analysis_tag_version(
        self, tag_id: int, conditions: dict[str, Any], reason: str, occurred_at: str,
    ) -> int:
        with sqlite3.connect(self._path) as connection:
            connection.row_factory = sqlite3.Row
            original = self._get_latest_analysis_tag(connection, tag_id)
            validate_tag_conditions(str(original["rule_key"]), conditions)
            if bool(original["enabled"]):
                connection.execute(
                    "UPDATE analysis_tag_versions SET enabled = 0 WHERE id = ?", (tag_id,)
                )
                connection.execute(
                    """
                    INSERT INTO analysis_tag_audit_events (
                        rule_key, rule_version_id, action, reason, occurred_at
                    ) VALUES (?, ?, 'disabled', ?, ?)
                    """,
                    (original["rule_key"], tag_id, f"新版作成: {reason}", occurred_at),
                )
            cursor = connection.execute(
                """
                INSERT INTO analysis_tag_versions (
                    rule_key, version, title, source_url, evidence_summary,
                    study_period, population, evidence_quality, conditions_json,
                    enabled, probability_multiplier, created_at,
                    supersedes_rule_version_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, ?, ?)
                """,
                (
                    original["rule_key"], int(original["version"]) + 1,
                    original["title"], original["source_url"], original["evidence_summary"],
                    original["study_period"], original["population"], original["evidence_quality"],
                    json.dumps(conditions, ensure_ascii=False), occurred_at, tag_id,
                ),
            )
            new_id = cursor.lastrowid
            if new_id is None:
                raise RuntimeError("Analysis tag version could not be saved")
            connection.execute(
                """
                INSERT INTO analysis_tag_audit_events (
                    rule_key, rule_version_id, action, reason, occurred_at
                ) VALUES (?, ?, 'version_created', ?, ?)
                """,
                (original["rule_key"], new_id, reason, occurred_at),
            )
        return int(new_id)

    def list_analysis_tag_versions(self, rule_key: str) -> list[sqlite3.Row]:
        with sqlite3.connect(self._path) as connection:
            connection.row_factory = sqlite3.Row
            return connection.execute(
                "SELECT * FROM analysis_tag_versions WHERE rule_key = ? ORDER BY version",
                (rule_key,),
            ).fetchall()

    def list_analysis_tag_audit(self, rule_key: str) -> list[sqlite3.Row]:
        with sqlite3.connect(self._path) as connection:
            connection.row_factory = sqlite3.Row
            return connection.execute(
                "SELECT * FROM analysis_tag_audit_events WHERE rule_key = ? ORDER BY id",
                (rule_key,),
            ).fetchall()
