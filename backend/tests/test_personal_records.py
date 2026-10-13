import sqlite3
from hashlib import sha256
from pathlib import Path
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.main import create_app


SAMPLE_CSV = (Path(__file__).parents[2] / "examples" / "sample-race.csv").read_bytes()
SYSTEM_TABLES = (
    "prediction_runs", "rule_judgement_runs", "bets", "odds_snapshots",
    "odds_snapshot_runners", "result_versions", "runner_results", "settlements",
    "jra_result_observations",
)


def import_race(client: TestClient, csv: bytes = SAMPLE_CSV) -> dict[str, object]:
    response = client.post("/api/races/import", content=csv, headers={"Content-Type": "text/csv"})
    assert response.status_code == 201, response.text
    return response.json()


def operation_id(number: int) -> str:
    return f"00000000-0000-4000-8000-{number:012d}"


def system_table_snapshot(database_path: Path) -> tuple[tuple[str, tuple[tuple[object, ...], ...]], ...]:
    with sqlite3.connect(database_path) as connection:
        snapshot = []
        for table in SYSTEM_TABLES:
            rows = connection.execute(f'SELECT * FROM "{table}"').fetchall()
            snapshot.append((table, tuple(sorted(rows, key=repr))))
        return tuple(snapshot)


def test_personal_opinions_append_clear_and_reflections_are_separate(tmp_path: Path) -> None:
    database_path = tmp_path / "data.sqlite3"
    app = create_app(database_path)

    with TestClient(app) as client:
        race = import_race(client)
        race_id = race["race_id"]
        blank = client.get(f"/api/races/{race_id}/personal-runner-records")
        assert blank.status_code == 200, blank.text
        runner = blank.json()["runners"][0]
        assert runner["current_revision"] is None
        assert runner["revisions"] == []
        assert runner["reflections"] == []

        protected_before = system_table_snapshot(database_path)
        opinion_path = f"/api/races/{race_id}/runners/1/personal-opinion-revisions"
        clear_payload = {"operation_id": operation_id(1), "mark": None, "memo": None, "skip_reason": None}
        assert client.post(opinion_path, json=clear_payload).status_code == 422
        first = client.post(opinion_path, json={
            "operation_id": operation_id(2), "mark": "◎", "memo": "軸候補😀", "skip_reason": None,
        })
        assert first.status_code == 201, first.text
        first_record = first.json()
        assert first_record["recorded_at"]

        second = client.post(opinion_path, json={
            "operation_id": operation_id(3), "mark": "○", "memo": "考え直した", "skip_reason": None,
        })
        assert second.status_code == 201, second.text
        clear_payload["operation_id"] = operation_id(4)
        assert client.post(opinion_path, json=clear_payload).status_code == 201

        records = client.get(f"/api/races/{race_id}/personal-runner-records").json()
        current = records["runners"][0]
        assert current["current_revision"]["memo"] is None
        assert len(current["revisions"]) == 3
        assert [item["memo"] for item in current["revisions"]] == ["軸候補😀", "考え直した", None]
        assert current["revisions"][0]["recorded_at"] == first_record["recorded_at"]

        reflection_path = f"/api/races/{race_id}/runners/1/personal-reflections"
        unavailable = client.post(reflection_path, json={"operation_id": operation_id(5), "reflection": "結果未保存でも追加"})
        assert unavailable.status_code == 409
        results = (
            "horse_number,finish_position,status,win_payout_per_100,place_payout_per_100\n"
            "1,1,確定,420,150\n2,2,確定,0,180\n3,3,確定,0,0\n"
            "4,4,確定,0,0\n5,5,確定,0,0\n"
        ).encode()
        saved_results = client.post(
            f"/api/races/{race_id}/results/import", content=results,
            headers={"Content-Type": "text/csv"},
        )
        assert saved_results.status_code == 201, saved_results.text
        protected_before = system_table_snapshot(database_path)
        too_long_reflection = client.post(reflection_path, json={"operation_id": operation_id(6), "reflection": "😀" * 2001})
        assert too_long_reflection.status_code == 422
        reflection = client.post(reflection_path, json={"operation_id": operation_id(7), "reflection": "😀" * 2000})
        assert reflection.status_code == 201, reflection.text
        assert len(reflection.json()["reflection"]) == 2000
        assert system_table_snapshot(database_path) == protected_before
        assert "result_version_id" not in reflection.json()
        reread = client.get(f"/api/races/{race_id}/personal-runner-records").json()["runners"][0]
        assert len(reread["revisions"]) == 3
        assert [item["reflection"] for item in reread["reflections"]] == ["😀" * 2000]

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT value FROM application_metadata WHERE key='schema_version'").fetchone() == ("19",)
        assert connection.execute("SELECT COUNT(*) FROM personal_runner_opinion_revisions").fetchone() == (3,)


