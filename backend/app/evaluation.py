from datetime import datetime, timezone
from typing import Literal, TypedDict

from pydantic import BaseModel, Field, field_validator, model_validator

from app.bets import SettlementTotals, totals_for


class EvaluationRow(TypedDict):
    model_identifier: str | None
    model_version: str | None
    frozen_at: str | None
    bet_type: Literal["win", "place"]
    racecourse: str
    odds_value: float | None
    popularity: int | None
    tags: list[tuple[str, int]]


class PredictionRunEvaluation(TypedDict):
    prediction_run_id: int
    race_id: int
    input_snapshot_id: int
    model_identifier: str
    model_version: str
    prediction_kind: Literal["market_baseline", "independent"]
    frozen_at: str
    race_date: str
    racecourse: str
    status: str
    official_evaluation_eligible: bool
    has_win: bool
    has_place: bool


class PredictionEvaluationRow(EvaluationRow):
    prediction_run_id: int
    prediction_kind: Literal["market_baseline", "independent"]
    race_id: int
    input_snapshot_id: int
    active_result_version_id: int
    horse_number: int
    race_date: str
    predicted_probability: float
    outcome: int
    eligible: bool


class SettlementEvaluationRow(EvaluationRow):
    decision_type: Literal["candidate", "discretionary"]
    stake_yen: int
    payout_yen: int
    refund_yen: int


class ProbabilityBand(BaseModel):
    lower_bound: float
    upper_bound: float
    count: int
    average_predicted_probability: float | None
    actual_win_rate: float | None
    small_sample: bool


class CalibrationBand(BaseModel):
    lower_bound: float
    upper_bound: float
    count: int
    average_predicted_probability: float | None
    actual_win_rate: float | None


class CalibrationSummary(BaseModel):
    eligible_prediction_runs: int
    excluded_prediction_runs: int
    runner_count: int
    brier_score: float | None
    bands: list[ProbabilityBand]


class MarketComparison(BaseModel):
    baseline_version: str | None
    status: Literal["available", "no_baseline", "no_common_observations", "not_applicable"]
    reason: str | None
    model_brier_score: float | None
    market_brier_score: float | None
    brier_difference: float | None
    matched_runner_observation_count: int
    matched_race_count: int
    matched_observation_count: int
    raw_unique_run_count: int
    eligible_candidate_run_count: int
    capability_qualified_run_count: int
    selected_run_count: int
    ineligible_excluded_run_count: int
    unsupported_bet_excluded_run_count: int
    duplicate_excluded_run_count: int


class CalibrationGroup(BaseModel):
    model_identifier: str
    model_version: str
    bet_type: Literal["win", "place"]
    eligible_prediction_runs: int
    excluded_prediction_runs: int
    raw_unique_run_count: int
    eligible_candidate_run_count: int
    capability_qualified_run_count: int
    selected_prediction_run_count: int
    ineligible_excluded_run_count: int
    unsupported_bet_excluded_run_count: int
    duplicate_excluded_run_count: int
    race_date_from: str | None
    race_date_to: str | None
    prediction_frozen_from: str | None
    prediction_frozen_to: str | None
    distinct_race_count: int
    distinct_observation_count: int
    runner_observation_count: int
    brier_score: float | None
    bands: list[CalibrationBand]
    market_comparisons: list[MarketComparison]
    market_comparison_status: Literal[
        "available", "no_baseline", "no_common_observations", "not_applicable",
    ]
    market_comparison_reason: str | None
    uncertainty_status: Literal["not_estimated"]
    uncertainty_interval: None = None
    uncertainty_reason: str


class EvaluationFilters(BaseModel):
    model_identifier: str | None = None
    model_version: str | None = None
    tag_rule_key: str | None = None
    tag_version: int | None = Field(default=None, ge=1)
    racecourse: str | None = None
    bet_type: Literal["win", "place"] | None = None
    odds_min: float | None = Field(default=None, gt=0)
    odds_max: float | None = Field(default=None, gt=0)
    popularity_min: int | None = Field(default=None, ge=1)
    popularity_max: int | None = Field(default=None, ge=1)
    prediction_frozen_from: datetime | None = None
    prediction_frozen_to: datetime | None = None

    @field_validator("prediction_frozen_from", "prediction_frozen_to")
    @classmethod
    def prediction_time_must_include_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("予測固定時刻にはタイムゾーンが必要です。")
        return value

    @model_validator(mode="after")
    def validate_ranges_and_versions(self) -> "EvaluationFilters":
        if self.tag_version is not None and self.tag_rule_key is None:
            raise ValueError("タグ版を指定するときはタグを選択してください。")
        if self.odds_min is not None and self.odds_max is not None and self.odds_min > self.odds_max:
            raise ValueError("オッズ帯の下限は上限以下にしてください。")
        if self.popularity_min is not None and self.popularity_max is not None and self.popularity_min > self.popularity_max:
            raise ValueError("人気帯の下限は上限以下にしてください。")
        if (
            self.prediction_frozen_from is not None and self.prediction_frozen_to is not None
            and self.prediction_frozen_from > self.prediction_frozen_to
        ):
            raise ValueError("予測固定時刻の開始は終了以前にしてください。")
        return self


