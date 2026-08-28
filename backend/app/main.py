from collections.abc import Iterator
from os import environ
from pathlib import Path
import sqlite3
from threading import Lock
from datetime import datetime, timezone
from typing import Callable, Literal

from fastapi import Body, Depends, FastAPI, HTTPException, Query, Response
from pydantic import BaseModel
from starlette.staticfiles import StaticFiles

from app.database import RaceImportConflictError, SqliteDatabase
from app.data_maintenance import (
    BackupNotFoundError, BackupSummary, DataMaintenance, InvalidBackupError,
    RestoreSummary,
)
from app.bets import (
    Bet, BetCreate, RaceLedger, ResultCsvValidationError, ResultVersion,
    bet_response, parse_results_csv, pending_totals_for, result_response,
    settlement_response, totals_for,
)
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
    resolved_database_path = database_path or default_database_path()
    database = SqliteDatabase(resolved_database_path)
    database_initialization_error: Exception | None = None
    try:
        database.initialize()
    except (OSError, sqlite3.Error, RuntimeError) as error:
        database_initialization_error = error

    database_access_lock = Lock()

    def serialized_database_access() -> Iterator[None]:
        with database_access_lock:
            yield

    app = FastAPI(
        title="Horse Racing Analytics API",
        version="0.1.0",
        dependencies=[Depends(serialized_database_access)],
    )
    current_time = now_provider or (lambda: datetime.now(timezone.utc))
    data_maintenance = DataMaintenance(resolved_database_path, current_time)

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

    @app.post("/api/races/{race_id}/bets", response_model=Bet, status_code=201)
    def create_bet(race_id: int, request: BetCreate) -> Bet:
        try:
            bet_id = database.create_bet(
                race_id, request.horse_number, request.bet_type,
                request.decision_type, request.amount_yen, utc_iso(current_time()),
            )
        except LookupError as error:
            raise HTTPException(status_code=404, detail={"code": str(error), "message": "購入対象の出走馬が見つかりません。"}) from error
        except ValueError as error:
            messages = {
                "race_already_settled": "結果取込後に購入は追加できません。",
                "place_not_offered": "4頭以下のレースでは複勝を登録できません。",
                "candidate_not_available": "現在は適格な期待値候補がないため、候補内購入を登録できません。",
            }
            raise HTTPException(status_code=409, detail={"code": str(error), "message": messages.get(str(error), "購入を登録できません。")}) from error
        stored = database.get_bet(bet_id)
        if stored is None:
            raise HTTPException(status_code=500, detail="登録した購入を読み込めません。")
        return bet_response(stored)

    def save_results(
        race_id: int, csv_content: bytes, correction_reason: str | None = None,
    ) -> ResultVersion:
        try:
            results = parse_results_csv(
                csv_content, allow_dead_heat=correction_reason is not None,
            )
            result_version_id = database.create_result_version(
                race_id, results, utc_iso(current_time()), correction_reason,
            )
        except ResultCsvValidationError as error:
            raise HTTPException(status_code=422, detail=error.detail()) from error
        except LookupError as error:
            raise HTTPException(status_code=404, detail={"code": str(error), "message": "レースが見つかりません。"}) from error
        except ValueError as error:
            status_code = 409 if str(error) in {"result_import_conflict", "result_not_imported"} else 422
            messages = {
                "result_import_conflict": "取込済み結果と内容が異なります。理由付き訂正を使用してください。",
                "result_not_imported": "訂正元の結果がありません。",
                "result_runner_mismatch": "全出走馬の結果を1件ずつ指定してください。",
                "result_invalid_position": "着順が出走頭数の範囲外です。",
                "result_payout_mismatch": "着順・頭数規則と公式払戻の組み合わせを確認してください。",
            }
            raise HTTPException(status_code=status_code, detail={"code": str(error), "message": messages.get(str(error), "結果を登録できません。")}) from error
        stored = database.get_result_version(result_version_id)
        if stored is None:
            raise HTTPException(status_code=500, detail="登録した結果を読み込めません。")
        return result_response(*stored)

    @app.post("/api/races/{race_id}/results/import", response_model=ResultVersion, status_code=201)
    def import_results(race_id: int, csv_content: bytes = Body(media_type="text/csv")) -> ResultVersion:
        return save_results(race_id, csv_content)

    @app.post("/api/races/{race_id}/results/correct", response_model=ResultVersion, status_code=201)
    def correct_results(
        race_id: int,
        reason: str = Query(min_length=1),
        csv_content: bytes = Body(media_type="text/csv"),
    ) -> ResultVersion:
        if not reason.strip():
            raise HTTPException(status_code=422, detail={"code": "blank_correction_reason", "message": "訂正理由を入力してください。"})
        return save_results(race_id, csv_content, reason.strip())

    @app.get("/api/races/{race_id}/results", response_model=list[ResultVersion])
    def list_results(race_id: int) -> list[ResultVersion]:
        return [result_response(*stored) for stored in database.list_result_versions(race_id)]

    @app.get("/api/races/{race_id}/ledger", response_model=RaceLedger)
    def get_race_ledger(race_id: int) -> RaceLedger:
        if database.get_race(race_id) is None:
            raise HTTPException(status_code=404, detail={"code": "race_not_found", "message": "レースが見つかりません。"})
        bets, result, settlements = database.get_race_ledger(race_id)
        if result is None:
            by_decision_type = {
                decision_type: pending_totals_for([
                    bet for bet in bets if str(bet["decision_type"]) == decision_type
                ])
                for decision_type in ("candidate", "discretionary")
            }
            totals = pending_totals_for(bets)
        else:
            by_decision_type = {
                decision_type: totals_for([
                    settlement for settlement in settlements
                    if str(settlement["decision_type"]) == decision_type
                ])
                for decision_type in ("candidate", "discretionary")
            }
            totals = totals_for(settlements)
        return RaceLedger(
            bets=[bet_response(bet) for bet in bets],
            result_version=None if result is None else result_response(*result),
            settlements=[settlement_response(settlement) for settlement in settlements],
            totals=totals, by_decision_type=by_decision_type,
        )

    @app.post("/api/data/backups", response_model=BackupSummary, status_code=201)
    def create_backup() -> BackupSummary:
        try:
            return data_maintenance.create_backup()
        except (OSError, sqlite3.Error, InvalidBackupError) as error:
            raise HTTPException(
                status_code=500,
                detail={"code": "backup_failed", "message": "バックアップを作成できませんでした。"},
            ) from error

    @app.get("/api/data/backups", response_model=list[BackupSummary])
    def list_backups() -> list[BackupSummary]:
        return data_maintenance.list_backups()

    @app.post("/api/data/backups/{backup_id}/verify", response_model=BackupSummary)
    def verify_backup(backup_id: str) -> BackupSummary:
        try:
            return data_maintenance.verify_backup(backup_id)
        except BackupNotFoundError as error:
            raise HTTPException(
                status_code=404,
                detail={"code": "backup_not_found", "message": "バックアップが見つかりません。"},
            ) from error
        except InvalidBackupError as error:
            raise HTTPException(
                status_code=422,
                detail={"code": "backup_invalid", "message": "バックアップが破損しているか形式が違います。"},
            ) from error

    @app.post("/api/data/backups/{backup_id}/restore", response_model=RestoreSummary)
    def restore_backup(backup_id: str) -> RestoreSummary:
        try:
            return data_maintenance.restore_backup(backup_id)
        except BackupNotFoundError as error:
            raise HTTPException(
                status_code=404,
                detail={"code": "backup_not_found", "message": "バックアップが見つかりません。"},
            ) from error
        except InvalidBackupError as error:
            raise HTTPException(
                status_code=422,
                detail={"code": "backup_invalid", "message": "検証に失敗したバックアップは復元できません。"},
            ) from error
        except (OSError, sqlite3.Error) as error:
            raise HTTPException(
                status_code=500,
                detail={"code": "restore_failed", "message": "復元できませんでした。現在データは変更されていません。"},
            ) from error

    @app.get("/api/data/export")
    def export_data(export_format: Literal["json", "csv"] = Query(alias="format")) -> Response:
        try:
            artifact = data_maintenance.export(export_format)
        except (OSError, sqlite3.Error) as error:
            raise HTTPException(
                status_code=500,
                detail={"code": "export_failed", "message": "データを出力できませんでした。"},
            ) from error
        return Response(
            content=artifact.content,
            media_type=artifact.media_type,
            headers={"Content-Disposition": f'attachment; filename="{artifact.filename}"'},
        )

    frontend_dist = frontend_dist_path or default_frontend_dist_path()
    if frontend_dist.is_dir():
        app.mount(
            "/",
            StaticFiles(directory=frontend_dist, html=True),
            name="frontend",
        )

    return app


app = create_app()
