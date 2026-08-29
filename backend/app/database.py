from pathlib import Path
import sqlite3
import json
from collections.abc import Sequence
from typing import Any

from app.analysis_tags import INITIAL_ANALYSIS_TAGS, build_tag_context, validate_tag_conditions
from app.race_analysis import win_market_baseline


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
                    observed_at TEXT,
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
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS bets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    race_id INTEGER NOT NULL REFERENCES races(id),
                    horse_number INTEGER NOT NULL,
                    bet_type TEXT NOT NULL CHECK (bet_type IN ('win', 'place')),
                    decision_type TEXT NOT NULL CHECK (decision_type IN ('candidate', 'discretionary')),
                    amount_yen INTEGER NOT NULL CHECK (amount_yen > 0 AND amount_yen % 100 = 0),
                    placed_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'invalidated')),
                    invalidation_reason TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS result_versions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    race_id INTEGER NOT NULL REFERENCES races(id),
                    version INTEGER NOT NULL,
                    received_at TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('active', 'superseded')),
                    correction_reason TEXT,
                    supersedes_result_version_id INTEGER REFERENCES result_versions(id),
                    UNIQUE (race_id, version)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS runner_results (
                    result_version_id INTEGER NOT NULL REFERENCES result_versions(id),
                    horse_number INTEGER NOT NULL,
                    finish_position INTEGER,
                    status TEXT NOT NULL CHECK (status IN ('確定', '取消', '除外')),
                    win_payout_per_100 INTEGER NOT NULL CHECK (win_payout_per_100 >= 0),
                    place_payout_per_100 INTEGER NOT NULL CHECK (place_payout_per_100 >= 0),
                    PRIMARY KEY (result_version_id, horse_number)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS settlements (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bet_id INTEGER NOT NULL REFERENCES bets(id),
                    result_version_id INTEGER NOT NULL REFERENCES result_versions(id),
                    stake_yen INTEGER NOT NULL,
                    payout_yen INTEGER NOT NULL,
                    refund_yen INTEGER NOT NULL,
                    profit_yen INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'superseded')),
                    UNIQUE (bet_id, result_version_id)
                )
                """
            )
            connection.execute(
                "UPDATE application_metadata SET value = '6' WHERE key = 'schema_version'"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS acquired_race_cards (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    organizer TEXT NOT NULL, country TEXT NOT NULL, racecourse TEXT NOT NULL,
                    race_date TEXT NOT NULL, race_number INTEGER NOT NULL, start_time TEXT NOT NULL,
                    timezone TEXT NOT NULL, start_utc TEXT NOT NULL, surface TEXT NOT NULL,
                    distance_m INTEGER NOT NULL, going TEXT NOT NULL, field_size INTEGER NOT NULL,
                    source_url TEXT NOT NULL, source_race_id TEXT NOT NULL,
                    received_at TEXT NOT NULL, source_updated_at TEXT,
                    parser_version TEXT NOT NULL, response_sha256 TEXT NOT NULL,
                    validation_status TEXT NOT NULL CHECK (validation_status = 'valid'),
                    version INTEGER NOT NULL, status TEXT NOT NULL CHECK (status IN ('active', 'superseded')),
                    supersedes_card_id INTEGER REFERENCES acquired_race_cards(id),
                    UNIQUE (source_url, response_sha256)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS acquired_race_card_runners (
                    card_id INTEGER NOT NULL REFERENCES acquired_race_cards(id),
                    gate INTEGER NOT NULL, horse_number INTEGER NOT NULL, horse_name TEXT NOT NULL,
                    age INTEGER NOT NULL, sex TEXT NOT NULL, assigned_weight REAL NOT NULL,
                    status TEXT NOT NULL, PRIMARY KEY (card_id, horse_number)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS acquisition_failures (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, source_url TEXT NOT NULL,
                    received_at TEXT NOT NULL, parser_version TEXT NOT NULL,
                    response_sha256 TEXT NOT NULL,
                    validation_status TEXT NOT NULL CHECK (validation_status = 'invalid'),
                    error_code TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "UPDATE application_metadata SET value = '7' WHERE key = 'schema_version'"
            )
            connection.execute(
                """
                UPDATE acquired_race_cards
                SET source_race_id =
                    'JRA-' || substr(source_race_id, 22, 8) || '-' ||
                    substr(source_race_id, 10, 2) || '-' ||
                    substr(source_race_id, 16, 2) || '-' ||
                    substr(source_race_id, 18, 2) || '-' ||
                    substr(source_race_id, 20, 2)
                WHERE source_race_id GLOB 'pw01dde[01][10]*'
                """
            )
            connection.execute(
                "UPDATE application_metadata SET value = '8' WHERE key = 'schema_version'"
            )
            observed_at_column = next(
                row for row in connection.execute("PRAGMA table_info(odds_snapshots)").fetchall()
                if row[1] == "observed_at"
            )
            if observed_at_column[3] == 1:
                connection.execute(
                    """CREATE TABLE odds_snapshots_v9 (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        race_id INTEGER NOT NULL REFERENCES races(id),
                        observed_at TEXT, received_at TEXT NOT NULL, source TEXT NOT NULL
                    )"""
                )
                connection.execute(
                    "INSERT INTO odds_snapshots_v9 (id,race_id,observed_at,received_at,source) SELECT id,race_id,observed_at,received_at,source FROM odds_snapshots"
                )
                connection.execute("DROP TABLE odds_snapshots")
                connection.execute("ALTER TABLE odds_snapshots_v9 RENAME TO odds_snapshots")
            connection.execute(
                """CREATE TABLE IF NOT EXISTS jra_race_registrations (
                    card_id INTEGER PRIMARY KEY REFERENCES acquired_race_cards(id),
                    race_id INTEGER NOT NULL REFERENCES races(id)
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS jra_odds_observations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    snapshot_id INTEGER NOT NULL UNIQUE REFERENCES odds_snapshots(id),
                    source_url TEXT NOT NULL, source_race_id TEXT NOT NULL,
                    received_at TEXT NOT NULL, source_updated_at TEXT,
                    parser_version TEXT NOT NULL, response_sha256 TEXT NOT NULL,
                    validation_status TEXT NOT NULL CHECK (validation_status = 'valid')
                )"""
            )
            connection.execute(
                "UPDATE application_metadata SET value = '9' WHERE key = 'schema_version'"
            )

    def check(self) -> None:
        with sqlite3.connect(self._path) as connection:
            row = connection.execute(
                "SELECT value FROM application_metadata WHERE key = 'schema_version'"
            ).fetchone()

        if row != ("9",):
            raise RuntimeError("SQLite schema is not ready")

    def register_jra_race_with_odds(
        self, card_id: int, odds: Sequence[dict[str, Any]], observation: dict[str, Any],
    ) -> tuple[int, int]:
        with sqlite3.connect(self._path) as connection:
            connection.row_factory = sqlite3.Row
            card = connection.execute("SELECT * FROM acquired_race_cards WHERE id = ?", (card_id,)).fetchone()
            if card is None:
                raise LookupError("race_card_not_found")
            card_runners = connection.execute(
                "SELECT * FROM acquired_race_card_runners WHERE card_id = ? ORDER BY horse_number", (card_id,),
            ).fetchall()
            expected = {int(row["horse_number"]) for row in card_runners}
            supplied = {int(row["horse_number"]) for row in odds}
            if expected != supplied or len(supplied) != len(odds):
                raise ValueError("snapshot_runner_mismatch")
            if str(card["source_race_id"]) != str(observation["source_race_id"]):
                raise ValueError("odds_race_mismatch")
            registered = connection.execute(
                "SELECT race_id FROM jra_race_registrations WHERE card_id = ?", (card_id,),
            ).fetchone()
            if registered is None:
                existing = connection.execute(
                    "SELECT id FROM races WHERE organizer=? AND country=? AND racecourse=? AND race_date=? AND race_number=?",
                    (card["organizer"], card["country"], card["racecourse"], card["race_date"], card["race_number"]),
                ).fetchone()
                if existing is not None:
                    raise RaceImportConflictError
                cursor = connection.execute(
                    """INSERT INTO races (organizer,country,racecourse,race_date,race_number,start_time,timezone,
                    start_utc,surface,distance_m,going,field_size) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    tuple(card[key] for key in ("organizer","country","racecourse","race_date","race_number","start_time","timezone","start_utc","surface","distance_m","going","field_size")),
                )
                race_id = cursor.lastrowid
                if race_id is None:
                    raise RuntimeError("Race could not be saved")
                odds_by_number = {int(row["horse_number"]): row for row in odds}
                market_by_number = {
                    int(row["horse_number"]): values
                    for row, values in zip(
                        odds,
                        win_market_baseline([float(row["win_odds"]) for row in odds]),
                        strict=True,
                    )
                }
                connection.executemany(
                    """INSERT INTO runners (race_id,gate,horse_number,horse_name,age,sex,assigned_weight,status,
                    win_odds,place_odds_min,place_odds_max,raw_inverse_win_odds,normalized_win_market_share)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    [(race_id, row["gate"], row["horse_number"], row["horse_name"], row["age"], row["sex"],
                      row["assigned_weight"], row["status"], odds_by_number[int(row["horse_number"])]["win_odds"],
                      odds_by_number[int(row["horse_number"])]["place_odds_min"], odds_by_number[int(row["horse_number"])]["place_odds_max"],
                      *market_by_number[int(row["horse_number"])]) for row in card_runners],
                )
                connection.execute("INSERT INTO jra_race_registrations (card_id,race_id) VALUES (?,?)", (card_id, race_id))
            else:
                race_id = int(registered["race_id"])
            snapshot = connection.execute(
                "INSERT INTO odds_snapshots (race_id,observed_at,received_at,source) VALUES (?,?,?,'jra_web')",
                (race_id, observation["source_updated_at"], observation["received_at"]),
            )
            snapshot_id = snapshot.lastrowid
            if snapshot_id is None:
                raise RuntimeError("Snapshot could not be saved")
            connection.executemany(
                "INSERT INTO odds_snapshot_runners (snapshot_id,horse_number,win_odds,place_odds_min,place_odds_max) VALUES (?,?,?,?,?)",
                [(snapshot_id, row["horse_number"], row["win_odds"], row["place_odds_min"], row["place_odds_max"]) for row in odds],
            )
            connection.execute(
                """INSERT INTO jra_odds_observations (snapshot_id,source_url,source_race_id,received_at,source_updated_at,
                parser_version,response_sha256,validation_status) VALUES (?,?,?,?,?,?,?,?)""",
                (snapshot_id, observation["url"], observation["source_race_id"], observation["received_at"],
                 observation["source_updated_at"], observation["parser_version"], observation["response_sha256"], observation["validation_status"]),
            )
            return int(race_id), int(snapshot_id)

    def save_acquired_race_card(
        self, race: dict[str, Any], runners: Sequence[dict[str, Any]], observation: dict[str, Any],
    ) -> int:
        with sqlite3.connect(self._path) as connection:
            existing = connection.execute(
                "SELECT id FROM acquired_race_cards WHERE source_url = ? AND response_sha256 = ?",
                (observation["url"], observation["response_sha256"]),
            ).fetchone()
            if existing is not None:
                return int(existing[0])
            previous = connection.execute(
                "SELECT id, version FROM acquired_race_cards WHERE source_race_id = ? AND status = 'active'",
                (observation["source_race_id"],),
            ).fetchone()
            version = 1 if previous is None else int(previous[1]) + 1
            if previous is not None:
                connection.execute(
                    "UPDATE acquired_race_cards SET status = 'superseded' WHERE id = ?", (previous[0],),
                )
            cursor = connection.execute(
                """
                INSERT INTO acquired_race_cards (
                    organizer, country, racecourse, race_date, race_number, start_time, timezone,
                    start_utc, surface, distance_m, going, field_size, source_url, source_race_id, received_at,
                    source_updated_at, parser_version, response_sha256, validation_status,
                    version, status, supersedes_card_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?)
                """,
                tuple(race[key] for key in (
                    "organizer", "country", "racecourse", "race_date", "race_number", "start_time",
                    "timezone", "start_utc", "surface", "distance_m", "going", "field_size",
                )) + tuple(observation[key] for key in (
                    "url", "source_race_id", "received_at", "source_updated_at", "parser_version", "response_sha256", "validation_status",
                )) + (version, None if previous is None else previous[0]),
            )
            card_id = cursor.lastrowid
            if card_id is None:
                raise RuntimeError("Race card could not be saved")
            connection.executemany(
                """INSERT INTO acquired_race_card_runners
                (card_id, gate, horse_number, horse_name, age, sex, assigned_weight, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                [(card_id, *(runner[key] for key in (
                    "gate", "horse_number", "horse_name", "age", "sex", "assigned_weight", "status",
                ))) for runner in runners],
            )
            return int(card_id)

    def list_acquired_race_cards(self) -> list[tuple[sqlite3.Row, list[sqlite3.Row]]]:
        with sqlite3.connect(self._path) as connection:
            connection.row_factory = sqlite3.Row
            cards = connection.execute("SELECT * FROM acquired_race_cards ORDER BY id").fetchall()
            return [(card, connection.execute(
                "SELECT * FROM acquired_race_card_runners WHERE card_id = ? ORDER BY horse_number", (card["id"],)
            ).fetchall()) for card in cards]

    def save_acquisition_failure(self, observation: dict[str, Any]) -> None:
        with sqlite3.connect(self._path) as connection:
            connection.execute(
                """INSERT INTO acquisition_failures
                (source_url, received_at, parser_version, response_sha256, validation_status, error_code)
                VALUES (?, ?, ?, ?, ?, ?)""",
                tuple(observation[key] for key in (
                    "url", "received_at", "parser_version", "response_sha256", "validation_status", "error_code",
                )),
            )

    def list_acquisition_failures(self) -> list[sqlite3.Row]:
        with sqlite3.connect(self._path) as connection:
            connection.row_factory = sqlite3.Row
            return connection.execute("SELECT * FROM acquisition_failures ORDER BY id").fetchall()

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

    def get_race(self, race_id: int) -> tuple[sqlite3.Row, list[Any]] | None:
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
            latest_snapshot = connection.execute(
                "SELECT id FROM odds_snapshots WHERE race_id = ? ORDER BY id DESC LIMIT 1",
                (race_id,),
            ).fetchone()
            if latest_snapshot is not None:
                odds = connection.execute(
                    "SELECT * FROM odds_snapshot_runners WHERE snapshot_id = ? ORDER BY horse_number",
                    (latest_snapshot["id"],),
                ).fetchall()
                odds_by_number = {int(row["horse_number"]): row for row in odds}
                runners = [{
                    **dict(runner),
                    "win_odds": odds_by_number[int(runner["horse_number"])]["win_odds"],
                    "place_odds_min": odds_by_number[int(runner["horse_number"])]["place_odds_min"],
                    "place_odds_max": odds_by_number[int(runner["horse_number"])]["place_odds_max"],
                } for runner in runners]
        return race, runners

    def list_races(self) -> list[sqlite3.Row]:
        with sqlite3.connect(self._path) as connection:
            connection.row_factory = sqlite3.Row
            return connection.execute(
                "SELECT * FROM races ORDER BY start_utc DESC, id DESC"
            ).fetchall()

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
            market_values = win_market_baseline([float(row["win_odds"]) for row in odds])
            connection.executemany(
                """
                INSERT INTO runner_predictions (
                    prediction_run_id, horse_number, raw_inverse_win_odds, win_market_share
                ) VALUES (?, ?, ?, ?)
                """,
                [
                    (prediction_id, row["horse_number"], raw, share)
                    for row, (raw, share) in zip(odds, market_values, strict=True)
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

    def create_bet(
        self, race_id: int, horse_number: int, bet_type: str,
        decision_type: str, amount_yen: int, placed_at: str,
    ) -> int:
        with sqlite3.connect(self._path) as connection:
            connection.row_factory = sqlite3.Row
            runner = connection.execute(
                "SELECT * FROM runners WHERE race_id = ? AND horse_number = ?",
                (race_id, horse_number),
            ).fetchone()
            if runner is None:
                raise LookupError("runner_not_found")
            active_result = connection.execute(
                "SELECT id FROM result_versions WHERE race_id = ? AND status = 'active'",
                (race_id,),
            ).fetchone()
            if active_result is not None:
                raise ValueError("race_already_settled")
            if decision_type == "candidate":
                # Candidate records will be introduced with the independent-model workflow.
                # Until then, accepting this label would mix discretionary judgment into model results.
                raise ValueError("candidate_not_available")
            if bet_type == "place":
                field_size = connection.execute(
                    "SELECT COUNT(*) FROM runners WHERE race_id = ?", (race_id,)
                ).fetchone()
                if field_size is None or int(field_size[0]) < 5:
                    raise ValueError("place_not_offered")
            cursor = connection.execute(
                """
                INSERT INTO bets (
                    race_id, horse_number, bet_type, decision_type,
                    amount_yen, placed_at, status
                ) VALUES (?, ?, ?, ?, ?, ?, 'active')
                """,
                (race_id, horse_number, bet_type, decision_type, amount_yen, placed_at),
            )
            bet_id = cursor.lastrowid
            if bet_id is None:
                raise RuntimeError("Bet could not be saved")
        return int(bet_id)

    def get_bet(self, bet_id: int) -> sqlite3.Row | None:
        with sqlite3.connect(self._path) as connection:
            connection.row_factory = sqlite3.Row
            row: sqlite3.Row | None = connection.execute(
                "SELECT * FROM bets WHERE id = ?", (bet_id,)
            ).fetchone()
            return row

    def create_result_version(
        self, race_id: int, results: list[dict[str, Any]], received_at: str,
        correction_reason: str | None = None,
    ) -> int:
        with sqlite3.connect(self._path) as connection:
            connection.row_factory = sqlite3.Row
            race = connection.execute("SELECT id FROM races WHERE id = ?", (race_id,)).fetchone()
            if race is None:
                raise LookupError("race_not_found")
            active = connection.execute(
                "SELECT * FROM result_versions WHERE race_id = ? AND status = 'active'",
                (race_id,),
            ).fetchone()
            if active is not None and correction_reason is None:
                stored_results = connection.execute(
                    """
                    SELECT horse_number, finish_position, status,
                           win_payout_per_100, place_payout_per_100
                    FROM runner_results
                    WHERE result_version_id = ?
                    ORDER BY horse_number
                    """,
                    (active["id"],),
                ).fetchall()
                supplied = sorted(
                    (
                        int(result["horse_number"]), result["finish_position"], result["status"],
                        int(result["win_payout_per_100"]), int(result["place_payout_per_100"]),
                    )
                    for result in results
                )
                stored = [
                    (
                        int(result["horse_number"]), result["finish_position"], result["status"],
                        int(result["win_payout_per_100"]), int(result["place_payout_per_100"]),
                    )
                    for result in stored_results
                ]
                if supplied == stored:
                    return int(active["id"])
                raise ValueError("result_import_conflict")
            if active is None and correction_reason is not None:
                raise ValueError("result_not_imported")
            runner_numbers = {
                int(row[0]) for row in connection.execute(
                    "SELECT horse_number FROM runners WHERE race_id = ?", (race_id,)
                ).fetchall()
            }
            supplied_numbers = {int(result["horse_number"]) for result in results}
            if supplied_numbers != runner_numbers:
                raise ValueError("result_runner_mismatch")
            field_size = len(runner_numbers)
            place_positions = 0 if field_size <= 4 else 2 if field_size <= 7 else 3
            for result in results:
                if result["status"] != "確定":
                    continue
                finish_position = int(result["finish_position"])
                if finish_position > field_size:
                    raise ValueError("result_invalid_position")
                win_should_pay = finish_position == 1
                place_should_pay = place_positions > 0 and finish_position <= place_positions
                if (int(result["win_payout_per_100"]) > 0) != win_should_pay:
                    raise ValueError("result_payout_mismatch")
                if (int(result["place_payout_per_100"]) > 0) != place_should_pay:
                    raise ValueError("result_payout_mismatch")
            version = 1 if active is None else int(active["version"]) + 1
            supersedes_id = None if active is None else int(active["id"])
            if active is not None:
                connection.execute(
                    "UPDATE result_versions SET status = 'superseded' WHERE id = ?",
                    (active["id"],),
                )
                connection.execute(
                    "UPDATE settlements SET status = 'superseded' WHERE result_version_id = ?",
                    (active["id"],),
                )
            cursor = connection.execute(
                """
                INSERT INTO result_versions (
                    race_id, version, received_at, status, correction_reason,
                    supersedes_result_version_id
                ) VALUES (?, ?, ?, 'active', ?, ?)
                """,
                (race_id, version, received_at, correction_reason, supersedes_id),
            )
            result_version_id = cursor.lastrowid
            if result_version_id is None:
                raise RuntimeError("Result version could not be saved")
            connection.executemany(
                """
                INSERT INTO runner_results (
                    result_version_id, horse_number, finish_position, status,
                    win_payout_per_100, place_payout_per_100
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                [(
                    result_version_id, result["horse_number"], result["finish_position"],
                    result["status"], result["win_payout_per_100"], result["place_payout_per_100"],
                ) for result in results],
            )
            bets = connection.execute(
                "SELECT * FROM bets WHERE race_id = ? AND status = 'active' ORDER BY id",
                (race_id,),
            ).fetchall()
            results_by_horse = {int(result["horse_number"]): result for result in results}
            for bet in bets:
                result = results_by_horse[int(bet["horse_number"])]
                stake = int(bet["amount_yen"])
                refunded = result["status"] in {"取消", "除外"}
                payout_per_100 = int(result[
                    "win_payout_per_100" if bet["bet_type"] == "win" else "place_payout_per_100"
                ])
                payout = 0 if refunded else stake // 100 * payout_per_100
                refund = stake if refunded else 0
                connection.execute(
                    """
                    INSERT INTO settlements (
                        bet_id, result_version_id, stake_yen, payout_yen,
                        refund_yen, profit_yen, status
                    ) VALUES (?, ?, ?, ?, ?, ?, 'active')
                    """,
                    (bet["id"], result_version_id, stake, payout, refund, payout + refund - stake),
                )
        return int(result_version_id)

    def list_result_versions(
        self, race_id: int,
    ) -> list[tuple[sqlite3.Row, list[sqlite3.Row]]]:
        with sqlite3.connect(self._path) as connection:
            connection.row_factory = sqlite3.Row
            versions = connection.execute(
                "SELECT * FROM result_versions WHERE race_id = ? ORDER BY version",
                (race_id,),
            ).fetchall()
            return [
                (
                    version,
                    connection.execute(
                        "SELECT * FROM runner_results WHERE result_version_id = ? ORDER BY horse_number",
                        (version["id"],),
                    ).fetchall(),
                )
                for version in versions
            ]

    def get_result_version(
        self, result_version_id: int,
    ) -> tuple[sqlite3.Row, list[sqlite3.Row]] | None:
        with sqlite3.connect(self._path) as connection:
            connection.row_factory = sqlite3.Row
            version = connection.execute(
                "SELECT * FROM result_versions WHERE id = ?", (result_version_id,)
            ).fetchone()
            if version is None:
                return None
            runners = connection.execute(
                "SELECT * FROM runner_results WHERE result_version_id = ? ORDER BY horse_number",
                (result_version_id,),
            ).fetchall()
        return version, runners

    def get_race_ledger(
        self, race_id: int,
    ) -> tuple[
        list[sqlite3.Row], tuple[sqlite3.Row, list[sqlite3.Row]] | None, list[sqlite3.Row]
    ]:
        with sqlite3.connect(self._path) as connection:
            connection.row_factory = sqlite3.Row
            bets = connection.execute(
                "SELECT * FROM bets WHERE race_id = ? AND status = 'active' ORDER BY id",
                (race_id,),
            ).fetchall()
            version = connection.execute(
                "SELECT * FROM result_versions WHERE race_id = ? AND status = 'active'",
                (race_id,),
            ).fetchone()
            result = None
            if version is not None:
                result = (
                    version,
                    connection.execute(
                        "SELECT * FROM runner_results WHERE result_version_id = ? ORDER BY horse_number",
                        (version["id"],),
                    ).fetchall(),
                )
            settlements = connection.execute(
                """
                SELECT settlement.*, bet.decision_type
                FROM settlements AS settlement
                JOIN bets AS bet ON bet.id = settlement.bet_id
                WHERE bet.race_id = ? AND bet.status = 'active' AND settlement.status = 'active'
                ORDER BY settlement.id
                """,
                (race_id,),
            ).fetchall()
        return bets, result, settlements