class ModelOption(BaseModel):
    identifier: str
    version: str


class TagOption(BaseModel):
    rule_key: str
    version: int


class EvaluationFilterOptions(BaseModel):
    models: list[ModelOption]
    tags: list[TagOption]
    racecourses: list[str]
    bet_types: list[Literal["win", "place"]]


class EvaluationReport(BaseModel):
    filters: EvaluationFilters
    filter_options: EvaluationFilterOptions
    calibration_groups: list[CalibrationGroup]
    calibration_status: Literal["no_groups", "single_group", "multiple_groups"]
    calibration_reason: str | None
    calibration: CalibrationSummary | None
    returns: dict[str, SettlementTotals]


UNCERTAINTY_REASON = "レース内相関を考慮した不確実性推定法は未実装・未検証です。"


def build_evaluation_report(
    prediction_runs: list[PredictionRunEvaluation],
    all_prediction_rows: list[PredictionEvaluationRow],
    settlement_rows: list[SettlementEvaluationRow],
    filters: EvaluationFilters,
) -> EvaluationReport:
    filtered_runs = [run for run in prediction_runs if _matches_run_filters(run, filters)]
    filtered_settlements = [row for row in settlement_rows if _matches_filters(row, filters)]
    group_keys = sorted({
        (run["model_identifier"], run["model_version"])
        for run in filtered_runs
    })
    bet_types: tuple[Literal["win", "place"], ...] = (
        (filters.bet_type,) if filters.bet_type is not None else ("win", "place")
    )
    groups: list[CalibrationGroup] = []
    for model_identifier, model_version in group_keys:
        raw_group_runs = [
            run for run in filtered_runs
            if run["model_identifier"] == model_identifier and run["model_version"] == model_version
        ]
        for bet_type in bet_types:
            raw_runs = _unique_runs(raw_group_runs)
            eligible_runs = [
                run for run in raw_runs
                if run["status"] == "active" and run["official_evaluation_eligible"]
            ]
            capable_runs = [run for run in eligible_runs if _supports_bet(run, bet_type)]
            selected_runs = _select_whole_runs(capable_runs)
            selected_ids = {run["prediction_run_id"] for run in selected_runs}
            selected_rows = [
                row for row in all_prediction_rows
                if row["prediction_run_id"] in selected_ids
                and row["bet_type"] == bet_type
                and _matches_observation_filters(row, filters)
            ]
            selected_rows = _unique_observations(selected_rows)
            groups.append(_build_group(
                model_identifier, model_version, bet_type, raw_runs, eligible_runs,
                capable_runs, selected_runs, selected_rows, all_prediction_rows,
                prediction_runs,
                run_filter=filters,
            ))

    if not groups:
        calibration_status: Literal["no_groups", "single_group", "multiple_groups"] = "no_groups"
        calibration_reason: str | None = "フィルターに一致する予測runがありません。"
        legacy_calibration = None
    elif len(groups) == 1:
        calibration_status = "single_group"
        calibration_reason = None
        legacy_calibration = _legacy_calibration(groups[0])
    else:
        calibration_status = "multiple_groups"
        calibration_reason = "モデル版・券種ごとに分けて集計しています。各groupを個別に確認してください。"
        legacy_calibration = None

    option_rows: list[EvaluationRow] = [*all_prediction_rows, *settlement_rows]
    return EvaluationReport(
        filters=filters,
        filter_options=EvaluationFilterOptions(
            models=[ModelOption(identifier=identifier, version=version) for identifier, version in sorted({
                (str(row["model_identifier"]), str(row["model_version"]))
                for row in option_rows
                if row.get("model_identifier") is not None and row.get("model_version") is not None
            })],
            tags=[TagOption(rule_key=rule_key, version=version) for rule_key, version in sorted({
                tag for row in option_rows for tag in row["tags"]
            })],
            racecourses=sorted({row["racecourse"] for row in option_rows}),
            bet_types=["place", "win"],
        ),
        calibration_groups=groups,
        calibration_status=calibration_status,
        calibration_reason=calibration_reason,
        calibration=legacy_calibration,
        returns={
            decision_type: totals_for([
                row for row in filtered_settlements if str(row["decision_type"]) == decision_type
            ])
            for decision_type in ("candidate", "discretionary")
        },
    )


