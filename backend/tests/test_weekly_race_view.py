from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from fastapi.testclient import TestClient

from app.main import create_app


SAMPLE_CSV = (Path(__file__).parents[2] / "examples" / "sample-race.csv").read_text(encoding="utf-8")


def current_week_csv() -> bytes:
    return SAMPLE_CSV.replace("2026-08-30", "2026-09-05").replace(
        "2026-09-05T06:40:00Z", "2026-09-05T06:40:00Z",
    ).encode("utf-8")


def seed_ready_week(database_path: Path, race_id: int, snapshot_id: int, judgement_id: int) -> None:
    with sqlite3.connect(database_path) as connection:
        run_id = connection.execute(
            """INSERT INTO meeting_week_runs
            (week_start,week_end,started_at,completed_at,status,target_count,processed_count,
             ready_count,waiting_count,failed_count)
            VALUES ('2026-08-31','2026-09-06','2026-09-05T05:00:00Z','2026-09-05T05:01:00Z',
                    'completed',1,1,1,0,0)""",
        ).lastrowid
        assert run_id is not None
        connection.execute(
            """INSERT INTO meeting_week_races
            (run_id,week_start,race_date,racecourse,meeting_number,meeting_day,race_number,
             race_name,start_time,surface,distance_m,condition_text,source_url,state,
             race_id,snapshot_id,judgement_id,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'ready',?,?,?,?)""",
            (run_id, "2026-08-31", "2026-09-05", "東京", 4, 1, 11, "架空記念",
             "15:40", "芝", 2000, "3歳以上", "https://www.jra.go.jp/program", race_id,
             snapshot_id, judgement_id, "2026-09-05T05:01:00Z"),
        )


def test_current_week_exposes_attention_ratio_and_distinct_display_state(tmp_path: Path) -> None:
    database_path = tmp_path / "weekly-view.sqlite3"
    app = create_app(
        database_path,
        now_provider=lambda: datetime(2026, 9, 5, 5, 5, tzinfo=timezone.utc),
    )
    with TestClient(app) as client:
        race = client.post(
            "/api/races/import", content=current_week_csv(),
            headers={"Content-Type": "text/csv; charset=utf-8"},
        ).json()
        snapshot = client.post(f"/api/races/{race['race_id']}/odds-snapshots", json={
            "observed_at": "2026-09-05T05:00:00Z", "source": "test",
            "runners": [{
                "horse_number": runner["horse_number"], "win_odds": runner["win_odds"],
                "place_odds_min": runner["place_odds_min"],
                "place_odds_max": runner["place_odds_max"],
            } for runner in race["runners"]],
        }).json()
        rule = client.get("/api/rule-versions").json()[-1]
        judgement = client.post(f"/api/races/{race['race_id']}/rule-judgements/freeze", json={
            "snapshot_id": snapshot["id"], "rule_version_id": rule["id"],
            "judgement_as_of": "2026-09-05T05:00:00Z",
        }).json()
        seed_ready_week(database_path, race["race_id"], snapshot["id"], judgement["id"])

        response = client.get("/api/acquisition/jra/meeting-weeks/current")

    assert response.status_code == 200
    item = response.json()["races"][0]
    assert item["attention_horse_count"] == 2
    assert item["judged_runner_count"] == 5
    assert item["attention_ratio"] == 0.4
    assert item["attention_level"] == "medium"
    assert item["display_state"] == "attention"
    assert item["rule_version_id"] == rule["id"]
    assert item["has_started"] is False


def test_weekly_decision_view_uses_the_fixed_snapshot_and_judgement(tmp_path: Path) -> None:
    database_path = tmp_path / "weekly-detail.sqlite3"
    app = create_app(
        database_path,
        now_provider=lambda: datetime(2026, 9, 5, 5, 5, tzinfo=timezone.utc),
    )
    with TestClient(app) as client:
        race = client.post(
            "/api/races/import", content=current_week_csv(),
            headers={"Content-Type": "text/csv; charset=utf-8"},
        ).json()
        snapshot = client.post(f"/api/races/{race['race_id']}/odds-snapshots", json={
            "observed_at": "2026-09-05T05:00:00Z", "source": "test",
            "runners": [{
                "horse_number": runner["horse_number"], "win_odds": runner["win_odds"],
                "place_odds_min": runner["place_odds_min"],
                "place_odds_max": runner["place_odds_max"],
            } for runner in race["runners"]],
        }).json()
        rule = client.get("/api/rule-versions").json()[-1]
        judgement = client.post(f"/api/races/{race['race_id']}/rule-judgements/freeze", json={
            "snapshot_id": snapshot["id"], "rule_version_id": rule["id"],
            "judgement_as_of": "2026-09-05T05:00:00Z",
        }).json()

        response = client.get(
            f"/api/races/{race['race_id']}/weekly-decision-view",
            params={"snapshot_id": snapshot["id"], "judgement_id": judgement["id"]},
        )

    assert response.status_code == 200
    detail = response.json()
    assert detail["race"]["racecourse"] == "東京"
    assert detail["observed_at"] == "2026-09-05T05:00:00Z"
    assert detail["judgement_frozen_at"] == "2026-09-05T05:05:00Z"
    assert detail["attention_horse_count"] == 2
    assert detail["runners"][0] == {
        "horse_number": 1, "horse_name": "アカツキ", "win_odds": 2.0,
        "place_odds_min": 1.2, "place_odds_max": 1.5, "market_rank": 1,
        "normalized_win_market_share": detail["runners"][0]["normalized_win_market_share"],
        "rule_judgement": "注目", "rule_reason": "市場順位が2位以内 / 単勝オッズが10.0以下",
        "missing_reasons": [],
    }
    assert detail["disclaimer"] == "注目段階はルール該当率です。期待値、回収率、購入推奨、利益優位性を示しません。"
