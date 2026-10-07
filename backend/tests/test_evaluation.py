from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.evaluation import EvaluationFilters, PredictionEvaluationRow, PredictionRunEvaluation, build_evaluation_report


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
    assert evaluation["calibration_status"] == "multiple_groups"
    assert evaluation["calibration_reason"]
    assert evaluation["calibration"] is None
    groups = {
        (group["model_identifier"], group["model_version"], group["bet_type"]): group
        for group in evaluation["calibration_groups"]
    }
    assert set(groups) == {
        ("market-baseline", "1.0", "win"), ("market-baseline", "1.0", "place"),
        ("market-baseline", "2.0", "win"), ("market-baseline", "2.0", "place"),
    }
    calibration = groups[("market-baseline", "1.0", "win")]
    assert calibration["eligible_prediction_runs"] == 1
    assert calibration["ineligible_excluded_run_count"] == 1
    assert calibration["runner_observation_count"] == 5
    assert calibration["brier_score"] == pytest.approx(19 / 242)
    populated_bands = [band for band in calibration["bands"] if band["count"]]
    assert [(band["lower_bound"], band["upper_bound"], band["count"]) for band in populated_bands] == [
        (0.0, 0.1, 2), (0.1, 0.2, 1), (0.2, 0.3, 1), (0.4, 0.5, 1),
    ]
    assert populated_bands[-1]["average_predicted_probability"] == pytest.approx(5 / 11)
    assert populated_bands[-1]["actual_win_rate"] == 1.0
    assert all("small_sample" not in band for band in populated_bands)
    assert calibration["uncertainty_status"] == "not_estimated"
    assert calibration["uncertainty_interval"] is None
    assert "レース内相関" in calibration["uncertainty_reason"]
    assert evaluation["returns"]["candidate"] == {
        "stake_yen": 0, "payout_yen": 0, "refund_yen": 0,
        "profit_yen": 0, "return_rate": None,
    }
    assert evaluation["returns"]["discretionary"] == {
        "stake_yen": 200, "payout_yen": 840, "refund_yen": 0,
        "profit_yen": 640, "return_rate": 4.2,
    }


def test_calibration_groups_deduplicate_whole_runs_by_bet_capability_and_pair_market() -> None:
    runs: list[PredictionRunEvaluation] = [
        _run(1, "independent", "model-a", "v1", "2026-08-30T04:50:00Z", True, True),
        _run(2, "independent", "model-a", "v1", "2026-08-30T04:51:00Z", True, False),
        _run(3, "market_baseline", "market-baseline", "1.0", "2026-08-30T04:49:00Z", True, False),
        _run(4, "independent", "model-a", "v1", "2026-08-30T04:51:00Z", True, False),
        _run(5, "independent", "model-a", "v1", "2026-08-30T04:52:00Z", True, True, eligible=False),
        _run(6, "market_baseline", "market-baseline", "baseline-v2", "2026-08-30T04:49:00Z", True, False),
        _run(7, "market_baseline", "market-baseline", "1.0", "2026-08-30T04:49:00Z", True, False),
    ]
    rows = [
        _row(1, "independent", "model-a", "v1", "win", 0.2, 0),
        _row(1, "independent", "model-a", "v1", "place", 0.6, 1),
        _row(2, "independent", "model-a", "v1", "win", 0.9, 1),
        _row(3, "market_baseline", "market-baseline", "1.0", "win", 0.4, 1),
        _row(4, "independent", "model-a", "v1", "win", 0.8, 1),
        _row(5, "independent", "model-a", "v1", "win", 0.1, 0),
        _row(6, "market_baseline", "market-baseline", "baseline-v2", "win", 0.2, 1),
        _row(7, "market_baseline", "market-baseline", "1.0", "win", 0.5, 1),
    ]

    report = build_evaluation_report(runs, rows, [], EvaluationFilters())
    groups = {(group.model_identifier, group.model_version, group.bet_type): group for group in report.calibration_groups}
    win = groups[("model-a", "v1", "win")]
    place = groups[("model-a", "v1", "place")]

    assert win.selected_prediction_run_count == 1
    assert win.duplicate_excluded_run_count == 2
    assert win.ineligible_excluded_run_count == 1
    assert win.runner_observation_count == 1
    assert win.brier_score == pytest.approx(0.04)
    assert win.market_comparison_status == "available"
    comparisons = {comparison.baseline_version: comparison for comparison in win.market_comparisons}
    assert set(comparisons) == {"1.0", "baseline-v2"}
    assert comparisons["1.0"].status == "available"
    assert comparisons["1.0"].market_brier_score == pytest.approx(0.25)
    assert comparisons["1.0"].brier_difference == pytest.approx(-0.21)
    assert comparisons["1.0"].duplicate_excluded_run_count == 1
    assert comparisons["baseline-v2"].market_brier_score == pytest.approx(0.64)
    assert not hasattr(win, "market_brier_score")
    assert place.selected_prediction_run_count == 1
    assert place.unsupported_bet_excluded_run_count == 2
    assert place.runner_observation_count == 1
    assert place.brier_score == pytest.approx(0.16)
    assert place.market_comparison_status == "not_applicable"
    assert place.market_comparisons == []
    assert report.calibration_status == "multiple_groups"
    assert report.calibration is None