def _build_group(
    model_identifier: str,
    model_version: str,
    bet_type: Literal["win", "place"],
    raw_runs: list[PredictionRunEvaluation],
    eligible_runs: list[PredictionRunEvaluation],
    capable_runs: list[PredictionRunEvaluation],
    selected_runs: list[PredictionRunEvaluation],
    rows: list[PredictionEvaluationRow],
    all_rows: list[PredictionEvaluationRow],
    all_runs: list[PredictionRunEvaluation],
    *,
    run_filter: EvaluationFilters,
) -> CalibrationGroup:
    unique_rows = _unique_observations(rows)
    squared_errors = [
        (row["predicted_probability"] - row["outcome"]) ** 2 for row in unique_rows
    ]
    bands = _calibration_bands(unique_rows)
    selected_ids = {run["prediction_run_id"] for run in selected_runs}
    selected_row_ids = {row["prediction_run_id"] for row in unique_rows}
    excluded_run_ids = {
        run["prediction_run_id"] for run in raw_runs
        if run["status"] != "active" or not run["official_evaluation_eligible"]
    }
    races = {row["race_id"] for row in unique_rows}
    observations = {(row["race_id"], row["input_snapshot_id"]) for row in unique_rows}
    runner_observations = {(row["prediction_run_id"], row["horse_number"]) for row in unique_rows}
    race_dates = [run["race_date"] for run in selected_runs]
    frozen_times = [run["frozen_at"] for run in selected_runs]
    market_comparisons: list[MarketComparison] = []
    market_status: Literal["available", "no_baseline", "no_common_observations", "not_applicable"]
    market_reason: str | None
    if raw_runs and raw_runs[0]["prediction_kind"] == "independent":
        if bet_type == "place":
            market_status = "not_applicable"
            market_reason = "複勝には比較対象の市場確率がありません。"
        else:
            market_comparisons = _market_comparisons(unique_rows, all_rows, all_runs, run_filter)
            if not market_comparisons or all(item.status == "no_baseline" for item in market_comparisons):
                market_status = "no_baseline"
                market_reason = "有効な市場基準runがありません。"
            elif any(item.status == "available" for item in market_comparisons):
                market_status = "available"
                market_reason = None
            else:
                market_status = "no_common_observations"
                market_reason = "市場基準との完全一致観測がありません。"
    else:
        market_status = "not_applicable"
        market_reason = "市場基準groupは独立モデルとの比較対象として扱いません。"
    return CalibrationGroup(
        model_identifier=model_identifier,
        model_version=model_version,
        bet_type=bet_type,
        eligible_prediction_runs=len(selected_row_ids),
        excluded_prediction_runs=len(excluded_run_ids),
        raw_unique_run_count=len(raw_runs),
        eligible_candidate_run_count=len(eligible_runs),
        capability_qualified_run_count=len(capable_runs),
        selected_prediction_run_count=len(selected_runs),
        ineligible_excluded_run_count=len(excluded_run_ids),
        unsupported_bet_excluded_run_count=len(eligible_runs) - len(capable_runs),
        duplicate_excluded_run_count=len(capable_runs) - len(selected_runs),
        race_date_from=min(race_dates) if race_dates else None,
        race_date_to=max(race_dates) if race_dates else None,
        prediction_frozen_from=min(frozen_times) if frozen_times else None,
        prediction_frozen_to=max(frozen_times) if frozen_times else None,
        distinct_race_count=len(races),
        distinct_observation_count=len(observations),
        runner_observation_count=len(runner_observations),
        brier_score=sum(squared_errors) / len(squared_errors) if squared_errors else None,
        bands=bands,
        market_comparisons=market_comparisons,
        market_comparison_status=market_status,
        market_comparison_reason=market_reason,
        uncertainty_status="not_estimated",
        uncertainty_interval=None,
        uncertainty_reason=UNCERTAINTY_REASON,
    )