def test_personal_record_lengths_marks_and_race_runner_boundaries(tmp_path: Path) -> None:
    app = create_app(tmp_path / "data.sqlite3")
    with TestClient(app) as client:
        first_race = import_race(client)
        second_race = import_race(client, SAMPLE_CSV.replace(b"2026-08-30", b"2026-09-06"))
        first_id = first_race["race_id"]
        second_id = second_race["race_id"]
        records_path = f"/api/races/{first_id}/personal-runner-records"
        protected_before = system_table_snapshot(tmp_path / "data.sqlite3")
        before = client.get(records_path).json()
        assert system_table_snapshot(tmp_path / "data.sqlite3") == protected_before
        invalid_mark = client.post(
            f"/api/races/{first_id}/runners/1/personal-opinion-revisions",
            json={"operation_id": operation_id(8), "mark": "本命", "memo": None, "skip_reason": None},
        )
        assert invalid_mark.status_code == 422
        forged_time = client.post(
            f"/api/races/{first_id}/runners/1/personal-opinion-revisions",
            json={"operation_id": operation_id(9), "mark": "消", "memo": "", "skip_reason": None,
                  "recorded_at": "2000-01-01T00:00:00Z"},
        )
        assert forged_time.status_code == 422
        too_long = client.post(
            f"/api/races/{first_id}/runners/1/personal-opinion-revisions",
            json={"operation_id": operation_id(10), "mark": None, "memo": "😀" * 2001, "skip_reason": None},
        )
        assert too_long.status_code == 422
        valid = client.post(
            f"/api/races/{first_id}/runners/1/personal-opinion-revisions",
            json={"operation_id": operation_id(11), "mark": "消", "memo": "😀" * 2000, "skip_reason": None},
        )
        assert valid.status_code == 201, valid.text
        invalid_runner = client.post(
            f"/api/races/{first_id}/runners/99/personal-opinion-revisions",
            json={"operation_id": operation_id(12), "mark": None, "memo": "no runner", "skip_reason": None},
        )
        assert invalid_runner.status_code == 404
        missing_race = client.get("/api/races/999999/personal-runner-records")
        assert missing_race.status_code == 404
        other_race = client.get(f"/api/races/{second_id}/personal-runner-records").json()
        assert other_race["runners"][0]["current_revision"] is None
        after = client.get(records_path).json()
        assert after["runners"][0]["current_revision"]["mark"] == "消"
        assert len(after["runners"][0]["current_revision"]["memo"]) == 2000
        assert len(after["runners"][1]["revisions"]) == 0
        assert before["runners"][0]["revisions"] == []
        assert system_table_snapshot(tmp_path / "data.sqlite3") == protected_before


def test_history_uses_recorded_time_while_current_revision_uses_append_id(tmp_path: Path) -> None:
    clock = [datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)]
    app = create_app(tmp_path / "data.sqlite3", now_provider=lambda: clock[0])
    with TestClient(app) as client:
        race_id = import_race(client)["race_id"]
        endpoint = f"/api/races/{race_id}/runners/1/personal-opinion-revisions"
        later_time = client.post(endpoint, json={
            "operation_id": operation_id(20), "mark": "◎", "memo": "append first", "skip_reason": None,
        })
        assert later_time.status_code == 201, later_time.text
        clock[0] = datetime(2026, 9, 5, 9, 0, tzinfo=timezone.utc)
        earlier_time = client.post(endpoint, json={
            "operation_id": operation_id(21), "mark": "○", "memo": "append second", "skip_reason": None,
        })
        assert earlier_time.status_code == 201, earlier_time.text

        record = client.get(f"/api/races/{race_id}/personal-runner-records").json()["runners"][0]
        assert [item["id"] for item in record["revisions"]] == [earlier_time.json()["id"], later_time.json()["id"]]
        assert record["current_revision"]["id"] == earlier_time.json()["id"]
        assert record["current_revision"]["memo"] == "append second"


