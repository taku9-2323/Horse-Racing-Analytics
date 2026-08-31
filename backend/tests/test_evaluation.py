from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


SAMPLE_CSV = (Path(__file__).parents[2] / "examples" / "sample-race.csv").read_bytes()


class MutableClock:
    def __init__(self, current: datetime) -> None:
        self.current = current

    def __call__(self) -> datetime:
        return self.current


def result_csv() -> bytes:
    return (
        "horse_number,finish_position,status,win_payout_per_100,place_payout_per_100\n"
        "1,1,確定,420,150\n"
        "2,2,確定,0,180\n"
        "3,3,確定,0,0\n"
        "4,4,確定,0,0\n"
        "5,5,確定,0,0\n"
    ).encode()


def prepare_evaluation_sample(client: TestClient, clock: MutableClock) -> None:
    race = client.post(
        "/api/races/import", content=SAMPLE_CSV,
        headers={"Content-Type": "text/csv"},
    ).json()
    race_id = race["race_id"]
    tags = client.get("/api/analysis-tags").json()
    market_tag = next(tag for tag in tags if tag["rule_key"] == "market_odds_level")
    enabled = client.post(
        f"/api/analysis-tags/{market_tag['id']}/state",
        json={"enabled": True, "reason": "評価テスト"},
    )
    assert enabled.status_code == 200
    snapshot = client.post(
        f"/api/races/{race_id}/odds-snapshots",
        json={
            "observed_at": "2026-08-30T04:55:00Z",
            "source": "test",
            "runners": [{
                "horse_number": runner["horse_number"],
                "win_odds": runner["win_odds"],
                "place_odds_min": runner["place_odds_min"],
                "place_odds_max": runner["place_odds_max"],
            } for runner in race["runners"]],
        },
    ).json()
    frozen = client.post(
        f"/api/odds-snapshots/{snapshot['id']}/freeze",
        json={"model_identifier": "market-baseline", "model_version": "1.0"},
    )
    assert frozen.status_code == 201
    corrected = client.post(
        f"/api/predictions/{frozen.json()['id']}/correct",
        json={"reason": "入力訂正", "input_snapshot_id": snapshot["id"]},
    )
    assert corrected.status_code == 201
    bet = client.post(f"/api/races/{race_id}/bets", json={
        "horse_number": 1, "bet_type": "win",
        "decision_type": "discretionary", "amount_yen": 200,
        "prediction_run_id": corrected.json()["id"],
    })
    assert bet.status_code == 201
    clock.current = datetime(2026, 8, 30, 7, 0, tzinfo=timezone.utc)
    post_start = client.post(
        f"/api/odds-snapshots/{snapshot['id']}/freeze",
        json={"model_identifier": "market-baseline", "model_version": "2.0"},
    )
    assert post_start.status_code == 201
    settled = client.post(
        f"/api/races/{race_id}/results/import", content=result_csv(),
        headers={"Content-Type": "text/csv"},
    )
    assert settled.status_code == 201


def test_evaluation_reports_full_field_brier_bands_and_separate_returns(tmp_path: Path) -> None:
    clock = MutableClock(datetime(2026, 8, 30, 5, 0, tzinfo=timezone.utc))
    app = create_app(tmp_path / "evaluation.sqlite3", now_provider=clock)

    with TestClient(app) as client:
        prepare_evaluation_sample(client, clock)
        response = client.get("/api/evaluation")

    assert response.status_code == 200
    evaluation = response.json()
    assert evaluation["calibration"]["eligible_prediction_runs"] == 1
    assert evaluation["calibration"]["excluded_prediction_runs"] == 2
    assert evaluation["calibration"]["runner_count"] == 5
    assert evaluation["calibration"]["brier_score"] == pytest.approx(19 / 242)
    populated_bands = [band for band in evaluation["calibration"]["bands"] if band["count"]]
    assert [(band["lower_bound"], band["upper_bound"], band["count"]) for band in populated_bands] == [
        (0.0, 0.1, 2), (0.1, 0.2, 1), (0.2, 0.3, 1), (0.4, 0.5, 1),
    ]
    assert populated_bands[-1]["average_predicted_probability"] == pytest.approx(5 / 11)
    assert populated_bands[-1]["actual_win_rate"] == 1.0
    assert all(band["small_sample"] for band in populated_bands)
    assert evaluation["returns"]["candidate"] == {
        "stake_yen": 0, "payout_yen": 0, "refund_yen": 0,
        "profit_yen": 0, "return_rate": None,
    }
    assert evaluation["returns"]["discretionary"] == {
        "stake_yen": 200, "payout_yen": 840, "refund_yen": 0,
        "profit_yen": 640, "return_rate": 4.2,
    }


