from os import environ
from pathlib import Path
import sqlite3
from typing import Literal

from fastapi import FastAPI, Response
from pydantic import BaseModel
from starlette.staticfiles import StaticFiles

from app.database import SqliteDatabase


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
) -> FastAPI:
    database = SqliteDatabase(database_path or default_database_path())
    database_initialization_error: Exception | None = None
    try:
        database.initialize()
    except (OSError, sqlite3.Error, RuntimeError) as error:
        database_initialization_error = error

    app = FastAPI(title="Horse Racing Analytics API", version="0.1.0")

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

    frontend_dist = frontend_dist_path or default_frontend_dist_path()
    if frontend_dist.is_dir():
        app.mount(
            "/",
            StaticFiles(directory=frontend_dist, html=True),
            name="frontend",
        )

    return app


app = create_app()