def _market_comparisons(
    model_rows: list[PredictionEvaluationRow],
    all_rows: list[PredictionEvaluationRow],
    all_runs: list[PredictionRunEvaluation],
    run_filter: EvaluationFilters,
) -> list[MarketComparison]:
    market_runs = [
        run for run in all_runs
        if run["prediction_kind"] == "market_baseline" and _matches_shared_run_filters(run, run_filter)
    ]
    baseline_versions = sorted({run["model_version"] for run in market_runs})
    if not baseline_versions:
        return []
    comparisons: list[MarketComparison] = []
    for baseline_version in baseline_versions:
        raw_runs = _unique_runs([
            run for run in market_runs if run["model_version"] == baseline_version
        ])
        eligible_runs = [
            run for run in raw_runs
            if run["status"] == "active" and run["official_evaluation_eligible"]
        ]
        capable_runs = [run for run in eligible_runs if run["has_win"]]
        selected_runs = _select_whole_runs(capable_runs)
        selected_ids = {run["prediction_run_id"] for run in selected_runs}
        selected_rows = _unique_observations([
            row for row in all_rows
            if row["prediction_run_id"] in selected_ids
            and row["bet_type"] == "win"
            and _matches_observation_filters(row, run_filter)
        ])
        model_by_key = {_comparison_key(row): row for row in model_rows}
        market_by_key = {_comparison_key(row): row for row in selected_rows}
        common_keys = sorted(model_by_key.keys() & market_by_key.keys())
        model_errors = [
            (model_by_key[key]["predicted_probability"] - model_by_key[key]["outcome"]) ** 2
            for key in common_keys
        ]
        market_errors = [
            (market_by_key[key]["predicted_probability"] - market_by_key[key]["outcome"]) ** 2
            for key in common_keys
        ]
        common_model_rows = [model_by_key[key] for key in common_keys]
        status: Literal["available", "no_baseline", "no_common_observations"]
        if not capable_runs:
            status = "no_baseline"
        elif common_keys:
            status = "available"
        else:
            status = "no_common_observations"
        if status == "available":
            reason = None
        elif status == "no_baseline":
            reason = "有効な単勝出力可能な市場基準runがありません。"
        else:
            reason = "市場基準と完全一致するrunner-observationがありません。"
        comparisons.append(MarketComparison(
            baseline_version=baseline_version,
            status=status,
            reason=reason,
            model_brier_score=sum(model_errors) / len(model_errors) if model_errors else None,
            market_brier_score=sum(market_errors) / len(market_errors) if market_errors else None,
            brier_difference=(
                (sum(model_errors) - sum(market_errors)) / len(common_keys) if common_keys else None
            ),
            matched_runner_observation_count=len(common_keys),
            matched_race_count=len({row["race_id"] for row in common_model_rows}),
            matched_observation_count=len({
                (row["race_id"], row["input_snapshot_id"]) for row in common_model_rows
            }),
            raw_unique_run_count=len(raw_runs),
            eligible_candidate_run_count=len(eligible_runs),
            capability_qualified_run_count=len(capable_runs),
            selected_run_count=len(selected_runs),
            ineligible_excluded_run_count=len(raw_runs) - len(eligible_runs),
            unsupported_bet_excluded_run_count=len(eligible_runs) - len(capable_runs),
            duplicate_excluded_run_count=len(capable_runs) - len(selected_runs),
        ))
    return comparisons


def _calibration_bands(rows: list[PredictionEvaluationRow]) -> list[CalibrationBand]:
    grouped: list[list[PredictionEvaluationRow]] = [[] for _ in range(10)]
    for row in rows:
        band_index = min(int(row["predicted_probability"] * 10), 9)
        grouped[band_index].append(row)
    bands: list[CalibrationBand] = []
    for index, band_rows in enumerate(grouped):
        count = len(band_rows)
        bands.append(CalibrationBand(
            lower_bound=index / 10,
            upper_bound=(index + 1) / 10,
            count=count,
            average_predicted_probability=(
                sum(row["predicted_probability"] for row in band_rows) / count if count else None
            ),
            actual_win_rate=(sum(row["outcome"] for row in band_rows) / count if count else None),
        ))
    return bands


def _legacy_calibration(group: CalibrationGroup) -> CalibrationSummary:
    return CalibrationSummary(
        eligible_prediction_runs=group.eligible_prediction_runs,
        excluded_prediction_runs=group.excluded_prediction_runs,
        runner_count=group.runner_observation_count,
        brier_score=group.brier_score,
        bands=[ProbabilityBand(
            lower_bound=band.lower_bound, upper_bound=band.upper_bound,
            count=band.count, average_predicted_probability=band.average_predicted_probability,
            actual_win_rate=band.actual_win_rate, small_sample=band.count < 30,
        ) for band in group.bands],
    )


