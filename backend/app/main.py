from os import environ
from pathlib import Path
import sqlite3
from datetime import datetime, timezone
from typing import Callable, Literal

from fastapi import Body, FastAPI, HTTPException, Response
from pydantic import BaseModel
from starlette.staticfiles import StaticFiles

from app.database import RaceImportConflictError, SqliteDatabase
from app.analysis_tags import (
    AnalysisTag, AnalysisTagConditionError, TagAuditEvent, TagStateChange, TagVersionCreate,
    analysis_tag_response, audit_event_response,
)
from app.race_analysis import CsvValidationError, RaceAnalysis, build_analysis, parse_race_csv
from app.predictions import (
    CorrectionRequest, FreezeRequest, OddsSnapshot, PredictionRun, SnapshotCreate,
    prediction_response, snapshot_response, utc_iso,
)


class ComponentHealth(BaseModel):
    status: Literal["ok"]


class DatabaseHealth(BaseModel):
    status: Literal["ok", "error"]
    engine: Literal["sqlite"]


class HealthResponse(BaseModel):
    service: str
    status: Literal["ok", "degraded"]
    api: ComponentHealth
    database: DatabaseHealth


def default_database_path() -> Path:
    configured_data_dir = environ.get("HRA_DATA_DIR")
    if configured_data_dir:
        data_dir = Path(configured_data_dir)
    else:
        data_dir = Path(__file__).resolve().parents[2] / ".local"
    return data_dir / "horse-racing-analytics.sqlite3"


def default_frontend_dist_path() -> Path:
    return Path(__file__).resolve().parents[2] / "frontend" / "dist"


