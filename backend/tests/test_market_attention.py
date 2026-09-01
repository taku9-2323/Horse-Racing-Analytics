from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


SAMPLE_CSV = (Path(__file__).parents[2] / "examples" / "sample-race.csv").read_bytes()


def test_market_attention_uses_selected_snapshot_and_competition_ranks(tmp_path: Path) -> None:
    app = create_app(tmp_path / "market-attention.sqlite3")
    with TestClient(app) as client:
        race_id = client.post("/api/races/import", content=SAMPLE_CSV, headers={"Content-Type": "text/csv"}).json()["race_id"]
        runners = [
            {"horse_number": number, "win_odds": odds, "place_odds_min": 1.2, "place_odds_max": 1.5}
            for number, odds in [(1, 4.0), (2, 4.0), (3, 8.0), (4, 16.0), (5, 32.0)]
        ]
        first = client.post(f"/api/races/{race_id}/odds-snapshots", json={
            "observed_at": "2026-08-30T05:00:00Z", "source": "test", "runners": runners,
        }).json()
        latest = client.post(f"/api/races/{race_id}/odds-snapshots", json={
            "observed_at": "2026-08-30T05:10:00Z", "source": "test", "runners": [
                {**runner, "win_odds": runner["win_odds"] * 2} for runner in runners
            ],
        }).json()

        selected = client.get(f"/api/races/{race_id}/market-attention", params={"snapshot_id": first["id"]})
        default_latest = client.get(f"/api/races/{race_id}/market-attention")

    assert selected.status_code == 200
    payload = selected.json()
    assert payload["snapshot_id"] == first["id"]
    assert payload["observed_at"] == "2026-08-30T05:00:00Z"
    assert [(runner["horse_number"], runner["rank"]) for runner in payload["runners"]] == [
        (1, 1), (2, 1), (3, 3), (4, 4), (5, 5),
    ]
    assert sum(runner["normalized_win_market_share"] for runner in payload["runners"]) == pytest.approx(1)
    assert "購入推奨ではありません" in payload["disclaimer"]
    assert default_latest.json()["snapshot_id"] == latest["id"]
    assert default_latest.json()["observed_at"] == "2026-08-30T05:10:00Z"


def test_market_attention_reports_missing_race_snapshot_and_wrong_snapshot(tmp_path: Path) -> None:
    app = create_app(tmp_path / "market-attention-errors.sqlite3")
    with TestClient(app) as client:
        missing_race = client.get("/api/races/999/market-attention")
        race_id = client.post("/api/races/import", content=SAMPLE_CSV, headers={"Content-Type": "text/csv"}).json()["race_id"]
        missing_snapshot = client.get(f"/api/races/{race_id}/market-attention")
        client.post(f"/api/races/{race_id}/odds-snapshots", json={
            "observed_at": "2026-08-30T05:00:00Z", "source": "test", "runners": [
                {"horse_number": number, "win_odds": 2 + number, "place_odds_min": 1.2, "place_odds_max": 1.5}
                for number in range(1, 6)
            ],
        })
        wrong_snapshot = client.get(f"/api/races/{race_id}/market-attention", params={"snapshot_id": 999})

    assert missing_race.status_code == 404
    assert missing_race.json()["detail"]["code"] == "race_not_found"
    assert missing_snapshot.status_code == 409
    assert missing_snapshot.json()["detail"]["code"] == "odds_snapshot_not_found"
    assert wrong_snapshot.status_code == 404
    assert wrong_snapshot.json()["detail"]["code"] == "snapshot_not_found"
