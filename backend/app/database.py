from pathlib import Path
import sqlite3
import json
from collections.abc import Sequence
from typing import Any, Literal, cast

from app.analysis_tags import INITIAL_ANALYSIS_TAGS, build_tag_context, validate_tag_conditions
from app.evaluation import PredictionEvaluationRow, SettlementEvaluationRow
from app.race_analysis import win_market_baseline
from app.rule_judgements import INITIAL_RULE, build_runner_judgements


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
            result_version_columns = {
                str(row[1]) for row in connection.execute("PRAGMA table_info(result_versions)").fetchall()
            }
            if "change_summary_json" not in result_version_columns:
                connection.execute(
                    "ALTER TABLE result_versions ADD COLUMN change_summary_json TEXT NOT NULL DEFAULT '[]'"
                )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS jra_result_observations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    result_version_id INTEGER NOT NULL REFERENCES result_versions(id),
                    source_url TEXT NOT NULL, source_race_id TEXT NOT NULL,
                    received_at TEXT NOT NULL, source_updated_at TEXT,
                    parser_version TEXT NOT NULL, response_sha256 TEXT NOT NULL,
                    validation_status TEXT NOT NULL CHECK (validation_status = 'valid')
                )"""
            )
            connection.execute(
                "UPDATE application_metadata SET value = '10' WHERE key = 'schema_version'"
            )
            bet_columns = {
                str(row[1]) for row in connection.execute("PRAGMA table_info(bets)").fetchall()
            }
            for definition in (
                "prediction_run_id INTEGER REFERENCES prediction_runs(id)",
                "odds_snapshot_id INTEGER REFERENCES odds_snapshots(id)",
                "win_odds REAL", "place_odds_min REAL", "place_odds_max REAL",
                "popularity INTEGER",
            ):
                column_name = definition.split()[0]
                if column_name not in bet_columns:
                    connection.execute(f"ALTER TABLE bets ADD COLUMN {definition}")
            connection.execute(
                "UPDATE application_metadata SET value = '11' WHERE key = 'schema_version'"
            )
            prediction_columns = {
                str(row[1]) for row in connection.execute("PRAGMA table_info(prediction_runs)").fetchall()
            }
            for definition in (
                "prediction_kind TEXT NOT NULL DEFAULT 'market_baseline'",
                "prediction_as_of TEXT NOT NULL DEFAULT ''",
                "rationale TEXT NOT NULL DEFAULT 'オッズから計算した市場基準'",
            ):
                column_name = definition.split()[0]
                if column_name not in prediction_columns:
                    connection.execute(f"ALTER TABLE prediction_runs ADD COLUMN {definition}")
            connection.execute(
                "UPDATE prediction_runs SET prediction_as_of = frozen_at WHERE prediction_as_of = ''"
            )
            runner_prediction_columns = {
                str(row[1]) for row in connection.execute("PRAGMA table_info(runner_predictions)").fetchall()
            }
            for definition in ("win_probability REAL", "place_probability REAL"):
                column_name = definition.split()[0]
                if column_name not in runner_prediction_columns:
                    connection.execute(f"ALTER TABLE runner_predictions ADD COLUMN {definition}")
            raw_inverse_column = next(
                row for row in connection.execute("PRAGMA table_info(runner_predictions)").fetchall()
                if row[1] == "raw_inverse_win_odds"
            )
            if raw_inverse_column[3] == 1:
                connection.execute(
                    """
                    CREATE TABLE runner_predictions_v12 (
                        prediction_run_id INTEGER NOT NULL REFERENCES prediction_runs(id),
                        horse_number INTEGER NOT NULL,
                        raw_inverse_win_odds REAL,
                        win_market_share REAL,
                        win_probability REAL,
                        place_probability REAL,
                        PRIMARY KEY (prediction_run_id, horse_number)
                    )
                    """
                )
                connection.execute(
                    """
                    INSERT INTO runner_predictions_v12 (
                        prediction_run_id, horse_number, raw_inverse_win_odds,
                        win_market_share, win_probability, place_probability
                    )
                    SELECT prediction_run_id, horse_number, raw_inverse_win_odds,
                           win_market_share, win_probability, place_probability
                    FROM runner_predictions
                    """
                )
                connection.execute("DROP TABLE runner_predictions")
                connection.execute("ALTER TABLE runner_predictions_v12 RENAME TO runner_predictions")
            connection.execute(
                "UPDATE application_metadata SET value = '12' WHERE key = 'schema_version'"
            )
            connection.execute("""CREATE TABLE IF NOT EXISTS rule_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT, rule_key TEXT NOT NULL, version INTEGER NOT NULL,
                title TEXT NOT NULL, conditions_json TEXT NOT NULL, priority_json TEXT NOT NULL,
                missing_policy TEXT NOT NULL, vocabulary_json TEXT NOT NULL, allowed_fields_json TEXT NOT NULL,
                created_at TEXT NOT NULL, UNIQUE(rule_key, version))""")
            connection.execute("""INSERT OR IGNORE INTO rule_versions (
                rule_key,version,title,conditions_json,priority_json,missing_policy,vocabulary_json,allowed_fields_json,created_at
            ) VALUES (?,?,?,?,?,?,?,?,?)""", (
                INITIAL_RULE["rule_key"], INITIAL_RULE["version"], INITIAL_RULE["title"],
                json.dumps(INITIAL_RULE["conditions"], ensure_ascii=False), json.dumps(INITIAL_RULE["priority"], ensure_ascii=False),
                INITIAL_RULE["missing_policy"], json.dumps(INITIAL_RULE["vocabulary"], ensure_ascii=False),
                json.dumps(INITIAL_RULE["allowed_fields"], ensure_ascii=False), "2026-09-01T00:00:00Z",
            ))
            connection.execute("""CREATE TABLE IF NOT EXISTS rule_judgement_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT, race_id INTEGER NOT NULL REFERENCES races(id),
                input_snapshot_id INTEGER NOT NULL REFERENCES odds_snapshots(id), rule_version_id INTEGER NOT NULL REFERENCES rule_versions(id),
                judgement_as_of TEXT NOT NULL, frozen_at TEXT NOT NULL, status TEXT NOT NULL,
                invalidation_reason TEXT, replaces_judgement_id INTEGER REFERENCES rule_judgement_runs(id),
                official_pre_race_eligible INTEGER NOT NULL, exclusion_reason TEXT)""")
            connection.execute("""CREATE TABLE IF NOT EXISTS runner_rule_judgements (
                judgement_run_id INTEGER NOT NULL REFERENCES rule_judgement_runs(id), horse_number INTEGER NOT NULL,
                horse_name TEXT NOT NULL, judgement TEXT NOT NULL, satisfied_conditions_json TEXT NOT NULL,
                failed_conditions_json TEXT NOT NULL, missing_reasons_json TEXT NOT NULL,
                PRIMARY KEY(judgement_run_id, horse_number))""")
            connection.execute("UPDATE application_metadata SET value = '13' WHERE key = 'schema_version'")
            connection.execute("""CREATE TABLE IF NOT EXISTS meeting_week_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                week_start TEXT NOT NULL, week_end TEXT NOT NULL,
                started_at TEXT NOT NULL, completed_at TEXT,
                status TEXT NOT NULL CHECK (status IN ('running','completed','stopped')),
                target_count INTEGER NOT NULL DEFAULT 0,
                processed_count INTEGER NOT NULL DEFAULT 0,
                ready_count INTEGER NOT NULL DEFAULT 0,
                waiting_count INTEGER NOT NULL DEFAULT 0,
                failed_count INTEGER NOT NULL DEFAULT 0,
                stop_reason TEXT, last_target TEXT
            )""")
            connection.execute("""CREATE UNIQUE INDEX IF NOT EXISTS one_running_meeting_week
                ON meeting_week_runs(status) WHERE status = 'running'""")
            connection.execute("""CREATE TABLE IF NOT EXISTS meeting_week_races (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL REFERENCES meeting_week_runs(id),
                week_start TEXT NOT NULL,
                race_date TEXT NOT NULL, racecourse TEXT NOT NULL,
                meeting_number INTEGER NOT NULL, meeting_day INTEGER NOT NULL,
                race_number INTEGER NOT NULL, race_name TEXT NOT NULL,
                start_time TEXT NOT NULL, surface TEXT NOT NULL,
                distance_m INTEGER NOT NULL, condition_text TEXT NOT NULL,
                source_url TEXT NOT NULL,
                state TEXT NOT NULL CHECK (state IN (
                    'schedule_only','entries_waiting','odds_waiting','judgement_waiting','ready','stopped'
                )),
                race_id INTEGER REFERENCES races(id),
                card_id INTEGER REFERENCES acquired_race_cards(id),
                snapshot_id INTEGER REFERENCES odds_snapshots(id),
                judgement_id INTEGER REFERENCES rule_judgement_runs(id),
                error_code TEXT, updated_at TEXT NOT NULL,
                UNIQUE (run_id, race_date, racecourse, race_number)
            )""")
            connection.execute("""CREATE TABLE IF NOT EXISTS meeting_week_observations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL REFERENCES meeting_week_runs(id),
                source_url TEXT NOT NULL, received_at TEXT NOT NULL,
                parser_version TEXT NOT NULL, response_sha256 TEXT NOT NULL,
                validation_status TEXT NOT NULL CHECK (validation_status IN ('valid','invalid')),
                error_code TEXT,
                UNIQUE (run_id, source_url, response_sha256)
            )""")
            connection.execute("UPDATE application_metadata SET value = '14' WHERE key = 'schema_version'")

    def check(self) -> None:
        with sqlite3.connect(self._path) as connection:
            row = connection.execute(
                "SELECT value FROM application_metadata WHERE key = 'schema_version'"
            ).fetchone()

        if row != ("14",):
            raise RuntimeError("SQLite schema is not ready")

    def start_meeting_week_run(self, week_start: str, week_end: str, started_at: str) -> int:
        with sqlite3.connect(self._path) as connection:
            try:
                cursor = connection.execute(
                    "INSERT INTO meeting_week_runs (week_start,week_end,started_at,status) VALUES (?,?,?,'running')",
                    (week_start, week_end, started_at),
                )
            except sqlite3.IntegrityError as error:
                raise RuntimeError("meeting_week_run_in_progress") from error
            run_id = cursor.lastrowid
            if run_id is None:
                raise RuntimeError("Meeting week run could not be created")
            return int(run_id)

    def stop_stale_meeting_week_runs(self, stopped_at: str) -> None:
        with sqlite3.connect(self._path) as connection:
            run_ids = [int(row[0]) for row in connection.execute(
                "SELECT id FROM meeting_week_runs WHERE status='running'",
            ).fetchall()]
        for run_id in run_ids:
            self.finish_meeting_week_run(
                run_id, "stopped", stopped_at, "アプリ再起動により前回の取得を終了しました。",
            )

    def has_running_meeting_week(self) -> bool:
        with sqlite3.connect(self._path) as connection:
            return connection.execute(
                "SELECT 1 FROM meeting_week_runs WHERE status='running' LIMIT 1",
            ).fetchone() is not None

    def save_meeting_week_observation(self, run_id: int, observation: dict[str, Any]) -> None:
        with sqlite3.connect(self._path) as connection:
            connection.execute(
                """INSERT OR IGNORE INTO meeting_week_observations
                (run_id,source_url,received_at,parser_version,response_sha256,validation_status,error_code)
                VALUES (?,?,?,?,?,?,?)""",
                (run_id, observation["url"], observation["received_at"], observation["parser_version"],
                 observation["response_sha256"], observation["validation_status"], observation.get("error_code")),
            )

    def upsert_meeting_week_race(
        self, run_id: int, week_start: str, race: dict[str, Any], updated_at: str,
    ) -> None:
        with sqlite3.connect(self._path) as connection:
            connection.execute(
                """INSERT INTO meeting_week_races (
                    run_id,week_start,race_date,racecourse,meeting_number,meeting_day,race_number,race_name,
                    start_time,surface,distance_m,condition_text,source_url,state,updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?, 'entries_waiting', ?)
                ON CONFLICT(run_id,race_date,racecourse,race_number) DO UPDATE SET
                    meeting_number=excluded.meeting_number, meeting_day=excluded.meeting_day,
                    race_name=excluded.race_name, start_time=excluded.start_time,
                    surface=excluded.surface, distance_m=excluded.distance_m,
                    condition_text=excluded.condition_text, source_url=excluded.source_url,
                    updated_at=excluded.updated_at""",
                (run_id, week_start, race["race_date"], race["racecourse"], race["meeting_number"], race["meeting_day"],
                 race["race_number"], race["race_name"], race["start_time"], race["surface"], race["distance_m"],
                 race["condition_text"], race["source_url"], updated_at),
            )

    def list_meeting_week_races(self, run_id: int) -> list[sqlite3.Row]:
        with sqlite3.connect(self._path) as connection:
            connection.row_factory = sqlite3.Row
            return connection.execute(
                "SELECT * FROM meeting_week_races WHERE run_id=? ORDER BY race_date,start_time,racecourse,race_number",
                (run_id,),
            ).fetchall()

    def update_meeting_week_race(
        self, run_id: int, race_date: str, racecourse: str, race_number: int,
        *, state: str, updated_at: str, race_id: int | None = None, card_id: int | None = None,
        snapshot_id: int | None = None, judgement_id: int | None = None,
        error_code: str | None = None,
    ) -> None:
        with sqlite3.connect(self._path) as connection:
            connection.execute(
                """UPDATE meeting_week_races SET state=?, updated_at=?,
                    race_id=COALESCE(?,race_id), card_id=COALESCE(?,card_id),
                    snapshot_id=COALESCE(?,snapshot_id), judgement_id=COALESCE(?,judgement_id),
                    error_code=?
                WHERE run_id=? AND race_date=? AND racecourse=? AND race_number=?""",
                (state, updated_at, race_id, card_id, snapshot_id, judgement_id, error_code,
                 run_id, race_date, racecourse, race_number),
            )

    def refresh_meeting_week_run_progress(
        self, run_id: int, processed_count: int, last_target: str | None,
    ) -> None:
        with sqlite3.connect(self._path) as connection:
            run = connection.execute("SELECT id FROM meeting_week_runs WHERE id=?", (run_id,)).fetchone()
            if run is None:
                raise LookupError("meeting_week_run_not_found")
            rows = connection.execute(
                "SELECT state FROM meeting_week_races WHERE run_id=?", (run_id,),
            ).fetchall()
            target_count, ready_count, waiting_count, failed_count = (
                self._summarize_meeting_week_states([str(row[0]) for row in rows])
            )
            connection.execute(
                """UPDATE meeting_week_runs SET target_count=?,processed_count=?,ready_count=?,
                    waiting_count=?,failed_count=?,last_target=? WHERE id=? AND status='running'""",
                (target_count, min(processed_count, target_count), ready_count, waiting_count,
                 failed_count, last_target, run_id),
            )

    def finish_meeting_week_run(
        self, run_id: int, status: str, completed_at: str,
        stop_reason: str | None = None, last_target: str | None = None,
    ) -> None:
        with sqlite3.connect(self._path) as connection:
            run = connection.execute(
                "SELECT id,processed_count FROM meeting_week_runs WHERE id=?", (run_id,),
            ).fetchone()
            if run is None:
                raise LookupError("meeting_week_run_not_found")
            rows = connection.execute(
                "SELECT state FROM meeting_week_races WHERE run_id=?", (run_id,),
            ).fetchall()
            target_count, ready_count, waiting_count, row_failure_count = (
                self._summarize_meeting_week_states([str(row[0]) for row in rows])
            )
            if status == "stopped" and row_failure_count == 0:
                target_count += 1
            failed_count = max(1, row_failure_count) if status == "stopped" else row_failure_count
            processed_count = len(rows) if status == "completed" else min(int(run[1]), target_count)
            connection.execute(
                """UPDATE meeting_week_runs SET completed_at=?,status=?,target_count=?,processed_count=?,
                    ready_count=?,waiting_count=?,failed_count=?,stop_reason=?,last_target=? WHERE id=?""",
                (completed_at, status, target_count, processed_count, ready_count,
                waiting_count, failed_count, stop_reason, last_target, run_id),
            )

    @staticmethod
    def _summarize_meeting_week_states(states: Sequence[str]) -> tuple[int, int, int, int]:
        waiting_states = {"schedule_only", "entries_waiting", "odds_waiting", "judgement_waiting"}
        return (
            len(states),
            sum(1 for state in states if state == "ready"),
            sum(1 for state in states if state in waiting_states),
            sum(1 for state in states if state == "stopped"),
        )

    def get_meeting_week(self, week_start: str) -> tuple[sqlite3.Row, list[dict[str, Any]]] | None:
        with sqlite3.connect(self._path) as connection:
            connection.row_factory = sqlite3.Row
            run = connection.execute(
                "SELECT * FROM meeting_week_runs WHERE week_start=? ORDER BY id DESC LIMIT 1", (week_start,),
            ).fetchone()
            if run is None:
                return None
            def races_for_run(run_id: int) -> list[dict[str, Any]]:
                rows = connection.execute(
                    """SELECT race_date,racecourse,meeting_number,meeting_day,race_number,race_name,
                    start_time,surface,distance_m,condition_text,state,race_id,card_id,snapshot_id,
                    judgement_id,error_code,updated_at,
                    (SELECT rule_version_id FROM rule_judgement_runs
                     WHERE id=meeting_week_races.judgement_id) AS rule_version_id,
                    (SELECT judgement_as_of FROM rule_judgement_runs
                     WHERE id=meeting_week_races.judgement_id) AS judgement_as_of,
                    (SELECT frozen_at FROM rule_judgement_runs
                     WHERE id=meeting_week_races.judgement_id) AS judgement_frozen_at,
                    (SELECT observed_at FROM odds_snapshots
                     WHERE id=meeting_week_races.snapshot_id) AS odds_observed_at,
                    (SELECT start_utc FROM races WHERE id=meeting_week_races.race_id) AS start_utc,
                    (SELECT COUNT(*) FROM runner_rule_judgements
                     WHERE judgement_run_id=meeting_week_races.judgement_id
                       AND judgement='注目') AS attention_horse_count,
                    (SELECT COUNT(*) FROM runner_rule_judgements
                     WHERE judgement_run_id=meeting_week_races.judgement_id
                       AND judgement IN ('注目','見送り')) AS judged_runner_count
                FROM meeting_week_races WHERE meeting_week_races.run_id=?
                ORDER BY race_date,start_time,racecourse,race_number""",
                    (run_id,),
                ).fetchall()
                return [dict(row) for row in rows]

            races = races_for_run(int(run["id"]))
            if str(run["status"]) != "completed":
                completed = connection.execute(
                    """SELECT id FROM meeting_week_runs
                    WHERE week_start=? AND status='completed' AND id<?
                    ORDER BY id DESC LIMIT 1""",
                    (week_start, run["id"]),
                ).fetchone()
                if completed is not None:
                    def race_key(row: dict[str, Any]) -> tuple[str, str, int]:
                        return str(row["race_date"]), str(row["racecourse"]), int(row["race_number"])

                    merged = {race_key(row): row for row in races_for_run(int(completed["id"]))}
                    for current in races:
                        key = race_key(current)
                        previous = merged.get(key)
                        if previous is not None and current["judgement_id"] is None:
                            for field in (
                                "race_id", "snapshot_id", "judgement_id", "rule_version_id",
                                "judgement_as_of", "judgement_frozen_at", "odds_observed_at",
                                "start_utc", "attention_horse_count", "judged_runner_count",
                            ):
                                current[field] = previous[field]
                        merged[key] = current
                    races = sorted(
                        merged.values(),
                        key=lambda row: (
                            row["race_date"], row["start_time"], row["racecourse"], row["race_number"],
                        ),
                    )
            return run, races

    def list_rule_versions(self) -> list[sqlite3.Row]:
        with sqlite3.connect(self._path) as connection:
            connection.row_factory = sqlite3.Row
            return connection.execute("SELECT * FROM rule_versions ORDER BY id").fetchall()

    def create_rule_judgement(self, race_id: int, snapshot_id: int, rule_version_id: int,
                              judgement_as_of: str, frozen_at: str, replaces_id: int | None = None,
                              reason: str | None = None) -> int:
        with sqlite3.connect(self._path) as connection:
            connection.row_factory = sqlite3.Row
            race = connection.execute("SELECT * FROM races WHERE id=?", (race_id,)).fetchone()
            snapshot = connection.execute("SELECT * FROM odds_snapshots WHERE id=? AND race_id=?", (snapshot_id, race_id)).fetchone()
            rule = connection.execute("SELECT * FROM rule_versions WHERE id=?", (rule_version_id,)).fetchone()
            if race is None: raise LookupError("race_not_found")
            if snapshot is None: raise LookupError("snapshot_not_found")
            if rule is None: raise LookupError("rule_version_not_found")
            if snapshot["observed_at"] is None: raise ValueError("snapshot_time_missing")
            if str(snapshot["observed_at"]) > judgement_as_of: raise ValueError("future_snapshot_not_allowed")
            if judgement_as_of > frozen_at: raise ValueError("judgement_as_of_after_freeze")
            if replaces_id is not None:
                original = connection.execute("SELECT * FROM rule_judgement_runs WHERE id=? AND race_id=?", (replaces_id, race_id)).fetchone()
                if original is None: raise LookupError("judgement_not_found")
                if str(original["status"]) != "active": raise ValueError("judgement_already_invalidated")
                connection.execute("UPDATE rule_judgement_runs SET status='invalidated', invalidation_reason=? WHERE id=?", (reason, replaces_id))
            eligible = frozen_at < str(race["start_utc"]) and judgement_as_of < str(race["start_utc"])
            cursor = connection.execute("""INSERT INTO rule_judgement_runs (
                race_id,input_snapshot_id,rule_version_id,judgement_as_of,frozen_at,status,replaces_judgement_id,
                official_pre_race_eligible,exclusion_reason) VALUES (?,?,?,?,?,'active',?,?,?)""",
                (race_id,snapshot_id,rule_version_id,judgement_as_of,frozen_at,replaces_id,int(eligible),None if eligible else "発走後の判定です。"))
            run_id = cursor.lastrowid
            if run_id is None: raise RuntimeError("Judgement could not be saved")
            race_runners = connection.execute("SELECT * FROM runners WHERE race_id=? ORDER BY horse_number", (race_id,)).fetchall()
            snapshot_runners = connection.execute("SELECT * FROM odds_snapshot_runners WHERE snapshot_id=? ORDER BY horse_number", (snapshot_id,)).fetchall()
            outputs = build_runner_judgements(race_runners, snapshot_runners)
            connection.executemany("""INSERT INTO runner_rule_judgements VALUES (?,?,?,?,?,?,?)""", [
                (run_id,item["horse_number"],item["horse_name"],item["judgement"],
                 json.dumps(item["satisfied_conditions"],ensure_ascii=False),json.dumps(item["failed_conditions"],ensure_ascii=False),
                 json.dumps(item["missing_reasons"],ensure_ascii=False)) for item in outputs])
            return int(run_id)

    def get_rule_judgement(self, run_id: int) -> tuple[sqlite3.Row, list[sqlite3.Row]] | None:
        with sqlite3.connect(self._path) as connection:
            connection.row_factory = sqlite3.Row
            run = connection.execute("SELECT * FROM rule_judgement_runs WHERE id=?", (run_id,)).fetchone()
            if run is None: return None
            rows = connection.execute("SELECT * FROM runner_rule_judgements WHERE judgement_run_id=? ORDER BY horse_number", (run_id,)).fetchall()
            return run, rows

    def find_active_rule_judgement(
        self, race_id: int, snapshot_id: int, rule_version_id: int,
    ) -> int | None:
        with sqlite3.connect(self._path) as connection:
            row = connection.execute(
                """SELECT id FROM rule_judgement_runs
                WHERE race_id=? AND input_snapshot_id=? AND rule_version_id=? AND status='active'
                ORDER BY id DESC LIMIT 1""",
                (race_id, snapshot_id, rule_version_id),
            ).fetchone()
            return None if row is None else int(row[0])

    def list_rule_judgements(self, race_id: int) -> list[tuple[sqlite3.Row, list[sqlite3.Row]]]:
        with sqlite3.connect(self._path) as connection:
            connection.row_factory = sqlite3.Row
            runs = connection.execute("SELECT id FROM rule_judgement_runs WHERE race_id=? ORDER BY id", (race_id,)).fetchall()
        return [stored for row in runs if (stored := self.get_rule_judgement(int(row["id"]))) is not None]

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
                prior_registration = connection.execute(
                    """SELECT registrations.race_id
                    FROM jra_race_registrations AS registrations
                    JOIN acquired_race_cards AS cards ON cards.id = registrations.card_id
                    WHERE cards.source_race_id = ?
                    ORDER BY cards.version DESC LIMIT 1""",
                    (card["source_race_id"],),
                ).fetchone()
                if prior_registration is not None:
                    race_id = int(prior_registration["race_id"])
                    connection.execute(
                        "INSERT INTO jra_race_registrations (card_id,race_id) VALUES (?,?)",
                        (card_id, race_id),
                    )
                else:
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
                    inserted_race_id = cursor.lastrowid
                    if inserted_race_id is None:
                        raise RuntimeError("Race could not be saved")
                    race_id = int(inserted_race_id)
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

    def find_jra_odds_snapshot(
        self, card_id: int, source_url: str, response_sha256: str,
    ) -> tuple[int, int] | None:
        with sqlite3.connect(self._path) as connection:
            row = connection.execute(
                """SELECT registrations.race_id, observations.snapshot_id
                FROM jra_race_registrations AS registrations
                JOIN jra_odds_observations AS observations
                  ON observations.snapshot_id IN (
                    SELECT id FROM odds_snapshots WHERE race_id=registrations.race_id
                  )
                WHERE registrations.card_id=? AND observations.source_url=?
                  AND observations.response_sha256=?
                ORDER BY observations.snapshot_id DESC LIMIT 1""",
                (card_id, source_url, response_sha256),
            ).fetchone()
            if row is None:
                return None
            return int(row[0]), int(row[1])

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
                    official_evaluation_eligible, evaluation_exclusion_reason,
                    prediction_kind, prediction_as_of, rationale
                ) VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?, 'market_baseline', ?, ?)
                """,
                (
                    snapshot["race_id"], snapshot_id, model_identifier, model_version,
                    frozen_at, replaces_prediction_id, int(eligible), exclusion,
                    snapshot["observed_at"] or frozen_at, "オッズから計算した市場基準",
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

    def create_independent_prediction(
        self,
        snapshot_id: int,
        model_identifier: str,
        model_version: str,
        prediction_as_of: str,
        rationale: str,
        frozen_at: str,
        outputs: Sequence[dict[str, Any]],
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
            expected = {
                int(row["horse_number"])
                for row in connection.execute(
                    "SELECT horse_number FROM runners WHERE race_id = ?",
                    (snapshot["race_id"],),
                ).fetchall()
            }
            supplied = {int(output["horse_number"]) for output in outputs}
            if supplied != expected or len(outputs) != len(expected):
                raise ValueError("runner_set_mismatch")
            if prediction_as_of > frozen_at:
                raise ValueError("prediction_as_of_after_freeze")
            eligible = frozen_at < str(race["start_utc"]) and prediction_as_of < str(race["start_utc"])
            exclusion = None if eligible else "発走後のモデル出力または固定のため公式評価対象外"
            cursor = connection.execute(
                """
                INSERT INTO prediction_runs (
                    race_id, input_snapshot_id, model_identifier, model_version,
                    frozen_at, status, official_evaluation_eligible,
                    evaluation_exclusion_reason, prediction_kind,
                    prediction_as_of, rationale
                ) VALUES (?, ?, ?, ?, ?, 'active', ?, ?, 'independent', ?, ?)
                """,
                (
                    snapshot["race_id"], snapshot_id, model_identifier, model_version,
                    frozen_at, int(eligible), exclusion, prediction_as_of, rationale,
                ),
            )
            prediction_id = cursor.lastrowid
            if prediction_id is None:
                raise RuntimeError("Independent prediction could not be saved")
            connection.executemany(
                """
                INSERT INTO runner_predictions (
                    prediction_run_id, horse_number, raw_inverse_win_odds,
                    win_market_share, win_probability, place_probability
                ) VALUES (?, ?, NULL, NULL, ?, ?)
                """,
                [
                    (
                        prediction_id, output["horse_number"],
                        output.get("win_probability"), output.get("place_probability"),
                    )
                    for output in outputs
                ],
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
        prediction_run_id: int | None = None,
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
            odds_snapshot_id = None
            win_odds = None
            place_odds_min = None
            place_odds_max = None
            popularity = None
            if prediction_run_id is not None:
                prediction = connection.execute(
                    "SELECT * FROM prediction_runs WHERE id = ?", (prediction_run_id,),
                ).fetchone()
                if prediction is None:
                    raise LookupError("prediction_not_found")
                if int(prediction["race_id"]) != race_id:
                    raise ValueError("prediction_race_mismatch")
                if (
                    str(prediction["status"]) != "active"
                    or not bool(prediction["official_evaluation_eligible"])
                    or str(prediction["frozen_at"]) > placed_at
                ):
                    raise ValueError("prediction_not_eligible_for_bet")
                odds_snapshot_id = int(prediction["input_snapshot_id"])
                odds = connection.execute(
                    """SELECT * FROM odds_snapshot_runners
                       WHERE snapshot_id = ? AND horse_number = ?""",
                    (odds_snapshot_id, horse_number),
                ).fetchone()
                if odds is None:
                    raise ValueError("prediction_runner_mismatch")
                win_odds = float(odds["win_odds"])
                place_odds_min = float(odds["place_odds_min"])
                place_odds_max = float(odds["place_odds_max"])
                rank = connection.execute(
                    """SELECT 1 + COUNT(*) FROM odds_snapshot_runners
                       WHERE snapshot_id = ? AND win_odds < ?""",
                    (odds_snapshot_id, win_odds),
                ).fetchone()
                popularity = int(rank[0]) if rank is not None else None
            cursor = connection.execute(
                """
                INSERT INTO bets (
                    race_id, horse_number, bet_type, decision_type,
                    amount_yen, placed_at, status, prediction_run_id,
                    odds_snapshot_id, win_odds, place_odds_min, place_odds_max, popularity
                ) VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?)
                """,
                (
                    race_id, horse_number, bet_type, decision_type, amount_yen, placed_at,
                    prediction_run_id, odds_snapshot_id, win_odds,
                    place_odds_min, place_odds_max, popularity,
                ),
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
        source_observation: dict[str, Any] | None = None,
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
            if source_observation is not None:
                source_ids = {
                    str(row[0]) for row in connection.execute(
                        """SELECT card.source_race_id
                           FROM jra_race_registrations AS registration
                           JOIN acquired_race_cards AS card ON card.id = registration.card_id
                           WHERE registration.race_id = ?""",
                        (race_id,),
                    ).fetchall()
                }
                if str(source_observation["source_race_id"]) not in source_ids:
                    raise ValueError("result_race_mismatch")
            runner_numbers = {
                int(row[0]) for row in connection.execute(
                    "SELECT horse_number FROM runners WHERE race_id = ?", (race_id,)
                ).fetchall()
            }
            supplied_numbers = {int(result["horse_number"]) for result in results}
            if supplied_numbers != runner_numbers:
                raise ValueError("result_runner_mismatch")
            changes: list[str] = []
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
                    if source_observation is not None:
                        self._insert_jra_result_observation(
                            connection, int(active["id"]), source_observation,
                        )
                    return int(active["id"])
                if source_observation is None:
                    raise ValueError("result_import_conflict")
                correction_reason = "JRA公開ページの再取得で公式結果の変更を検出"
                stored_by_horse = {int(item[0]): item for item in stored}
                supplied_by_horse = {int(item[0]): item for item in supplied}
                fields = ("finish_position", "status", "win_payout_per_100", "place_payout_per_100")
                for horse_number in sorted(supplied_by_horse):
                    before = stored_by_horse[horse_number]
                    after = supplied_by_horse[horse_number]
                    for index, field_name in enumerate(fields, start=1):
                        if before[index] != after[index]:
                            changes.append(
                                f"{horse_number}番 {field_name}: {before[index]}→{after[index]}"
                            )
            if active is None and correction_reason is not None:
                raise ValueError("result_not_imported")
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
                    supersedes_result_version_id, change_summary_json
                ) VALUES (?, ?, ?, 'active', ?, ?, ?)
                """,
                (race_id, version, received_at, correction_reason, supersedes_id,
                 json.dumps(changes, ensure_ascii=False)),
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
            if source_observation is not None:
                self._insert_jra_result_observation(
                    connection, int(result_version_id), source_observation,
                )
        return int(result_version_id)

    @staticmethod
    def _insert_jra_result_observation(
        connection: sqlite3.Connection, result_version_id: int, observation: dict[str, Any],
    ) -> None:
        connection.execute(
            """INSERT INTO jra_result_observations (
                result_version_id, source_url, source_race_id, received_at, source_updated_at,
                parser_version, response_sha256, validation_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                result_version_id, observation["url"], observation["source_race_id"],
                observation["received_at"], observation["source_updated_at"],
                observation["parser_version"], observation["response_sha256"],
                observation["validation_status"],
            ),
        )

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

    def get_evaluation_dataset(
        self,
    ) -> tuple[list[PredictionEvaluationRow], list[SettlementEvaluationRow]]:
        with sqlite3.connect(self._path) as connection:
            connection.row_factory = sqlite3.Row
            prediction_rows = connection.execute(
                """
                WITH ranked_snapshot_runners AS (
                    SELECT snapshot_runner.*,
                           RANK() OVER (
                               PARTITION BY snapshot_runner.snapshot_id
                               ORDER BY snapshot_runner.win_odds
                           ) AS popularity
                    FROM odds_snapshot_runners AS snapshot_runner
                )
                SELECT prediction.id AS prediction_run_id,
                       prediction.model_identifier, prediction.model_version,
                       prediction.status, prediction.official_evaluation_eligible,
                       prediction.frozen_at, 'win' AS bet_type,
                       race.racecourse, runner_prediction.horse_number,
                       CASE WHEN prediction.prediction_kind = 'independent'
                            THEN runner_prediction.win_probability
                            ELSE runner_prediction.win_market_share END AS predicted_probability,
                       snapshot_runner.win_odds,
                       snapshot_runner.win_odds AS odds_value,
                       snapshot_runner.popularity,
                       CASE WHEN runner_result.finish_position = 1 THEN 1 ELSE 0 END AS outcome
                FROM prediction_runs AS prediction
                JOIN races AS race ON race.id = prediction.race_id
                JOIN runner_predictions AS runner_prediction
                  ON runner_prediction.prediction_run_id = prediction.id
                JOIN ranked_snapshot_runners AS snapshot_runner
                  ON snapshot_runner.snapshot_id = prediction.input_snapshot_id
                 AND snapshot_runner.horse_number = runner_prediction.horse_number
                JOIN result_versions AS result_version
                  ON result_version.race_id = prediction.race_id
                 AND result_version.status = 'active'
                JOIN runner_results AS runner_result
                  ON runner_result.result_version_id = result_version.id
                 AND runner_result.horse_number = runner_prediction.horse_number
                 AND runner_result.status = '確定'
                WHERE prediction.prediction_kind = 'market_baseline'
                   OR runner_prediction.win_probability IS NOT NULL
                UNION ALL
                SELECT prediction.id AS prediction_run_id,
                       prediction.model_identifier, prediction.model_version,
                       prediction.status, prediction.official_evaluation_eligible,
                       prediction.frozen_at, 'place' AS bet_type,
                       race.racecourse, runner_prediction.horse_number,
                       runner_prediction.place_probability AS predicted_probability,
                       snapshot_runner.win_odds,
                       snapshot_runner.place_odds_min AS odds_value,
                       snapshot_runner.popularity,
                       CASE WHEN runner_result.place_payout_per_100 > 0 THEN 1 ELSE 0 END AS outcome
                FROM prediction_runs AS prediction
                JOIN races AS race ON race.id = prediction.race_id
                JOIN runner_predictions AS runner_prediction
                  ON runner_prediction.prediction_run_id = prediction.id
                JOIN ranked_snapshot_runners AS snapshot_runner
                  ON snapshot_runner.snapshot_id = prediction.input_snapshot_id
                 AND snapshot_runner.horse_number = runner_prediction.horse_number
                JOIN result_versions AS result_version
                  ON result_version.race_id = prediction.race_id
                 AND result_version.status = 'active'
                JOIN runner_results AS runner_result
                  ON runner_result.result_version_id = result_version.id
                 AND runner_result.horse_number = runner_prediction.horse_number
                 AND runner_result.status = '確定'
                WHERE prediction.prediction_kind = 'independent'
                  AND runner_prediction.place_probability IS NOT NULL
                ORDER BY 1, 6, 9
                """
            ).fetchall()
            tag_rows = connection.execute(
                """
                SELECT match.prediction_run_id, tag.rule_key, tag.version, match.context_json
                FROM prediction_analysis_tags AS match
                JOIN analysis_tag_versions AS tag ON tag.id = match.rule_version_id
                ORDER BY match.prediction_run_id, tag.rule_key, tag.version
                """
            ).fetchall()
            settlement_rows = connection.execute(
                """
                SELECT settlement.*, bet.decision_type, bet.bet_type,
                       bet.horse_number, race.racecourse,
                       prediction.id AS prediction_run_id,
                       prediction.model_identifier, prediction.model_version,
                       prediction.frozen_at,
                       bet.win_odds, bet.place_odds_min, bet.place_odds_max,
                       CASE WHEN bet.bet_type = 'win' THEN bet.win_odds
                            ELSE bet.place_odds_min END AS odds_value,
                       bet.popularity
                FROM settlements AS settlement
                JOIN bets AS bet ON bet.id = settlement.bet_id
                JOIN races AS race ON race.id = bet.race_id
                LEFT JOIN prediction_runs AS prediction ON prediction.id = bet.prediction_run_id
                WHERE settlement.status = 'active' AND bet.status = 'active'
                ORDER BY settlement.id
                """
            ).fetchall()
        tags_by_prediction: dict[int, list[tuple[str, int, set[int] | None]]] = {}
        for row in tag_rows:
            context = json.loads(str(row["context_json"]))
            context_runners = context.get("runners")
            runner_numbers = None
            if isinstance(context_runners, list):
                runner_numbers = {
                    int(value["horse_number"] if isinstance(value, dict) else value)
                    for value in context_runners
                }
            tags_by_prediction.setdefault(int(row["prediction_run_id"]), []).append(
                (
                    str(row["rule_key"]), int(row["version"]),
                    runner_numbers,
                )
            )
        def tags_for(prediction_id: int, horse_number: int) -> list[tuple[str, int]]:
            return [
                (rule_key, version)
                for rule_key, version, runners in tags_by_prediction.get(prediction_id, [])
                if runners is None or horse_number in runners
            ]
        predictions: list[PredictionEvaluationRow] = []
        for row in prediction_rows:
            prediction_id = int(row["prediction_run_id"])
            prediction_bet_type = str(row["bet_type"])
            if prediction_bet_type not in ("win", "place"):
                raise ValueError(f"Unsupported prediction type: {prediction_bet_type}")
            predictions.append(PredictionEvaluationRow(
                prediction_run_id=prediction_id,
                model_identifier=str(row["model_identifier"]),
                model_version=str(row["model_version"]),
                frozen_at=str(row["frozen_at"]),
                bet_type=cast(Literal["win", "place"], prediction_bet_type),
                racecourse=str(row["racecourse"]),
                odds_value=float(row["odds_value"]),
                popularity=int(row["popularity"]),
                tags=tags_for(prediction_id, int(row["horse_number"])),
                predicted_probability=float(row["predicted_probability"]),
                outcome=int(row["outcome"]),
                eligible=(
                    str(row["status"]) == "active"
                    and bool(row["official_evaluation_eligible"])
                ),
            ))
        settlements: list[SettlementEvaluationRow] = []
        for row in settlement_rows:
            prediction_id = row["prediction_run_id"]
            bet_type = str(row["bet_type"])
            decision_type = str(row["decision_type"])
            if bet_type not in ("win", "place"):
                raise ValueError(f"Unsupported bet type in evaluation data: {bet_type}")
            if decision_type not in ("candidate", "discretionary"):
                raise ValueError(f"Unsupported decision type in evaluation data: {decision_type}")
            settlements.append(SettlementEvaluationRow(
                model_identifier=(
                    None if row["model_identifier"] is None else str(row["model_identifier"])
                ),
                model_version=(
                    None if row["model_version"] is None else str(row["model_version"])
                ),
                frozen_at=None if row["frozen_at"] is None else str(row["frozen_at"]),
                bet_type=cast(Literal["win", "place"], bet_type),
                racecourse=str(row["racecourse"]),
                odds_value=None if row["odds_value"] is None else float(row["odds_value"]),
                popularity=None if row["popularity"] is None else int(row["popularity"]),
                tags=(
                    [] if prediction_id is None else tags_for(
                        int(prediction_id), int(row["horse_number"]),
                    )
                ),
                decision_type=cast(Literal["candidate", "discretionary"], decision_type),
                stake_yen=int(row["stake_yen"]),
                payout_yen=int(row["payout_yen"]),
                refund_yen=int(row["refund_yen"]),
            ))
        return predictions, settlements
