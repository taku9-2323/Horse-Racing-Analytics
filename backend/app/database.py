from pathlib import Path
import sqlite3
from collections.abc import Sequence
from typing import Any


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

    def check(self) -> None:
        with sqlite3.connect(self._path) as connection:
            row = connection.execute(
                "SELECT value FROM application_metadata WHERE key = 'schema_version'"
            ).fetchone()

        if row != ("2",):
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
