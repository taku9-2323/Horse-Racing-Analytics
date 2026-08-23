from pathlib import Path
import sqlite3

from fastapi.testclient import TestClient

from app.main import create_app


def test_user_can_confirm_api_and_database_are_ready(tmp_path: Path) -> None:
    app = create_app(tmp_path / "health.sqlite3")

    with TestClient(app) as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {
        "service": "Horse Racing Analytics",
        "status": "ok",
        "api": {"status": "ok"},
        "database": {"status": "ok", "engine": "sqlite"},
    }


def test_user_can_open_the_built_react_application(tmp_path: Path) -> None:
    frontend_dist = tmp_path / "dist"
    frontend_dist.mkdir()
    (frontend_dist / "index.html").write_text(
        "<html><title>Horse Racing Analytics</title></html>",
        encoding="utf-8",
    )
    app = create_app(tmp_path / "health.sqlite3", frontend_dist)

    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "Horse Racing Analytics" in response.text


def test_user_can_distinguish_database_failure_from_api_failure(tmp_path: Path) -> None:
    database_path = tmp_path / "health.sqlite3"
    app = create_app(database_path)
    with sqlite3.connect(database_path) as connection:
        connection.execute("DROP TABLE application_metadata")

    with TestClient(app) as client:
        response = client.get("/api/health")

    assert response.status_code == 503
    assert response.json() == {
        "service": "Horse Racing Analytics",
        "status": "degraded",
        "api": {"status": "ok"},
        "database": {"status": "error", "engine": "sqlite"},
    }
