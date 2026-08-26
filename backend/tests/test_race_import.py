from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


SAMPLE_CSV = (Path(__file__).parents[2] / "examples" / "sample-race.csv").read_text(encoding="utf-8")


def test_user_can_import_one_race_and_view_market_analysis(tmp_path: Path) -> None:
    app = create_app(tmp_path / "analysis.sqlite3")

    with TestClient(app) as client:
        response = client.post(
            "/api/races/import",
            content=SAMPLE_CSV.encode("utf-8"),
            headers={"Content-Type": "text/csv; charset=utf-8"},
        )

    assert response.status_code == 201, response.text
    analysis = response.json()
    assert analysis["race"] == {
        "organizer": "JRA",
        "country": "JP",
        "racecourse": "東京",
        "race_date": "2026-08-30",
        "race_number": 11,
        "start_time": "15:40",
        "timezone": "Asia/Tokyo",
        "start_utc": "2026-08-30T06:40:00Z",
        "surface": "芝",
        "distance_m": 2000,
        "going": "良",
        "field_size": 5,
    }
    assert len(analysis["runners"]) == 5
    first = analysis["runners"][0]
    assert first["horse_name"] == "アカツキ"
    assert first["raw_inverse_win_odds"] == pytest.approx(0.5)
    assert first["normalized_win_market_share"] == pytest.approx(0.45454545)
    assert first["place_break_even_hit_rate"] == {
        "minimum": pytest.approx(1 / 1.5),
        "midpoint": pytest.approx(1 / 1.35),
        "maximum": pytest.approx(1 / 1.2),
    }
    assert analysis["candidate_status"] == "期待値候補なし"
    assert analysis["candidate_reason"] == "市場基準は独立した予測確率ではないため、候補を生成しません。"


def test_imported_analysis_can_be_loaded_again(tmp_path: Path) -> None:
    app = create_app(tmp_path / "analysis.sqlite3")

    with TestClient(app) as client:
        imported = client.post(
            "/api/races/import",
            content=SAMPLE_CSV.encode("utf-8"),
            headers={"Content-Type": "text/csv; charset=utf-8"},
        ).json()
        response = client.get(f"/api/races/{imported['race_id']}")

    assert response.status_code == 200
    assert response.json() == imported


def test_four_runner_race_does_not_generate_place_analysis(tmp_path: Path) -> None:
    four_runner_csv = "\n".join(SAMPLE_CSV.splitlines()[:5]) + "\n"
    app = create_app(tmp_path / "four-runners.sqlite3")

    with TestClient(app) as client:
        response = client.post(
            "/api/races/import",
            content=four_runner_csv.encode("utf-8"),
            headers={"Content-Type": "text/csv; charset=utf-8"},
        )

    assert response.status_code == 201, response.text
    assert all(
        runner["place_break_even_hit_rate"] is None
        for runner in response.json()["runners"]
    )