def test_operation_id_retry_returns_existing_append_and_conflict_does_not_duplicate(tmp_path: Path) -> None:
    database_path = tmp_path / "data.sqlite3"
    app = create_app(database_path)
    with TestClient(app) as client:
        race_id = import_race(client)["race_id"]
        endpoint = f"/api/races/{race_id}/runners/1/personal-opinion-revisions"
        payload = {
            "operation_id": operation_id(30), "mark": "◎", "memo": None, "skip_reason": None,
        }
        first = client.post(endpoint, json=payload)
        retry = client.post(endpoint, json=payload)
        assert first.status_code == 201, first.text
        assert retry.status_code == 201, retry.text
        assert retry.json() == first.json()

        changed_payload = {**payload, "memo": "different content"}
        conflict = client.post(endpoint, json=changed_payload)
        assert conflict.status_code == 409
        assert conflict.json()["detail"]["code"] == "personal_operation_conflict"
        records = client.get(f"/api/races/{race_id}/personal-runner-records").json()["runners"][0]
        assert len(records["revisions"]) == 1
        assert records["current_revision"]["memo"] is None
        changed_nullness = {**payload, "memo": ""}
        assert client.post(endpoint, json=changed_nullness).status_code == 409
        changed_target = f"/api/races/{race_id}/runners/2/personal-opinion-revisions"
        assert client.post(changed_target, json=payload).status_code == 409
        assert client.post(
            f"/api/races/{race_id}/runners/1/personal-reflections",
            json={"operation_id": payload["operation_id"], "reflection": "same id, other operation"},
        ).status_code == 409
        other_race_id = import_race(client, SAMPLE_CSV.replace(b"2026-08-30", b"2026-09-06"))["race_id"]
        assert client.post(
            f"/api/races/{other_race_id}/runners/1/personal-opinion-revisions", json=payload,
        ).status_code == 409
        with sqlite3.connect(database_path) as connection:
            assert connection.execute("SELECT COUNT(*) FROM personal_operation_requests").fetchone() == (1,)
        unicode_payload = {
            "operation_id": operation_id(31), "mark": "◎", "memo": "é", "skip_reason": None,
        }
        assert client.post(endpoint, json=unicode_payload).status_code == 201
        normalized_variant = {**unicode_payload, "memo": "e\u0301"}
        assert client.post(endpoint, json=normalized_variant).status_code == 409
        with sqlite3.connect(database_path) as connection:
            assert connection.execute("SELECT COUNT(*) FROM personal_operation_requests").fetchone() == (2,)


def test_reflection_retry_replays_after_active_result_gate_changes(tmp_path: Path) -> None:
    database_path = tmp_path / "data.sqlite3"
    app = create_app(database_path)
    with TestClient(app) as client:
        race_id = import_race(client)["race_id"]
        results = (
            "horse_number,finish_position,status,win_payout_per_100,place_payout_per_100\n"
            "1,1,確定,420,150\n2,2,確定,0,180\n3,3,確定,0,0\n"
            "4,4,確定,0,0\n5,5,確定,0,0\n"
        ).encode()
        assert client.post(
            f"/api/races/{race_id}/results/import", content=results,
            headers={"Content-Type": "text/csv"},
        ).status_code == 201
        endpoint = f"/api/races/{race_id}/runners/1/personal-reflections"
        payload = {"operation_id": operation_id(40), "reflection": "response was lost"}
        first = client.post(endpoint, json=payload)
        assert first.status_code == 201, first.text
        with sqlite3.connect(database_path) as connection:
            connection.execute("UPDATE result_versions SET status='superseded' WHERE race_id=?", (race_id,))
        retry = client.post(endpoint, json=payload)
        assert retry.status_code == 201, retry.text
        assert retry.json() == first.json()
        new_operation = client.post(endpoint, json={
            "operation_id": operation_id(41), "reflection": "should be gated",
        })
        assert new_operation.status_code == 409
        with sqlite3.connect(database_path) as connection:
            assert connection.execute("SELECT COUNT(*) FROM personal_runner_reflections").fetchone() == (1,)


