from datetime import datetime, timezone
import hashlib
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile

from fastapi.testclient import TestClient

import app.data_maintenance as data_maintenance_module
from app.data_maintenance import DataMaintenance
from app.main import create_app


SAMPLE_CSV = (Path(__file__).parents[2] / "examples" / "sample-race.csv").read_bytes()


def results_csv() -> bytes:
    return (
        "horse_number,finish_position,status,win_payout_per_100,place_payout_per_100\n"
        "1,1,確定,420,150\n"
        "2,2,確定,0,180\n"
        "3,3,確定,0,0\n"
        "4,4,確定,0,0\n"
        "5,5,確定,0,0\n"
    ).encode("utf-8")


def test_backup_can_be_verified_and_restored_without_losing_audit_or_ledger_state(
    tmp_path: Path,
) -> None:
    app = create_app(
        tmp_path / "data.sqlite3",
        now_provider=lambda: datetime(2026, 8, 28, 12, 34, 56, tzinfo=timezone.utc),
    )

    with TestClient(app) as client:
        race = client.post(
            "/api/races/import", content=SAMPLE_CSV,
            headers={"Content-Type": "text/csv"},
        ).json()
        race_id = race["race_id"]
        snapshot = client.post(f"/api/races/{race_id}/odds-snapshots", json={
            "observed_at": "2026-08-28T12:00:00Z", "source": "test",
            "runners": [{
                "horse_number": runner["horse_number"],
                "win_odds": runner["win_odds"],
                "place_odds_min": runner["place_odds_min"],
                "place_odds_max": runner["place_odds_max"],
            } for runner in race["runners"]],
        }).json()
        assert client.post(f"/api/odds-snapshots/{snapshot['id']}/freeze", json={
            "model_identifier": "market-baseline", "model_version": "1.0",
        }).status_code == 201
        tag = client.get("/api/analysis-tags").json()[0]
        assert client.post(f"/api/analysis-tags/{tag['id']}/state", json={
            "enabled": True, "reason": "バックアップ検証",
        }).status_code == 200
        assert client.post(f"/api/races/{race_id}/bets", json={
            "horse_number": 1, "bet_type": "win",
            "decision_type": "discretionary", "amount_yen": 200,
        }).status_code == 201
        assert client.post(
            f"/api/races/{race_id}/results/import", content=results_csv(),
            headers={"Content-Type": "text/csv"},
        ).status_code == 201
        second_csv = SAMPLE_CSV.replace(b",11,", b",12,")
        second_race = client.post(
            "/api/races/import", content=second_csv,
            headers={"Content-Type": "text/csv"},
        )
        assert second_race.status_code == 201

        expected_predictions = client.get(f"/api/races/{race_id}/predictions").json()
        expected_audit = client.get(f"/api/analysis-tags/{tag['rule_key']}/audit").json()
        expected_ledger = client.get(f"/api/races/{race_id}/ledger").json()
        created = client.post("/api/data/backups")

        assert created.status_code == 201, created.text
        backup = created.json()
        assert backup["kind"] == "manual"
        assert backup["verified"] is True
        assert backup["size_bytes"] > 0
        assert client.post(f"/api/data/backups/{backup['id']}/verify").json() == {
            **backup, "verified": True,
        }

        third_csv = SAMPLE_CSV.replace(b",11,", b",13,")
        third = client.post(
            "/api/races/import", content=third_csv,
            headers={"Content-Type": "text/csv"},
        )
        assert third.status_code == 201
        third_race_id = third.json()["race_id"]

        restored = client.post(f"/api/data/backups/{backup['id']}/restore")
        assert restored.status_code == 200, restored.text
        assert restored.json()["restored_backup_id"] == backup["id"]
        assert restored.json()["safety_backup"]["kind"] == "pre_restore"
        assert client.get(f"/api/races/{third_race_id}").status_code == 404
        assert client.get(f"/api/races/{race_id}/predictions").json() == expected_predictions
        assert client.get(f"/api/analysis-tags/{tag['rule_key']}/audit").json() == expected_audit
        assert client.get(f"/api/races/{race_id}/ledger").json() == expected_ledger
        assert {item["id"] for item in client.get("/api/data/backups").json()} == {
            backup["id"], restored.json()["safety_backup"]["id"],
        }

    restored_app = create_app(
        tmp_path / "restored.sqlite3",
        now_provider=lambda: datetime(2026, 8, 28, 12, 40, tzinfo=timezone.utc),
    )
    with TestClient(restored_app) as restored_client:
        assert restored_client.post(f"/api/data/backups/{backup['id']}/restore").status_code == 200
        restored_export = restored_client.get("/api/data/export?format=json").json()
        assert len(restored_export["tables"]["races"]) == 2
        assert len(restored_export["tables"]["runners"]) == 10
        assert restored_client.get(f"/api/races/{race_id}/predictions").json() == expected_predictions
        assert restored_client.get(f"/api/analysis-tags/{tag['rule_key']}/audit").json() == expected_audit
        assert restored_client.get(f"/api/races/{race_id}/ledger").json() == expected_ledger


