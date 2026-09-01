from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app


CSV = (Path(__file__).parents[2] / "examples" / "sample-race.csv").read_bytes()
RESULTS = ("horse_number,finish_position,status,win_payout_per_100,place_payout_per_100\n"
           "1,1,確定,420,150\n2,2,確定,0,180\n3,3,確定,0,0\n4,4,確定,0,0\n5,5,確定,0,0\n").encode()


def setup(client: TestClient) -> tuple[int, int, int]:
    rule_id = client.get("/api/rule-versions").json()[0]["id"]
    race_id = client.post("/api/races/import", content=CSV, headers={"Content-Type": "text/csv"}).json()["race_id"]
    analysis = client.get(f"/api/races/{race_id}").json()
    snapshot_id = client.post(f"/api/races/{race_id}/odds-snapshots", json={
        "observed_at": "2026-08-30T05:00:00Z", "source": "test",
        "runners": [{"horse_number": item["horse_number"], "win_odds": item["win_odds"],
                     "place_odds_min": item["place_odds_min"], "place_odds_max": item["place_odds_max"]}
                    for item in analysis["runners"]],
    }).json()["id"]
    return race_id, snapshot_id, rule_id


def test_comparison_distinguishes_unfixed_fixed_and_rule_mismatch(tmp_path: Path) -> None:
    app = create_app(tmp_path / "comparison.sqlite3", now_provider=lambda: datetime(2026, 8, 30, 5, 10, tzinfo=timezone.utc))
    with TestClient(app) as client:
        race_id, snapshot_id, rule_id = setup(client)
        unfixed = client.get(f"/api/races/{race_id}/market-rule-comparison", params={"snapshot_id": snapshot_id, "rule_version_id": rule_id})
        mismatch = client.get(f"/api/races/{race_id}/market-rule-comparison", params={"snapshot_id": snapshot_id, "rule_version_id": 999})
        client.post(f"/api/races/{race_id}/rule-judgements/freeze", json={
            "snapshot_id": snapshot_id, "rule_version_id": rule_id, "judgement_as_of": "2026-08-30T05:00:00Z",
        })
        fixed = client.get(f"/api/races/{race_id}/market-rule-comparison", params={"snapshot_id": snapshot_id, "rule_version_id": rule_id})

    assert unfixed.json()["state"] == "judgement_not_generated"
    assert "判定を固定" in unfixed.json()["next_action"]
    assert mismatch.json()["state"] == "rule_version_mismatch"
    payload = fixed.json()
    assert payload["state"] == "available"
    assert payload["fixed_state"] == "fixed"
    assert payload["official_pre_race_eligible"] is True
    assert payload["rows"][0]["market_rank"] == 1
    assert payload["rows"][0]["rule_judgement"] == "注目"
    assert "購入候補" in payload["disclaimer"]


def test_fixed_comparison_does_not_change_after_result_registration(tmp_path: Path) -> None:
    app = create_app(tmp_path / "comparison-result.sqlite3", now_provider=lambda: datetime(2026, 8, 30, 5, 10, tzinfo=timezone.utc))
    with TestClient(app) as client:
        race_id, snapshot_id, rule_id = setup(client)
        client.post(f"/api/races/{race_id}/rule-judgements/freeze", json={
            "snapshot_id": snapshot_id, "rule_version_id": rule_id, "judgement_as_of": "2026-08-30T05:00:00Z",
        })
        before = client.get(f"/api/races/{race_id}/market-rule-comparison", params={"snapshot_id": snapshot_id, "rule_version_id": rule_id}).json()
        assert client.post(f"/api/races/{race_id}/results/import", content=RESULTS, headers={"Content-Type": "text/csv"}).status_code == 201
        after = client.get(f"/api/races/{race_id}/market-rule-comparison", params={"snapshot_id": snapshot_id, "rule_version_id": rule_id}).json()

    assert after == before