def _unique_runs(runs: list[PredictionRunEvaluation]) -> list[PredictionRunEvaluation]:
    return list({run["prediction_run_id"]: run for run in runs}.values())


def _supports_bet(run: PredictionRunEvaluation, bet_type: Literal["win", "place"]) -> bool:
    return run["has_win"] if bet_type == "win" else run["has_place"]


def _select_whole_runs(runs: list[PredictionRunEvaluation]) -> list[PredictionRunEvaluation]:
    selected: dict[tuple[int, int], PredictionRunEvaluation] = {}
    for run in sorted(runs, key=_run_order, reverse=True):
        selected.setdefault((run["race_id"], run["input_snapshot_id"]), run)
    return sorted(selected.values(), key=lambda run: run["prediction_run_id"])


def _run_order(run: PredictionRunEvaluation) -> tuple[datetime, int]:
    frozen_at = datetime.fromisoformat(run["frozen_at"].replace("Z", "+00:00"))
    if frozen_at.tzinfo is None:
        frozen_at = frozen_at.replace(tzinfo=timezone.utc)
    return frozen_at.astimezone(timezone.utc), run["prediction_run_id"]


def _unique_observations(
    rows: list[PredictionEvaluationRow],
) -> list[PredictionEvaluationRow]:
    unique: dict[tuple[int, int, int], PredictionEvaluationRow] = {}
    for row in rows:
        unique[(row["prediction_run_id"], row["horse_number"], row["active_result_version_id"])] = row
    return list(unique.values())


def _comparison_key(row: PredictionEvaluationRow) -> tuple[int, int, int, int]:
    return (
        row["race_id"], row["input_snapshot_id"], row["horse_number"],
        row["active_result_version_id"],
    )


def _matches_run_filters(row: EvaluationRow | PredictionRunEvaluation, filters: EvaluationFilters) -> bool:
    if filters.model_identifier is not None and row["model_identifier"] != filters.model_identifier:
        return False
    if filters.model_version is not None and row["model_version"] != filters.model_version:
        return False
    return _matches_shared_run_filters(row, filters)


def _matches_shared_run_filters(
    row: EvaluationRow | PredictionRunEvaluation, filters: EvaluationFilters,
) -> bool:
    if filters.racecourse is not None and row["racecourse"] != filters.racecourse:
        return False
    if filters.prediction_frozen_from is not None or filters.prediction_frozen_to is not None:
        frozen_value = row["frozen_at"]
        if frozen_value is None:
            return False
        frozen_at = datetime.fromisoformat(str(frozen_value).replace("Z", "+00:00"))
        if frozen_at.tzinfo is None:
            frozen_at = frozen_at.replace(tzinfo=timezone.utc)
        if filters.prediction_frozen_from is not None and frozen_at < filters.prediction_frozen_from:
            return False
        if filters.prediction_frozen_to is not None and frozen_at > filters.prediction_frozen_to:
            return False
    return True


def _matches_observation_filters(row: EvaluationRow, filters: EvaluationFilters) -> bool:
    if filters.tag_rule_key is not None:
        expected_tag = (filters.tag_rule_key, filters.tag_version)
        if not any(
            rule_key == expected_tag[0]
            and (expected_tag[1] is None or version == expected_tag[1])
            for rule_key, version in row["tags"]
        ):
            return False
    odds_value = row["odds_value"]
    if filters.odds_min is not None or filters.odds_max is not None:
        if odds_value is None:
            return False
        if filters.odds_min is not None and odds_value < filters.odds_min:
            return False
        if filters.odds_max is not None and odds_value > filters.odds_max:
            return False
    popularity = row["popularity"]
    if filters.popularity_min is not None or filters.popularity_max is not None:
        if popularity is None:
            return False
        if filters.popularity_min is not None and popularity < filters.popularity_min:
            return False
        if filters.popularity_max is not None and popularity > filters.popularity_max:
            return False
    return True


def _matches_filters(row: EvaluationRow, filters: EvaluationFilters) -> bool:
    if filters.bet_type is not None and row["bet_type"] != filters.bet_type:
        return False
    return _matches_run_filters(row, filters) and _matches_observation_filters(row, filters)
