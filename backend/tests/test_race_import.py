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


def test_analysis_ready_races_are_listed_in_latest_start_order(tmp_path: Path) -> None:
    app = create_app(tmp_path / "analysis.sqlite3")
    earlier_csv = SAMPLE_CSV.replace("2026-08-30", "2026-08-29").replace(
        "2026-08-30T06:40:00Z", "2026-08-29T06:40:00Z"
    )

    with TestClient(app) as client:
        earlier = client.post(
            "/api/races/import",
            content=earlier_csv.encode("utf-8"),
            headers={"Content-Type": "text/csv; charset=utf-8"},
        ).json()
        later = client.post(
            "/api/races/import",
            content=SAMPLE_CSV.encode("utf-8"),
            headers={"Content-Type": "text/csv; charset=utf-8"},
        ).json()
        response = client.get("/api/races")

    assert response.status_code == 200
    assert response.json() == [
        {"race_id": later["race_id"], "race": later["race"]},
        {"race_id": earlier["race_id"], "race": earlier["race"]},
    ]


def test_race_detail_uses_the_latest_saved_odds_snapshot(tmp_path: Path) -> None:
    app = create_app(tmp_path / "analysis.sqlite3")
    latest_odds = [
        {"horse_number": 1, "win_odds": 4.0, "place_odds_min": 1.4, "place_odds_max": 1.8},
        {"horse_number": 2, "win_odds": 5.0, "place_odds_min": 1.5, "place_odds_max": 2.0},
        {"horse_number": 3, "win_odds": 8.0, "place_odds_min": 1.8, "place_odds_max": 2.5},
        {"horse_number": 4, "win_odds": 12.0, "place_odds_min": 2.0, "place_odds_max": 3.0},
        {"horse_number": 5, "win_odds": 20.0, "place_odds_min": 2.5, "place_odds_max": 4.0},
    ]

    with TestClient(app) as client:
        race_id = client.post(
            "/api/races/import", content=SAMPLE_CSV.encode("utf-8"),
            headers={"Content-Type": "text/csv; charset=utf-8"},
        ).json()["race_id"]
        saved = client.post(f"/api/races/{race_id}/odds-snapshots", json={
            "observed_at": "2026-08-30T06:00:00Z", "source": "ui", "runners": latest_odds,
        })
        detail = client.get(f"/api/races/{race_id}")

    assert saved.status_code == 201, saved.text
    assert detail.status_code == 200
    first = detail.json()["runners"][0]
    assert first["win_odds"] == 4.0
    assert first["place_odds_min"] == 1.4
    assert first["normalized_win_market_share"] == pytest.approx(0.3529411765)


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


def test_all_csv_errors_are_reported_and_nothing_is_saved(tmp_path: Path) -> None:
    lines = SAMPLE_CSV.splitlines()
    first = lines[1].split(",")
    second = lines[2].split(",")
    first[18] = "0"
    first[19] = "3.0"
    first[20] = "2.0"
    second[14] = "not-a-number"
    invalid_csv = "\n".join([lines[0], ",".join(first), ",".join(second)]) + "\n"
    app = create_app(tmp_path / "invalid.sqlite3")

    with TestClient(app) as client:
        response = client.post(
            "/api/races/import",
            content=invalid_csv.encode("utf-8"),
            headers={"Content-Type": "text/csv; charset=utf-8"},
        )
        missing = client.get("/api/races/1")

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "csv_validation_failed"
    errors = {(error["row"], error["column"], error["code"]) for error in detail["errors"]}
    assert (2, "win_odds", "must_be_positive") in errors
    assert (2, "place_odds_min", "invalid_range") in errors
    assert (3, "age", "invalid_integer") in errors
    assert missing.status_code == 404


def test_same_csv_is_an_idempotent_success(tmp_path: Path) -> None:
    app = create_app(tmp_path / "idempotent.sqlite3")

    with TestClient(app) as client:
        first = client.post(
            "/api/races/import", content=SAMPLE_CSV.encode("utf-8"),
            headers={"Content-Type": "text/csv; charset=utf-8"},
        )
        second = client.post(
            "/api/races/import", content=SAMPLE_CSV.encode("utf-8"),
            headers={"Content-Type": "text/csv; charset=utf-8"},
        )

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json() == first.json()