def test_json_and_csv_exports_include_user_data_and_audit_history(tmp_path: Path) -> None:
    app = create_app(
        tmp_path / "data.sqlite3",
        now_provider=lambda: datetime(2026, 8, 28, 12, 34, 56, tzinfo=timezone.utc),
    )

    with TestClient(app) as client:
        race = client.post(
            "/api/races/import", content=SAMPLE_CSV,
            headers={"Content-Type": "text/csv"},
        ).json()
        tag = client.get("/api/analysis-tags").json()[0]
        client.post(f"/api/analysis-tags/{tag['id']}/state", json={
            "enabled": True, "reason": "出力監査",
        })

        json_export = client.get("/api/data/export?format=json")
        csv_export = client.get("/api/data/export?format=csv")

    assert json_export.status_code == 200
    assert json_export.headers["content-type"] == "application/json"
    assert "attachment;" in json_export.headers["content-disposition"]
    payload = json_export.json()
    assert payload["schema_version"] == "19"
    assert payload["exported_at"] == "2026-08-28T12:34:56Z"
    assert payload["tables"]["races"][0]["id"] == race["race_id"]
    assert payload["tables"]["runners"][0]["horse_name"] == "アカツキ"
    assert payload["tables"]["analysis_tag_audit_events"][0]["reason"] == "出力監査"
    assert {"meeting_week_runs", "meeting_week_races", "meeting_week_observations",
            "personal_runner_opinion_revisions", "personal_runner_reflections",
            "personal_operation_requests"} <= set(payload["tables"])

    assert csv_export.status_code == 200
    assert csv_export.headers["content-type"] == "application/zip"
    assert "attachment;" in csv_export.headers["content-disposition"]
    with ZipFile(BytesIO(csv_export.content)) as archive:
        assert {"races.csv", "runners.csv", "analysis_tag_audit_events.csv",
                "personal_runner_opinion_revisions.csv", "personal_runner_reflections.csv",
                "personal_operation_requests.csv"} <= set(archive.namelist())
        races_csv = archive.read("races.csv").decode("utf-8")
        audit_csv = archive.read("analysis_tag_audit_events.csv").decode("utf-8")
    assert "東京" in races_csv
    assert "出力監査" in audit_csv


def test_corrupt_or_unknown_backup_is_never_restored(tmp_path: Path) -> None:
    app = create_app(tmp_path / "data.sqlite3")

    with TestClient(app) as client:
        race_id = client.post(
            "/api/races/import", content=SAMPLE_CSV,
            headers={"Content-Type": "text/csv"},
        ).json()["race_id"]
        backup = client.post("/api/data/backups").json()
        (tmp_path / "backups" / f"{backup['id']}.sqlite3").write_bytes(b"not a sqlite database")

        listed = client.get("/api/data/backups").json()
        rejected = client.post(f"/api/data/backups/{backup['id']}/restore")
        unknown = client.post("/api/data/backups/not-a-managed-backup/restore")

        assert listed[0]["verified"] is False
        assert rejected.status_code == 422
        assert rejected.json()["detail"]["code"] == "backup_invalid"
        assert unknown.status_code == 404
        assert client.get(f"/api/races/{race_id}").status_code == 200


def test_restore_collision_preserves_preexisting_stage_marker_and_live_data(
    tmp_path: Path, monkeypatch,
) -> None:
    database_path = tmp_path / "data.sqlite3"
    app = create_app(database_path)
    with TestClient(app) as client:
        client.post("/api/races/import", content=SAMPLE_CSV, headers={"Content-Type": "text/csv"})
        backup = client.post("/api/data/backups").json()
        source_path = tmp_path / "backups" / f"{backup['id']}.sqlite3"
        source_digest = hashlib.sha256(source_path.read_bytes()).hexdigest()
        live_digest = DataMaintenance._database_digest(database_path)

        collision_hex = "aaaaaaaaaaaa4aaa8aaaaaaaaaaaaaaa"
        marker_path = tmp_path / f".restore-stage-{collision_hex}.sqlite3"
        marker_bytes = b"unrelated synthetic pre-existing stage-path data"
        marker_path.write_bytes(marker_bytes)
        marker_digest = hashlib.sha256(marker_bytes).hexdigest()
        monkeypatch.setattr(data_maintenance_module, "uuid4", lambda: SimpleNamespace(hex=collision_hex))

        response = client.post(f"/api/data/backups/{backup['id']}/restore")

        assert response.status_code == 500
        assert response.json()["detail"]["code"] == "restore_failed"
        assert marker_path.read_bytes() == marker_bytes
        assert hashlib.sha256(marker_path.read_bytes()).hexdigest() == marker_digest
        assert hashlib.sha256(source_path.read_bytes()).hexdigest() == source_digest
        assert DataMaintenance._database_digest(database_path) == live_digest
        backup_ids = [item["id"] for item in client.get("/api/data/backups").json()]
        assert backup_ids == [backup["id"]]