def create_app(
    database_path: Path | None = None,
    frontend_dist_path: Path | None = None,
    now_provider: Callable[[], datetime] | None = None,
) -> FastAPI:
    database = SqliteDatabase(database_path or default_database_path())
    database_initialization_error: Exception | None = None
    try:
        database.initialize()
    except (OSError, sqlite3.Error, RuntimeError) as error:
        database_initialization_error = error

    app = FastAPI(title="Horse Racing Analytics API", version="0.1.0")
    current_time = now_provider or (lambda: datetime.now(timezone.utc))

    @app.get("/api/health", response_model=HealthResponse)
    def health(response: Response) -> HealthResponse:
        try:
            if database_initialization_error is not None:
                raise database_initialization_error
            database.check()
        except (OSError, sqlite3.Error, RuntimeError):
            response.status_code = 503
            return HealthResponse(
                service="Horse Racing Analytics",
                status="degraded",
                api=ComponentHealth(status="ok"),
                database=DatabaseHealth(status="error", engine="sqlite"),
            )

        return HealthResponse(
            service="Horse Racing Analytics",
            status="ok",
            api=ComponentHealth(status="ok"),
            database=DatabaseHealth(status="ok", engine="sqlite"),
        )

    @app.post("/api/races/import", response_model=RaceAnalysis, status_code=201)
    def import_race(response: Response, csv_content: bytes = Body(media_type="text/csv")) -> RaceAnalysis:
        try:
            race, runners = parse_race_csv(csv_content)
            race_id, created = database.import_race(race, runners)
        except CsvValidationError as error:
            raise HTTPException(status_code=422, detail=error.detail()) from error
        except RaceImportConflictError as error:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "race_import_conflict",
                    "message": "同じレースに異なる内容がすでに登録されています。",
                },
            ) from error
        if not created:
            response.status_code = 200
        stored = database.get_race(race_id)
        if stored is None:
            raise HTTPException(status_code=500, detail="保存したレースを読み込めません。")
        return build_analysis(race_id, *stored)

    @app.get("/api/races/{race_id}", response_model=RaceAnalysis)
    def get_race(race_id: int) -> RaceAnalysis:
        stored = database.get_race(race_id)
        if stored is None:
            raise HTTPException(status_code=404, detail="レースが見つかりません。")
        return build_analysis(race_id, *stored)

    @app.post("/api/races/{race_id}/odds-snapshots", response_model=OddsSnapshot, status_code=201)
    def create_snapshot(race_id: int, request: SnapshotCreate) -> OddsSnapshot:
        try:
            snapshot_id = database.create_odds_snapshot(
                race_id, utc_iso(request.observed_at), utc_iso(current_time()), request.source,
                [runner.model_dump() for runner in request.runners],
            )
        except LookupError as error:
            raise HTTPException(status_code=404, detail={"code": str(error), "message": "レースが見つかりません。"}) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail={"code": str(error), "message": "全出走馬のオッズを1件ずつ指定してください。"}) from error
        stored = database.get_odds_snapshot(snapshot_id)
        if stored is None:
            raise HTTPException(status_code=500, detail="保存したオッズ時点を読み込めません。")
        return snapshot_response(*stored)

    @app.get("/api/races/{race_id}/odds-snapshots", response_model=list[OddsSnapshot])
    def list_snapshots(race_id: int) -> list[OddsSnapshot]:
        return [snapshot_response(*stored) for stored in database.list_odds_snapshots(race_id)]

    @app.post("/api/odds-snapshots/{snapshot_id}/freeze", response_model=PredictionRun, status_code=201)
    def freeze_prediction(snapshot_id: int, request: FreezeRequest) -> PredictionRun:
        if request.model_identifier != "market-baseline":
            raise HTTPException(status_code=422, detail={"code": "unsupported_model", "message": "現在固定できるのは市場基準だけです。"})
        try:
            prediction_id = database.create_prediction(
                snapshot_id, request.model_identifier, request.model_version,
                utc_iso(current_time()),
            )
        except LookupError as error:
            raise HTTPException(status_code=404, detail={"code": str(error), "message": "オッズ時点が見つかりません。"}) from error
        stored = database.get_prediction(prediction_id)
        if stored is None:
            raise HTTPException(status_code=500, detail="固定予測を読み込めません。")
        return prediction_response(*stored)

    @app.put("/api/predictions/{prediction_id}")
    def reject_prediction_edit(prediction_id: int) -> None:
        if database.get_prediction(prediction_id) is None:
            raise HTTPException(status_code=404, detail={"code": "prediction_not_found", "message": "固定予測が見つかりません。"})
        raise HTTPException(status_code=409, detail={"code": "frozen_prediction_immutable", "message": "固定済み予測は直接編集できません。"})

    @app.post("/api/predictions/{prediction_id}/correct", response_model=PredictionRun, status_code=201)
    def correct_prediction(prediction_id: int, request: CorrectionRequest) -> PredictionRun:
        original = database.get_prediction(prediction_id)
        if original is None:
            raise HTTPException(status_code=404, detail={"code": "prediction_not_found", "message": "固定予測が見つかりません。"})
        try:
            replacement_id = database.create_prediction(
                request.input_snapshot_id, str(original[0]["model_identifier"]),
                str(original[0]["model_version"]), utc_iso(current_time()),
                replaces_prediction_id=prediction_id, correction_reason=request.reason,
            )
        except LookupError as error:
            raise HTTPException(status_code=404, detail={"code": str(error), "message": "訂正対象が見つかりません。"}) from error
        except ValueError as error:
            raise HTTPException(status_code=409, detail={"code": str(error), "message": "この予測は訂正できません。"}) from error
        stored = database.get_prediction(replacement_id)
        if stored is None:
            raise HTTPException(status_code=500, detail="訂正版を読み込めません。")
        return prediction_response(*stored)

    @app.get("/api/races/{race_id}/predictions", response_model=list[PredictionRun])
    def list_prediction_runs(race_id: int) -> list[PredictionRun]:
        return [prediction_response(*stored) for stored in database.list_predictions(race_id)]

    @app.get("/api/analysis-tags", response_model=list[AnalysisTag])
    def list_analysis_tags() -> list[AnalysisTag]:
        return [analysis_tag_response(row) for row in database.list_analysis_tags()]

    @app.post("/api/analysis-tags/{tag_id}/state", response_model=AnalysisTag)
    def change_analysis_tag_state(tag_id: int, request: TagStateChange) -> AnalysisTag:
        try:
            database.set_analysis_tag_state(
                tag_id, request.enabled, request.reason, utc_iso(current_time()),
            )
        except LookupError as error:
            raise HTTPException(status_code=404, detail={"code": str(error), "message": "分析タグが見つかりません。"}) from error
        except ValueError as error:
            raise HTTPException(status_code=409, detail={"code": str(error), "message": "旧版の状態は変更できません。"}) from error
        stored = database.get_analysis_tag(tag_id)
        if stored is None:
            raise HTTPException(status_code=500, detail="更新した分析タグを読み込めません。")
        return analysis_tag_response(stored)

    @app.post("/api/analysis-tags/{tag_id}/versions", response_model=AnalysisTag, status_code=201)
    def create_analysis_tag_version(tag_id: int, request: TagVersionCreate) -> AnalysisTag:
        try:
            new_id = database.create_analysis_tag_version(
                tag_id, request.conditions, request.reason, utc_iso(current_time()),
            )
        except AnalysisTagConditionError as error:
            raise HTTPException(status_code=422, detail={"code": "invalid_tag_conditions", "message": str(error)}) from error
        except LookupError as error:
            raise HTTPException(status_code=404, detail={"code": str(error), "message": "分析タグが見つかりません。"}) from error
        except ValueError as error:
            raise HTTPException(status_code=409, detail={"code": str(error), "message": "旧版から新版は作成できません。"}) from error
        stored = database.get_analysis_tag(new_id)
        if stored is None:
            raise HTTPException(status_code=500, detail="作成した分析タグ版を読み込めません。")
        return analysis_tag_response(stored)

    @app.get("/api/analysis-tags/{rule_key}/versions", response_model=list[AnalysisTag])
    def list_analysis_tag_versions(rule_key: str) -> list[AnalysisTag]:
        return [analysis_tag_response(row) for row in database.list_analysis_tag_versions(rule_key)]

    @app.get("/api/analysis-tags/{rule_key}/audit", response_model=list[TagAuditEvent])
    def list_analysis_tag_audit(rule_key: str) -> list[TagAuditEvent]:
        return [audit_event_response(row) for row in database.list_analysis_tag_audit(rule_key)]

    frontend_dist = frontend_dist_path or default_frontend_dist_path()
    if frontend_dist.is_dir():
        app.mount(
            "/",
            StaticFiles(directory=frontend_dist, html=True),
            name="frontend",
        )

    return app


app = create_app()