def test_same_natural_key_with_different_content_is_rejected(tmp_path: Path) -> None:
    changed_csv = SAMPLE_CSV.replace("アカツキ,4,牡", "別の馬,4,牡")
    app = create_app(tmp_path / "conflict.sqlite3")

    with TestClient(app) as client:
        original = client.post(
            "/api/races/import", content=SAMPLE_CSV.encode("utf-8"),
            headers={"Content-Type": "text/csv; charset=utf-8"},
        )
        conflict = client.post(
            "/api/races/import", content=changed_csv.encode("utf-8"),
            headers={"Content-Type": "text/csv; charset=utf-8"},
        )
        stored = client.get(f"/api/races/{original.json()['race_id']}")

    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "race_import_conflict"
    assert stored.json() == original.json()


def test_same_rows_in_a_different_order_are_idempotent(tmp_path: Path) -> None:
    lines = SAMPLE_CSV.splitlines()
    reordered = "\n".join([lines[0], *reversed(lines[1:])]) + "\n"
    app = create_app(tmp_path / "reordered.sqlite3")

    with TestClient(app) as client:
        first = client.post("/api/races/import", content=SAMPLE_CSV.encode(), headers={"Content-Type": "text/csv"})
        second = client.post("/api/races/import", content=reordered.encode(), headers={"Content-Type": "text/csv"})

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json() == first.json()


def test_duplicate_horse_number_and_non_finite_number_are_structured_errors(tmp_path: Path) -> None:
    lines = SAMPLE_CSV.splitlines()
    duplicate = lines[2].split(",")
    duplicate[12] = "1"
    duplicate[18] = "nan"
    invalid_csv = "\n".join([lines[0], lines[1], ",".join(duplicate)]) + "\n"
    app = create_app(tmp_path / "structural-errors.sqlite3")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post("/api/races/import", content=invalid_csv.encode(), headers={"Content-Type": "text/csv"})

    assert response.status_code == 422
    errors = {(error["row"], error["column"], error["code"]) for error in response.json()["detail"]["errors"]}
    assert (3, "horse_number", "duplicate_horse_number") in errors
    assert (3, "win_odds", "invalid_number") in errors


def test_encoding_datetime_and_choice_contract_errors_are_reported(tmp_path: Path) -> None:
    lines = SAMPLE_CSV.splitlines()
    invalid = lines[1].split(",")
    invalid[7] = "not-a-datetime"
    invalid[8] = "砂"
    invalid_csv = "\n".join([lines[0], ",".join(invalid)]) + "\n"
    app = create_app(tmp_path / "contract-errors.sqlite3")

    with TestClient(app) as client:
        values = client.post("/api/races/import", content=invalid_csv.encode(), headers={"Content-Type": "text/csv"})
        encoding = client.post("/api/races/import", content=b"\x81", headers={"Content-Type": "text/csv"})

    value_codes = {error["code"] for error in values.json()["detail"]["errors"]}
    assert values.status_code == 422
    assert {"invalid_datetime", "invalid_choice"} <= value_codes
    assert encoding.status_code == 422
    assert encoding.json()["detail"]["errors"][0]["code"] == "invalid_encoding"


def test_equivalent_iso_time_representations_are_the_same_race(tmp_path: Path) -> None:
    lines = SAMPLE_CSV.splitlines()
    equivalent = lines[2].split(",")
    equivalent[5] = "15:40:00"
    equivalent[7] = "2026-08-30T06:40:00+00:00"
    csv_with_equivalent_times = "\n".join([lines[0], lines[1], ",".join(equivalent), *lines[3:]]) + "\n"
    app = create_app(tmp_path / "equivalent-times.sqlite3")

    with TestClient(app) as client:
        response = client.post("/api/races/import", content=csv_with_equivalent_times.encode(), headers={"Content-Type": "text/csv"})

    assert response.status_code == 201, response.text


def test_invalid_first_row_does_not_hide_later_race_mismatch(tmp_path: Path) -> None:
    lines = SAMPLE_CSV.splitlines()
    first = lines[1].split(",")
    third = lines[3].split(",")
    first[7] = "bad"
    third[7] = "2026-08-30T07:40:00Z"
    invalid_csv = "\n".join([lines[0], ",".join(first), lines[2], ",".join(third), *lines[4:]]) + "\n"
    app = create_app(tmp_path / "all-row-errors.sqlite3")

    with TestClient(app) as client:
        response = client.post("/api/races/import", content=invalid_csv.encode(), headers={"Content-Type": "text/csv"})

    errors = {(error["row"], error["column"], error["code"]) for error in response.json()["detail"]["errors"]}
    assert response.status_code == 422
    assert (2, "start_utc", "invalid_datetime") in errors
    assert (4, "start_utc", "mixed_race") in errors
