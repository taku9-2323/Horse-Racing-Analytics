from datetime import datetime, timezone
from math import isclose
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.analysis_tags import AppliedAnalysisTag, applied_tag_response


def utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("タイムゾーン付き日時が必要です。")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class SnapshotRunnerInput(BaseModel):
    horse_number: int = Field(gt=0)
    win_odds: float = Field(gt=0)
    place_odds_min: float = Field(gt=0)
    place_odds_max: float = Field(gt=0)

    @model_validator(mode="after")
    def validate_place_range(self) -> "SnapshotRunnerInput":
        if self.place_odds_min > self.place_odds_max:
            raise ValueError("複勝下限オッズは上限以下にしてください。")
        return self


class SnapshotCreate(BaseModel):
    observed_at: datetime
    source: str = Field(min_length=1)
    runners: list[SnapshotRunnerInput] = Field(min_length=1)


class SnapshotRunner(BaseModel):
    horse_number: int
    win_odds: float
    place_odds_min: float
    place_odds_max: float


class OddsSnapshot(BaseModel):
    id: int
    race_id: int
    observed_at: str | None
    received_at: str
    source: str
    runners: list[SnapshotRunner]


class FreezeRequest(BaseModel):
    model_identifier: str = Field(min_length=1)
    model_version: str = Field(min_length=1)


class IndependentRunnerOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    horse_number: int = Field(gt=0)
    win_probability: float | None = Field(default=None, ge=0, le=1)
    place_probability: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def require_probability(self) -> "IndependentRunnerOutput":
        if self.win_probability is None and self.place_probability is None:
            raise ValueError("単勝確率または複勝確率を指定してください。")
        return self


class IndependentPredictionFreezeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_identifier: str = Field(min_length=1)
    model_version: str = Field(min_length=1)
    prediction_as_of: datetime
    rationale: str = Field(min_length=1)
    runners: list[IndependentRunnerOutput] = Field(min_length=1)

    @field_validator("model_identifier", "model_version", "rationale")
    @classmethod
    def text_must_not_be_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("空白だけの値は指定できません。")
        return stripped

    @model_validator(mode="after")
    def validate_output_capabilities(self) -> "IndependentPredictionFreezeRequest":
        if self.model_identifier == "market-baseline":
            raise ValueError("市場基準は独立予測モデルとして登録できません。")
        has_win = self.runners[0].win_probability is not None
        has_place = self.runners[0].place_probability is not None
        if any((runner.win_probability is not None) != has_win for runner in self.runners):
            raise ValueError("単勝確率は全出走馬に対して同じ契約で返してください。")
        if any((runner.place_probability is not None) != has_place for runner in self.runners):
            raise ValueError("複勝確率は全出走馬に対して同じ契約で返してください。")
        if has_win and not isclose(
            sum(runner.win_probability or 0 for runner in self.runners), 1.0, abs_tol=1e-6
        ):
            raise ValueError("単勝確率の合計は1にしてください。")
        return self

    @property
    def output_capabilities(self) -> list[Literal["win", "place"]]:
        capabilities: list[Literal["win", "place"]] = []
        if self.runners[0].win_probability is not None:
            capabilities.append("win")
        if self.runners[0].place_probability is not None:
            capabilities.append("place")
        return capabilities


class CorrectionRequest(BaseModel):
    reason: str = Field(min_length=1)
    input_snapshot_id: int

    @field_validator("reason")
    @classmethod
    def reason_must_not_be_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("訂正理由を入力してください。")
        return stripped


class MarketRunnerPrediction(BaseModel):
    horse_number: int
    raw_inverse_win_odds: float
    win_market_share: float


class IndependentRunnerPrediction(BaseModel):
    horse_number: int
    win_probability: float | None
    place_probability: float | None


class PredictionRun(BaseModel):
    id: int
    race_id: int
    input_snapshot_id: int
    prediction_kind: Literal["market_baseline", "independent"]
    model_identifier: str
    model_version: str
    prediction_as_of: str
    rationale: str
    output_capabilities: list[Literal["win", "place"]]
    frozen_at: str
    status: str
    invalidation_reason: str | None
    replaces_prediction_id: int | None
    official_evaluation_eligible: bool
    evaluation_exclusion_reason: str | None
    runners: list[MarketRunnerPrediction | IndependentRunnerPrediction]
    analysis_tags: list[AppliedAnalysisTag]


def snapshot_response(snapshot: Any, runners: list[Any]) -> OddsSnapshot:
    return OddsSnapshot(
        id=int(snapshot["id"]), race_id=int(snapshot["race_id"]),
        observed_at=None if snapshot["observed_at"] is None else str(snapshot["observed_at"]),
        received_at=str(snapshot["received_at"]),
        source=str(snapshot["source"]),
        runners=[SnapshotRunner(
            horse_number=int(runner["horse_number"]), win_odds=float(runner["win_odds"]),
            place_odds_min=float(runner["place_odds_min"]),
            place_odds_max=float(runner["place_odds_max"]),
        ) for runner in runners],
    )


def prediction_response(prediction: Any, runners: list[Any], tags: list[Any]) -> PredictionRun:
    prediction_kind = cast(
        Literal["market_baseline", "independent"], str(prediction["prediction_kind"])
    )
    output_capabilities: list[Literal["win", "place"]] = []
    if prediction_kind == "market_baseline":
        output_capabilities.append("win")
    else:
        if runners and runners[0]["win_probability"] is not None:
            output_capabilities.append("win")
        if runners and runners[0]["place_probability"] is not None:
            output_capabilities.append("place")
    return PredictionRun(
        id=int(prediction["id"]), race_id=int(prediction["race_id"]),
        input_snapshot_id=int(prediction["input_snapshot_id"]),
        prediction_kind=prediction_kind,
        model_identifier=str(prediction["model_identifier"]),
        model_version=str(prediction["model_version"]),
        prediction_as_of=str(prediction["prediction_as_of"]),
        rationale=str(prediction["rationale"]),
        output_capabilities=output_capabilities,
        frozen_at=str(prediction["frozen_at"]),
        status=str(prediction["status"]), invalidation_reason=prediction["invalidation_reason"],
        replaces_prediction_id=prediction["replaces_prediction_id"],
        official_evaluation_eligible=bool(prediction["official_evaluation_eligible"]),
        evaluation_exclusion_reason=prediction["evaluation_exclusion_reason"],
        runners=(
            [MarketRunnerPrediction(
                horse_number=int(runner["horse_number"]),
                raw_inverse_win_odds=float(runner["raw_inverse_win_odds"]),
                win_market_share=float(runner["win_market_share"]),
            ) for runner in runners]
            if prediction_kind == "market_baseline"
            else [IndependentRunnerPrediction(
                horse_number=int(runner["horse_number"]),
                win_probability=(None if runner["win_probability"] is None else float(runner["win_probability"])),
                place_probability=(None if runner["place_probability"] is None else float(runner["place_probability"])),
            ) for runner in runners]
        ),
        analysis_tags=[applied_tag_response(tag) for tag in tags],
    )