def test_evaluation_filters_calibration_and_returns_by_recorded_context(tmp_path: Path) -> None:
    clock = MutableClock(datetime(2026, 8, 30, 5, 0, tzinfo=timezone.utc))
    app = create_app(tmp_path / "evaluation-filters.sqlite3", now_provider=clock)

    with TestClient(app) as client:
        prepare_evaluation_sample(client, clock)
        filtered = client.get("/api/evaluation", params={
            "model_identifier": "market-baseline",
            "model_version": "1.0",
            "tag_rule_key": "market_odds_level",
            "tag_version": 1,
            "racecourse": "東京",
            "bet_type": "win",
            "odds_min": 1,
            "odds_max": 3,
            "popularity_min": 1,
            "popularity_max": 1,
            "prediction_frozen_from": "2026-08-30T04:59:00Z",
            "prediction_frozen_to": "2026-08-30T05:01:00Z",
        })
        excluded_version = client.get("/api/evaluation", params={
            "model_identifier": "market-baseline", "model_version": "2.0",
        })

    assert filtered.status_code == 200
    report = filtered.json()
    assert report["filters"] == {
        "model_identifier": "market-baseline", "model_version": "1.0",
        "tag_rule_key": "market_odds_level", "tag_version": 1,
        "racecourse": "東京", "bet_type": "win",
        "odds_min": 1.0, "odds_max": 3.0,
        "popularity_min": 1, "popularity_max": 1,
        "prediction_frozen_from": "2026-08-30T04:59:00Z",
        "prediction_frozen_to": "2026-08-30T05:01:00Z",
    }
    assert report["calibration"]["eligible_prediction_runs"] == 1
    assert report["calibration"]["excluded_prediction_runs"] == 1
    assert report["calibration"]["runner_count"] == 1
    assert report["calibration"]["brier_score"] == pytest.approx(36 / 121)
    assert report["returns"]["discretionary"]["profit_yen"] == 640
    assert report["filter_options"] == {
        "models": [
            {"identifier": "market-baseline", "version": "1.0"},
            {"identifier": "market-baseline", "version": "2.0"},
        ],
        "tags": [{"rule_key": "market_odds_level", "version": 1}],
        "racecourses": ["東京"],
        "bet_types": ["place", "win"],
    }
    excluded_report = excluded_version.json()
    assert excluded_report["calibration"]["eligible_prediction_runs"] == 0
    assert excluded_report["calibration"]["excluded_prediction_runs"] == 1
    assert excluded_report["returns"]["discretionary"]["stake_yen"] == 0


def test_purchase_filters_use_the_recorded_prediction_and_place_lower_odds_after_correction(
    tmp_path: Path,
) -> None:
    clock = MutableClock(datetime(2026, 8, 30, 5, 0, tzinfo=timezone.utc))
    app = create_app(tmp_path / "recorded-bet-context.sqlite3", now_provider=clock)

    with TestClient(app) as client:
        race = client.post(
            "/api/races/import", content=SAMPLE_CSV,
            headers={"Content-Type": "text/csv"},
        ).json()
        race_id = race["race_id"]
        runners = [{
            "horse_number": runner["horse_number"], "win_odds": runner["win_odds"],
            "place_odds_min": runner["place_odds_min"],
            "place_odds_max": runner["place_odds_max"],
        } for runner in race["runners"]]
        snapshot = client.post(f"/api/races/{race_id}/odds-snapshots", json={
            "observed_at": "2026-08-30T04:55:00Z", "source": "test", "runners": runners,
        }).json()
        prediction = client.post(f"/api/odds-snapshots/{snapshot['id']}/freeze", json={
            "model_identifier": "market-baseline", "model_version": "1.0",
        }).json()
        for horse_number, bet_type in ((1, "win"), (2, "place")):
            registered = client.post(f"/api/races/{race_id}/bets", json={
                "horse_number": horse_number, "bet_type": bet_type,
                "decision_type": "discretionary", "amount_yen": 100,
                "prediction_run_id": prediction["id"],
            })
            assert registered.status_code == 201
            assert registered.json()["prediction_run_id"] == prediction["id"]
        corrected_runners = [dict(runner) for runner in runners]
        corrected_runners[0]["win_odds"] = 4.0
        corrected_snapshot = client.post(f"/api/races/{race_id}/odds-snapshots", json={
            "observed_at": "2026-08-30T04:56:00Z", "source": "test",
            "runners": corrected_runners,
        }).json()
        clock.current = datetime(2026, 8, 30, 7, 0, tzinfo=timezone.utc)
        corrected = client.post(f"/api/predictions/{prediction['id']}/correct", json={
            "reason": "オッズ訂正", "input_snapshot_id": corrected_snapshot["id"],
        })
        assert corrected.status_code == 201
        client.post(
            f"/api/races/{race_id}/results/import", content=result_csv(),
            headers={"Content-Type": "text/csv"},
        )
        win_band = client.get("/api/evaluation", params={
            "bet_type": "win", "odds_min": 1, "odds_max": 3,
        }).json()
        place_band = client.get("/api/evaluation", params={
            "bet_type": "place", "odds_min": 1, "odds_max": 3,
        }).json()

    assert win_band["returns"]["discretionary"]["stake_yen"] == 100
    assert place_band["returns"]["discretionary"]["stake_yen"] == 100
    assert win_band["filter_options"]["models"] == [
        {"identifier": "market-baseline", "version": "1.0"},
    ]
