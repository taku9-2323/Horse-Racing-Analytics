from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


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
    observed_at: str
    received_at: str
    source: str
    runners: list[SnapshotRunner]


class FreezeRequest(BaseModel):
    model_identifier: str = Field(min_length=1)
    model_version: str = Field(min_length=1)


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


class RunnerPrediction(BaseModel):
    horse_number: int
    raw_inverse_win_odds: float
    win_market_share: float


class PredictionRun(BaseModel):
    id: int
    race_id: int
    input_snapshot_id: int
    model_identifier: str
    model_version: str
    frozen_at: str
    status: str
    invalidation_reason: str | None
    replaces_prediction_id: int | None
    official_evaluation_eligible: bool
    evaluation_exclusion_reason: str | None
    runners: list[RunnerPrediction]


def snapshot_response(snapshot: Any, runners: list[Any]) -> OddsSnapshot:
    return OddsSnapshot(
        id=int(snapshot["id"]), race_id=int(snapshot["race_id"]),
        observed_at=str(snapshot["observed_at"]), received_at=str(snapshot["received_at"]),
        source=str(snapshot["source"]),
        runners=[SnapshotRunner(
            horse_number=int(runner["horse_number"]), win_odds=float(runner["win_odds"]),
            place_odds_min=float(runner["place_odds_min"]),
            place_odds_max=float(runner["place_odds_max"]),
        ) for runner in runners],
    )


def prediction_response(prediction: Any, runners: list[Any]) -> PredictionRun:
    return PredictionRun(
        id=int(prediction["id"]), race_id=int(prediction["race_id"]),
        input_snapshot_id=int(prediction["input_snapshot_id"]),
        model_identifier=str(prediction["model_identifier"]),
        model_version=str(prediction["model_version"]), frozen_at=str(prediction["frozen_at"]),
        status=str(prediction["status"]), invalidation_reason=prediction["invalidation_reason"],
        replaces_prediction_id=prediction["replaces_prediction_id"],
        official_evaluation_eligible=bool(prediction["official_evaluation_eligible"]),
        evaluation_exclusion_reason=prediction["evaluation_exclusion_reason"],
        runners=[RunnerPrediction(
            horse_number=int(runner["horse_number"]),
            raw_inverse_win_odds=float(runner["raw_inverse_win_odds"]),
            win_market_share=float(runner["win_market_share"]),
        ) for runner in runners],
    )