def test_evaluation_reports_no_group_state_without_legacy_zero_calibration() -> None:
    report = build_evaluation_report([], [], [], EvaluationFilters())

    assert report.calibration_status == "no_groups"
    assert report.calibration_reason
    assert report.calibration_groups == []
    assert report.calibration is None


def test_calibration_market_status_distinguishes_no_baseline_from_no_common_rows() -> None:
    independent = _run(1, "independent", "model-a", "v1", "2026-08-30T04:50:00Z", True, True)
    row = _row(1, "independent", "model-a", "v1", "win", 0.2, 0)
    no_baseline = build_evaluation_report([independent], [row], [], EvaluationFilters())
    group = next(group for group in no_baseline.calibration_groups if group.bet_type == "win")
    assert group.market_comparison_status == "no_baseline"
    assert group.market_comparison_reason
    assert group.market_comparisons == []

    market = _run(2, "market_baseline", "market-baseline", "b1", "2026-08-30T04:49:00Z", True, False)
    mismatched_market_row = _row(
        2, "market_baseline", "market-baseline", "b1", "win", 0.4, 0,
    )
    mismatched_market_row["active_result_version_id"] = 21
    no_common = build_evaluation_report(
        [independent, market], [row, mismatched_market_row], [], EvaluationFilters(),
    )
    group = next(group for group in no_common.calibration_groups
                 if group.model_identifier == "model-a" and group.bet_type == "win")
    assert group.market_comparison_status == "no_common_observations"
    assert group.market_comparison_reason
    assert group.market_comparisons[0].status == "no_common_observations"


def _run(
    run_id: int, prediction_kind: Literal["market_baseline", "independent"], model_identifier: str, model_version: str,
    frozen_at: str, has_win: bool, has_place: bool, *, eligible: bool = True,
) -> PredictionRunEvaluation:
    return PredictionRunEvaluation(
        prediction_run_id=run_id, race_id=1, input_snapshot_id=10,
        model_identifier=model_identifier, model_version=model_version,
        prediction_kind=prediction_kind, frozen_at=frozen_at, race_date="2026-08-30",
        racecourse="東京", status="active", official_evaluation_eligible=eligible,
        has_win=has_win, has_place=has_place,
    )


def _row(
    run_id: int, prediction_kind: Literal["market_baseline", "independent"], model_identifier: str, model_version: str,
    bet_type: Literal["win", "place"], probability: float, outcome: int,
) -> PredictionEvaluationRow:
    return PredictionEvaluationRow(
        prediction_run_id=run_id, prediction_kind=prediction_kind,
        model_identifier=model_identifier, model_version=model_version,
        frozen_at="2026-08-30T04:51:00Z", bet_type=bet_type,
        race_id=1, input_snapshot_id=10, active_result_version_id=20,
        horse_number=2, race_date="2026-08-30", racecourse="東京",
        odds_value=20.0, popularity=3, tags=[], predicted_probability=probability,
        outcome=outcome, eligible=True,
    )


@pytest.mark.parametrize(
    ("baseline_eligible", "baseline_has_win", "ineligible_count", "unsupported_count"),
    [(False, True, 1, 0), (True, False, 0, 1)],
    ids=["ineligible-only-version", "unsupported-only-version"],
)
def test_baseline_version_without_eligible_win_run_is_no_baseline(
    baseline_eligible: bool,
    baseline_has_win: bool,
    ineligible_count: int,
    unsupported_count: int,
) -> None:
    independent = _run(1, "independent", "model-a", "v1", "2026-08-30T04:50:00Z", True, False)
    market = _run(
        2, "market_baseline", "market-baseline", "baseline-v2",
        "2026-08-30T04:49:00Z", baseline_has_win, not baseline_has_win,
        eligible=baseline_eligible,
    )
    report = build_evaluation_report(
        [independent, market],
        [_row(1, "independent", "model-a", "v1", "win", 0.2, 0)],
        [], EvaluationFilters(),
    )

    group = next(group for group in report.calibration_groups
                 if group.model_identifier == "model-a" and group.bet_type == "win")
    comparison = group.market_comparisons[0]
    assert comparison.baseline_version == "baseline-v2"
    assert comparison.status == "no_baseline"
    assert comparison.reason
    assert comparison.model_brier_score is None
    assert comparison.market_brier_score is None
    assert comparison.ineligible_excluded_run_count == ineligible_count
    assert comparison.unsupported_bet_excluded_run_count == unsupported_count
    assert comparison.capability_qualified_run_count == 0
    assert group.market_comparison_status == "no_baseline"
    assert group.market_comparison_reason


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
    assert excluded_report["calibration_status"] == "multiple_groups"
    assert excluded_report["calibration"] is None
    assert all(group["eligible_candidate_run_count"] == 0 for group in excluded_report["calibration_groups"])
    assert all(group["brier_score"] is None for group in excluded_report["calibration_groups"])
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