def _make_v18_backup(client: TestClient, database_path: Path) -> tuple[str, Path, str]:
    backup = client.post("/api/data/backups").json()
    source_path = database_path.parent / "backups" / f"{backup['id']}.sqlite3"
    with sqlite3.connect(source_path) as connection:
        connection.execute("DROP TABLE personal_runner_opinion_revisions")
        connection.execute("DROP TABLE personal_runner_reflections")
        connection.execute("DROP TABLE personal_operation_requests")
        connection.execute("DROP INDEX IF EXISTS personal_opinions_by_runner")
        connection.execute("DROP INDEX IF EXISTS personal_reflections_by_runner")
        connection.execute("UPDATE application_metadata SET value='18' WHERE key='schema_version'")
    digest = sha256(source_path.read_bytes()).hexdigest()
    return backup["id"], source_path, digest


def test_schema18_backup_restores_through_staging_and_keeps_original_bytes(tmp_path: Path) -> None:
    database_path = tmp_path / "data.sqlite3"
    app = create_app(database_path)
    with TestClient(app) as client:
        race = import_race(client)
        race_id = race["race_id"]
        backup_id, source_path, source_digest = _make_v18_backup(client, database_path)
        assert client.post(f"/api/data/backups/{backup_id}/verify").status_code == 200
        added = client.post(
            f"/api/races/{race_id}/runners/1/personal-opinion-revisions",
            json={"operation_id": operation_id(13), "mark": "◎", "memo": "live only", "skip_reason": None},
        )
        assert added.status_code == 201

        restored = client.post(f"/api/data/backups/{backup_id}/restore")
        assert restored.status_code == 200, restored.text
        assert sha256(source_path.read_bytes()).hexdigest() == source_digest
        empty = client.get(f"/api/races/{race_id}/personal-runner-records").json()
        assert empty["runners"][0]["revisions"] == []
        assert restored.json()["safety_backup"]["verified"] is True
        assert client.get("/api/data/export?format=json").json()["schema_version"] == "19"
        safety_id = restored.json()["safety_backup"]["id"]
        restored_live = client.post(f"/api/data/backups/{safety_id}/restore")
        assert restored_live.status_code == 200, restored_live.text
        retained = client.get(f"/api/races/{race_id}/personal-runner-records").json()
        assert retained["runners"][0]["revisions"][0]["memo"] == "live only"


def test_v18_restore_migration_failure_leaves_live_database_and_original_backup_unchanged(
    tmp_path: Path, monkeypatch,
) -> None:
    from app.database import SqliteDatabase
    from app.data_maintenance import DataMaintenance

    database_path = tmp_path / "data.sqlite3"
    app = create_app(database_path)
    with TestClient(app) as client:
        race = import_race(client)
        backup_id, source_path, source_digest = _make_v18_backup(client, database_path)
        before = DataMaintenance._database_digest(database_path)
        original_initialize = SqliteDatabase.initialize

        def fail_staging_migration(database: SqliteDatabase) -> None:
            if database._path.name.startswith(".restore-stage-"):
                raise sqlite3.OperationalError("synthetic migration failure")
            original_initialize(database)

        monkeypatch.setattr(SqliteDatabase, "initialize", fail_staging_migration)
        failed = client.post(f"/api/data/backups/{backup_id}/restore")
        assert failed.status_code == 422
        assert failed.json()["detail"]["code"] == "backup_invalid"
        assert DataMaintenance._database_digest(database_path) == before
        assert sha256(source_path.read_bytes()).hexdigest() == source_digest
        assert client.get(f"/api/races/{race['race_id']}").status_code == 200
        backup_list = client.get("/api/data/backups").json()
        assert len(backup_list) == 1
        assert backup_list[0]["id"] == backup_id
        assert backup_list[0]["verified"] is True


