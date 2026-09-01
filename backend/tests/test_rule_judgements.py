from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app


CSV = (Path(__file__).parents[2] / "examples" / "sample-race.csv").read_bytes()


def setup_race(client: TestClient) -> tuple[int, int]:
    race_id = client.post("/api/races/import", content=CSV, headers={"Content-Type": "text/csv"}).json()["race_id"]
    analysis = client.get(f"/api/races/{race_id}").json()
    snapshot = client.post(f"/api/races/{race_id}/odds-snapshots", json={
        "observed_at": "2026-08-30T05:00:00Z", "source": "test",
        "runners": [{
            "horse_number": runner["horse_number"], "win_odds": runner["win_odds"],
            "place_odds_min": runner["place_odds_min"], "place_odds_max": runner["place_odds_max"],
        } for runner in analysis["runners"]],
    }).json()
    return race_id, snapshot["id"]


def test_rule_version_and_fixed_explainable_judgements(tmp_path: Path) -> None:
    app = create_app(tmp_path / "rules.sqlite3", now_provider=lambda: datetime(2026, 8, 30, 5, 10, tzinfo=timezone.utc))
    with TestClient(app) as client:
        rule = client.get("/api/rule-versions").json()[0]
        race_id, snapshot_id = setup_race(client)
        frozen = client.post(f"/api/races/{race_id}/rule-judgements/freeze", json={
            "snapshot_id": snapshot_id, "rule_version_id": rule["id"], "judgement_as_of": "2026-08-30T05:00:00Z",
        })
        history = client.get(f"/api/races/{race_id}/rule-judgements")
        immutable = client.put(f"/api/rule-judgements/{frozen.json()['id']}", json={})

    assert rule["vocabulary"] == ["注目", "見送り", "判定不能"]
    assert "assigned_weight" in rule["allowed_fields"]
    assert frozen.status_code == 201
    payload = frozen.json()
    assert [runner["judgement"] for runner in payload["runners"]] == ["注目", "注目", "見送り", "見送り", "見送り"]
    assert payload["runners"][0]["satisfied_conditions"] == ["市場順位が2位以内", "単勝オッズが10.0以下"]
    assert payload["official_pre_race_eligible"] is True
    assert "期待値" in payload["disclaimer"]
    assert history.json() == [payload]
    assert immutable.status_code == 409


def test_future_snapshot_is_rejected_and_correction_preserves_history(tmp_path: Path) -> None:
    app = create_app(tmp_path / "rules-history.sqlite3", now_provider=lambda: datetime(2026, 8, 30, 5, 20, tzinfo=timezone.utc))
    with TestClient(app) as client:
        rule_id = client.get("/api/rule-versions").json()[0]["id"]
        race_id, snapshot_id = setup_race(client)
        leaked = client.post(f"/api/races/{race_id}/rule-judgements/freeze", json={
            "snapshot_id": snapshot_id, "rule_version_id": rule_id, "judgement_as_of": "2026-08-30T04:59:00Z",
        })
        first = client.post(f"/api/races/{race_id}/rule-judgements/freeze", json={
            "snapshot_id": snapshot_id, "rule_version_id": rule_id, "judgement_as_of": "2026-08-30T05:00:00Z",
        }).json()
        corrected = client.post(f"/api/rule-judgements/{first['id']}/correct", json={
            "snapshot_id": snapshot_id, "rule_version_id": rule_id, "judgement_as_of": "2026-08-30T05:00:00Z", "reason": "表示理由を再固定",
        })
        history = client.get(f"/api/races/{race_id}/rule-judgements").json()

    assert leaked.status_code == 422
    assert leaked.json()["detail"]["code"] == "future_snapshot_not_allowed"
    assert corrected.status_code == 201
    assert history[0]["status"] == "invalidated"
    assert history[0]["invalidation_reason"] == "表示理由を再固定"
    assert history[1]["replaces_judgement_id"] == first["id"]
    assert history[0]["runners"] == history[1]["runners"]


def test_inactive_runner_is_frozen_as_unavailable_with_reason(tmp_path: Path) -> None:
    app = create_app(tmp_path / "rules-unavailable.sqlite3", now_provider=lambda: datetime(2026, 8, 30, 5, 10, tzinfo=timezone.utc))
    csv_with_scratch = CSV.replace("出走".encode(), "取消".encode(), 1)
    with TestClient(app) as client:
        rule_id = client.get("/api/rule-versions").json()[0]["id"]
        race_id = client.post("/api/races/import", content=csv_with_scratch, headers={"Content-Type": "text/csv"}).json()["race_id"]
        analysis = client.get(f"/api/races/{race_id}").json()
        snapshot_id = client.post(f"/api/races/{race_id}/odds-snapshots", json={
            "observed_at": "2026-08-30T05:00:00Z", "source": "test",
            "runners": [{"horse_number": runner["horse_number"], "win_odds": runner["win_odds"],
                         "place_odds_min": runner["place_odds_min"], "place_odds_max": runner["place_odds_max"]}
                        for runner in analysis["runners"]],
        }).json()["id"]
        frozen = client.post(f"/api/races/{race_id}/rule-judgements/freeze", json={
            "snapshot_id": snapshot_id, "rule_version_id": rule_id, "judgement_as_of": "2026-08-30T05:00:00Z",
        }).json()

    assert frozen["runners"][0]["judgement"] == "判定不能"
    assert frozen["runners"][0]["missing_reasons"] == ["有効な出走馬の単勝オッズがありません。"]
