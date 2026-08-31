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


class PredictionEvaluationRow(EvaluationRow):
    prediction_run_id: int
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


class CalibrationSummary(BaseModel):
    eligible_prediction_runs: int
    excluded_prediction_runs: int
    runner_count: int
    brier_score: float | None
    bands: list[ProbabilityBand]


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
        if (
            self.popularity_min is not None and self.popularity_max is not None
            and self.popularity_min > self.popularity_max
        ):
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
    calibration: CalibrationSummary
    returns: dict[str, SettlementTotals]


def build_evaluation_report(
    all_prediction_rows: list[PredictionEvaluationRow],
    settlement_rows: list[SettlementEvaluationRow],
    filters: EvaluationFilters,
) -> EvaluationReport:
    eligible_rows = [row for row in all_prediction_rows if bool(row["eligible"])]
    prediction_rows = [row for row in eligible_rows if _matches_filters(row, filters)]
    excluded_prediction_runs = len({
        int(row["prediction_run_id"])
        for row in all_prediction_rows
        if not bool(row["eligible"]) and _matches_filters(row, filters)
    })
    filtered_settlements = [row for row in settlement_rows if _matches_filters(row, filters)]
    band_rows: list[list[PredictionEvaluationRow]] = [[] for _ in range(10)]
    squared_errors: list[float] = []
    prediction_run_ids: set[int] = set()
    for row in prediction_rows:
        probability = float(row["predicted_probability"])
        outcome = int(row["outcome"])
        prediction_run_ids.add(int(row["prediction_run_id"]))
        squared_errors.append((probability - outcome) ** 2)
        band_rows[min(int(probability * 10), 9)].append(row)

    bands = []
    for index, rows in enumerate(band_rows):
        count = len(rows)
        bands.append(ProbabilityBand(
            lower_bound=index / 10,
            upper_bound=(index + 1) / 10,
            count=count,
            average_predicted_probability=(
                sum(float(row["predicted_probability"]) for row in rows) / count if count else None
            ),
            actual_win_rate=(sum(int(row["outcome"]) for row in rows) / count if count else None),
            small_sample=count < 30,
        ))

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
            racecourses=sorted({
                row["racecourse"] for row in option_rows
            }),
            bet_types=["place", "win"],
        ),
        calibration=CalibrationSummary(
            eligible_prediction_runs=len(prediction_run_ids),
            excluded_prediction_runs=excluded_prediction_runs,
            runner_count=len(prediction_rows),
            brier_score=(sum(squared_errors) / len(squared_errors) if squared_errors else None),
            bands=bands,
        ),
        returns={
            decision_type: totals_for([
                row for row in filtered_settlements if str(row["decision_type"]) == decision_type
            ])
            for decision_type in ("candidate", "discretionary")
        },
    )


def _matches_filters(row: EvaluationRow, filters: EvaluationFilters) -> bool:
    if filters.model_identifier is not None and row["model_identifier"] != filters.model_identifier:
        return False
    if filters.model_version is not None and row["model_version"] != filters.model_version:
        return False
    if filters.racecourse is not None and row["racecourse"] != filters.racecourse:
        return False
    if filters.bet_type is not None and row["bet_type"] != filters.bet_type:
        return False
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
    if filters.prediction_frozen_from is not None or filters.prediction_frozen_to is not None:
        if row["frozen_at"] is None:
            return False
        frozen_at = datetime.fromisoformat(str(row["frozen_at"]).replace("Z", "+00:00"))
        if frozen_at.tzinfo is None:
            frozen_at = frozen_at.replace(tzinfo=timezone.utc)
        if filters.prediction_frozen_from is not None and frozen_at < filters.prediction_frozen_from:
            return False
        if filters.prediction_frozen_to is not None and frozen_at > filters.prediction_frozen_to:
            return False
    return True