def test_schema19_backup_missing_personal_table_shape_is_rejected_before_restore(tmp_path: Path) -> None:
    from app.data_maintenance import DataMaintenance

    database_path = tmp_path / "data.sqlite3"
    app = create_app(database_path)
    with TestClient(app) as client:
        race = import_race(client)
        opinion = client.post(
            f"/api/races/{race['race_id']}/runners/1/personal-opinion-revisions",
            json={"operation_id": operation_id(70), "mark": "◎", "memo": "synthetic history", "skip_reason": None},
        )
        assert opinion.status_code == 201, opinion.text
        live_digest = DataMaintenance._database_digest(database_path)
        damaged_backups: list[tuple[str, Path, str]] = []

        for damage in (
            "missing_opinion_table", "missing_reflection_table", "missing_operation_table",
            "missing_reflection_column", "missing_operation_unique", "orphan_operation_mapping",
            "nullable_operation_id_column", "null_operation_id", "empty_operation_id", "invalid_operation_id",
            "uppercase_operation_id", "urn_operation_id", "brace_operation_id",
        ):
            backup = client.post("/api/data/backups").json()
            backup_path = database_path.parent / "backups" / f"{backup['id']}.sqlite3"
            with sqlite3.connect(backup_path) as connection:
                if damage == "missing_opinion_table":
                    connection.execute("DROP TABLE personal_runner_opinion_revisions")
                elif damage == "missing_reflection_table":
                    connection.execute("DROP TABLE personal_runner_reflections")
                elif damage == "missing_operation_table":
                    connection.execute("DROP TABLE personal_operation_requests")
                elif damage == "missing_reflection_column":
                    connection.execute("ALTER TABLE personal_runner_reflections DROP COLUMN recorded_at")
                elif damage == "orphan_operation_mapping":
                    connection.execute("UPDATE personal_operation_requests SET record_id = 999")
                elif damage in {
                    "nullable_operation_id_column", "null_operation_id",
                    "empty_operation_id", "invalid_operation_id", "uppercase_operation_id",
                    "urn_operation_id", "brace_operation_id",
                }:
                    if damage in {"nullable_operation_id_column", "null_operation_id"}:
                        connection.execute(
                            """CREATE TABLE replacement_personal_operation_requests (
                                operation_id TEXT PRIMARY KEY,
                                operation_type TEXT NOT NULL CHECK (operation_type IN ('opinion', 'reflection')),
                                race_id INTEGER NOT NULL,
                                horse_number INTEGER NOT NULL CHECK (horse_number > 0),
                                payload_sha256 TEXT NOT NULL,
                                record_id INTEGER NOT NULL,
                                created_at TEXT NOT NULL,
                                UNIQUE (operation_type, record_id),
                                FOREIGN KEY (race_id, horse_number) REFERENCES runners(race_id, horse_number)
                            )""",
                        )
                        connection.execute(
                            """INSERT INTO replacement_personal_operation_requests
                                SELECT * FROM personal_operation_requests""",
                        )
                        connection.execute("DROP TABLE personal_operation_requests")
                        connection.execute(
                            "ALTER TABLE replacement_personal_operation_requests RENAME TO personal_operation_requests",
                        )
                    if damage == "null_operation_id":
                        connection.execute("UPDATE personal_operation_requests SET operation_id = NULL")
                    elif damage == "empty_operation_id":
                        connection.execute("UPDATE personal_operation_requests SET operation_id = ''")
                    elif damage == "invalid_operation_id":
                        connection.execute("UPDATE personal_operation_requests SET operation_id = 'not-a-uuid'")
                    elif damage == "uppercase_operation_id":
                        connection.execute(
                            "UPDATE personal_operation_requests SET operation_id = 'A0000000-0000-4A00-8000-000000000070'",
                        )
                    elif damage == "urn_operation_id":
                        connection.execute(
                            "UPDATE personal_operation_requests SET operation_id = 'urn:uuid:a0000000-0000-4a00-8000-000000000070'",
                        )
                    elif damage == "brace_operation_id":
                        connection.execute(
                            "UPDATE personal_operation_requests SET operation_id = '{a0000000-0000-4a00-8000-000000000070}'",
                        )
                else:
                    connection.execute(
                        """CREATE TABLE replacement_personal_operation_requests (
                            operation_id TEXT PRIMARY KEY,
                            operation_type TEXT NOT NULL CHECK (operation_type IN ('opinion', 'reflection')),
                            race_id INTEGER NOT NULL,
                            horse_number INTEGER NOT NULL CHECK (horse_number > 0),
                            payload_sha256 TEXT NOT NULL,
                            record_id INTEGER NOT NULL,
                            created_at TEXT NOT NULL,
                            FOREIGN KEY (race_id, horse_number) REFERENCES runners(race_id, horse_number)
                        )""",
                    )
                    connection.execute(
                        """INSERT INTO replacement_personal_operation_requests
                            SELECT * FROM personal_operation_requests""",
                    )
                    connection.execute("DROP TABLE personal_operation_requests")
                    connection.execute(
                        "ALTER TABLE replacement_personal_operation_requests RENAME TO personal_operation_requests",
                    )
            damaged_backups.append((backup["id"], backup_path, sha256(backup_path.read_bytes()).hexdigest()))

        for backup_id, backup_path, source_hash in damaged_backups:
            verified = client.post(f"/api/data/backups/{backup_id}/verify")
            rejected = client.post(f"/api/data/backups/{backup_id}/restore")
            assert verified.status_code == 422
            assert rejected.status_code == 422
            assert rejected.json()["detail"]["code"] == "backup_invalid"
            assert sha256(backup_path.read_bytes()).hexdigest() == source_hash

        assert DataMaintenance._database_digest(database_path) == live_digest
        backups = client.get("/api/data/backups").json()
        assert {item["id"] for item in backups} == {item[0] for item in damaged_backups}
        assert all(item["verified"] is False for item in backups)
        assert all(item["kind"] == "manual" for item in backups)
        assert client.get(f"/api/races/{race['race_id']}").status_code == 200


def test_restore_apply_failure_is_reported_only_after_verified_rollback(tmp_path: Path, monkeypatch) -> None:
    from app.data_maintenance import DataMaintenance

    database_path = tmp_path / "data.sqlite3"
    app = create_app(database_path)
    with TestClient(app) as client:
        race = import_race(client)
        backup = client.post("/api/data/backups").json()
        before = DataMaintenance._database_digest(database_path)
        original_copy = DataMaintenance._copy_database
        failed_once = False

        def fail_after_staging_copy(source_path: Path, destination_path: Path) -> None:
            nonlocal failed_once
            original_copy(source_path, destination_path)
            if source_path.name.startswith(".restore-stage-") and destination_path == database_path and not failed_once:
                failed_once = True
                raise sqlite3.OperationalError("synthetic apply verification failure")

        monkeypatch.setattr(DataMaintenance, "_copy_database", staticmethod(fail_after_staging_copy))
        failed = client.post(f"/api/data/backups/{backup['id']}/restore")
        assert failed.status_code == 500
        assert failed.json()["detail"]["code"] == "restore_failed"
        assert "現在データが保たれていることを確認しました" in failed.json()["detail"]["message"]
        assert DataMaintenance._database_digest(database_path) == before
        backups = client.get("/api/data/backups").json()
        assert {item["kind"] for item in backups} == {"manual", "pre_restore"}
        assert client.get(f"/api/races/{race['race_id']}").status_code == 200


def test_rollback_failure_does_not_claim_live_data_is_unchanged(tmp_path: Path, monkeypatch) -> None:
    from app.data_maintenance import DataMaintenance

    database_path = tmp_path / "data.sqlite3"
    app = create_app(database_path)
    with TestClient(app) as client:
        race = import_race(client)
        backup = client.post("/api/data/backups").json()
        original_copy = DataMaintenance._copy_database

        def fail_apply_and_rollback(source_path: Path, destination_path: Path) -> None:
            if destination_path == database_path and source_path.name.startswith(".restore-stage-"):
                original_copy(source_path, destination_path)
                raise sqlite3.OperationalError("synthetic post-apply failure")
            if destination_path == database_path and source_path.name.startswith("pre-restore-"):
                raise sqlite3.OperationalError("synthetic rollback failure")
            original_copy(source_path, destination_path)

        monkeypatch.setattr(DataMaintenance, "_copy_database", staticmethod(fail_apply_and_rollback))
        failed = client.post(f"/api/data/backups/{backup['id']}/restore")
        assert failed.status_code == 500
        message = failed.json()["detail"]["message"]
        assert "戻ったことを確認できません" in message
        assert "変更されていません" not in message
        backups = client.get("/api/data/backups").json()
        assert {item["kind"] for item in backups} == {"manual", "pre_restore"}
