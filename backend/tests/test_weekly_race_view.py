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
                "horse_number": runner["horse_number"],
                "win_odds": {1: 2.0, 2: 4.0, 3: 20.0, 4: 30.0, 5: 40.0}[runner["horse_number"]],
                "place_odds_min": runner["place_odds_min"],
                "place_odds_max": runner["place_odds_max"],
            } for runner in race["runners"]],
        }).json()
        rule = client.get("/api/rule-versions").json()[-1]
        judgement = client.post(f"/api/races/{race['race_id']}/rule-judgements/freeze", json={
            "snapshot_id": snapshot["id"], "rule_version_id": rule["id"],
            "judgement_as_of": "2026-09-05T05:00:00Z",
        }).json()

        newer_snapshot = client.post(f"/api/races/{race['race_id']}/odds-snapshots", json={
            "observed_at": "2026-09-05T05:01:00Z", "source": "test",
            "runners": [{
                "horse_number": runner["horse_number"],
                "win_odds": {1: 40.0, 2: 30.0, 3: 2.0, 4: 4.0, 5: 20.0}[runner["horse_number"]],
                "place_odds_min": runner["place_odds_min"],
                "place_odds_max": runner["place_odds_max"],
            } for runner in race["runners"]],
        }).json()
        history_before = client.get(f"/api/races/{race['race_id']}/rule-judgements").json()

        response = client.get(
            f"/api/races/{race['race_id']}/weekly-decision-view",
            params={"snapshot_id": snapshot["id"], "judgement_id": judgement["id"]},
        )
        comparison = client.get(f"/api/races/{race['race_id']}/market-rule-comparison", params={
            "snapshot_id": snapshot["id"], "rule_version_id": rule["id"],
        })
        history_after = client.get(f"/api/races/{race['race_id']}/rule-judgements").json()

    assert response.status_code == 200
    detail = response.json()
    assert comparison.status_code == 200
    compare = comparison.json()
    assert detail["race"]["racecourse"] == "東京"
    assert detail["observed_at"] == "2026-09-05T05:00:00Z"
    assert detail["judgement_frozen_at"] == "2026-09-05T05:05:00Z"
    assert detail["attention_horse_count"] == 2
    assert detail["runners"][0] == {
        "horse_number": 1, "horse_name": "アカツキ", "win_odds": 2.0,
        "place_odds_min": 1.2, "place_odds_max": 1.5, "market_rank": 1,
        "normalized_win_market_share": detail["runners"][0]["normalized_win_market_share"],
        "rule_judgement": "注目",
        "rule_reason": "市場順位: 達成（実測値1位 / 条件2位以内） / 単勝オッズ: 達成（実測値2.0 / 条件10.0以下）",
        "rule_conditions": [
            {"field": "market_rank", "operator": "lte", "threshold": 2, "state": "satisfied", "observed_value": 1},
            {"field": "win_odds", "operator": "lte", "threshold": 10.0, "state": "satisfied", "observed_value": 2.0},
        ],
        "missing_reasons": [],
    }
    failed_detail = next(item for item in detail["runners"] if item["horse_number"] == 3)
    failed_comparison = next(item for item in compare["rows"] if item["horse_number"] == 3)
    expected_failed_conditions = [
        {"field": "market_rank", "operator": "lte", "threshold": 2, "state": "failed", "observed_value": 3},
        {"field": "win_odds", "operator": "lte", "threshold": 10.0, "state": "failed", "observed_value": 20.0},
    ]
    assert failed_detail["rule_conditions"] == expected_failed_conditions
    assert failed_detail["rule_judgement"] == "見送り"
    assert "未達" in failed_detail["rule_reason"]
    assert "3位" in failed_detail["rule_reason"] and "20.0" in failed_detail["rule_reason"]
    assert failed_comparison["rule_conditions"] == expected_failed_conditions
    assert failed_comparison["rule_reason"] == failed_detail["rule_reason"]
    assert newer_snapshot["id"] != snapshot["id"]
    assert history_after == history_before
    assert detail["disclaimer"] == "注目段階はルール該当率です。期待値、回収率、購入推奨、利益優位性を示しません。"


def test_incomplete_selected_snapshot_preserves_judgement_and_marks_market_values_unknown(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "weekly-incomplete-snapshot.sqlite3"
    app = create_app(
        database_path,
        now_provider=lambda: datetime(2026, 9, 5, 5, 5, tzinfo=timezone.utc),
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        race = client.post(
            "/api/races/import", content=current_week_csv(),
            headers={"Content-Type": "text/csv; charset=utf-8"},
        ).json()
        snapshot = client.post(f"/api/races/{race['race_id']}/odds-snapshots", json={
            "observed_at": "2026-09-05T05:00:00Z", "source": "test",
            "runners": [{
                "horse_number": runner["horse_number"],
                "win_odds": {1: 2.0, 2: 20.0, 3: 30.0, 4: 40.0, 5: 50.0}[runner["horse_number"]],
                "place_odds_min": runner["place_odds_min"],
                "place_odds_max": runner["place_odds_max"],
            } for runner in race["runners"]],
        }).json()
        rule = client.get("/api/rule-versions").json()[-1]
        judgement = client.post(f"/api/races/{race['race_id']}/rule-judgements/freeze", json={
            "snapshot_id": snapshot["id"], "rule_version_id": rule["id"],
            "judgement_as_of": "2026-09-05T05:00:00Z",
        }).json()
        history_before = client.get(f"/api/races/{race['race_id']}/rule-judgements").json()
        with sqlite3.connect(database_path) as connection:
            connection.execute(
                "DELETE FROM odds_snapshot_runners WHERE snapshot_id = ? AND horse_number = 2",
                (snapshot["id"],),
            )

        weekly = client.get(
            f"/api/races/{race['race_id']}/weekly-decision-view",
            params={"snapshot_id": snapshot["id"], "judgement_id": judgement["id"]},
        )
        comparison = client.get(f"/api/races/{race['race_id']}/market-rule-comparison", params={
            "snapshot_id": snapshot["id"], "rule_version_id": rule["id"],
        })
        history_after = client.get(f"/api/races/{race['race_id']}/rule-judgements").json()

    assert weekly.status_code == 200, weekly.text
    assert comparison.status_code == 200, comparison.text
    detail_runners = weekly.json()["runners"]
    comparison_rows = comparison.json()["rows"]
    assert all(runner["market_rank"] is None for runner in detail_runners)
    assert all(runner["normalized_win_market_share"] is None for runner in detail_runners)
    missing_odds = next(runner for runner in detail_runners if runner["horse_number"] == 2)
    assert missing_odds["win_odds"] is None
    assert missing_odds["rule_conditions"] == [
        {"field": "market_rank", "operator": "lte", "threshold": 2,
         "state": "satisfied", "observed_value": None},
        {"field": "win_odds", "operator": "lte", "threshold": 10.0,
         "state": "failed", "observed_value": None},
    ]
    assert all(row["market_rank"] is None for row in comparison_rows)
    assert all(row["normalized_win_market_share"] is None for row in comparison_rows)
    comparison_missing = next(row for row in comparison_rows if row["horse_number"] == 2)
    assert comparison_missing["rule_conditions"] == missing_odds["rule_conditions"]
    assert "不足" in comparison_missing["market_reason"]
    assert history_after == history_before


def test_older_complete_snapshot_ignores_newer_incomplete_snapshot(tmp_path: Path) -> None:
    database_path = tmp_path / "weekly-old-snapshot.sqlite3"
    app = create_app(
        database_path,
        now_provider=lambda: datetime(2026, 9, 5, 5, 5, tzinfo=timezone.utc),
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        race = client.post(
            "/api/races/import", content=current_week_csv(),
            headers={"Content-Type": "text/csv; charset=utf-8"},
        ).json()
        runners = [{
            "horse_number": runner["horse_number"],
            "win_odds": {1: 2.0, 2: 20.0, 3: 30.0, 4: 40.0, 5: 50.0}[runner["horse_number"]],
            "place_odds_min": runner["place_odds_min"],
            "place_odds_max": runner["place_odds_max"],
        } for runner in race["runners"]]
        snapshot = client.post(f"/api/races/{race['race_id']}/odds-snapshots", json={
            "observed_at": "2026-09-05T05:00:00Z", "source": "test", "runners": runners,
        }).json()
        rule = client.get("/api/rule-versions").json()[-1]
        judgement = client.post(f"/api/races/{race['race_id']}/rule-judgements/freeze", json={
            "snapshot_id": snapshot["id"], "rule_version_id": rule["id"],
            "judgement_as_of": "2026-09-05T05:00:00Z",
        }).json()
        newer = client.post(f"/api/races/{race['race_id']}/odds-snapshots", json={
            "observed_at": "2026-09-05T05:01:00Z", "source": "test",
            "runners": [{**runner, "win_odds": runner["win_odds"] + 100.0} for runner in runners],
        }).json()
        with sqlite3.connect(database_path) as connection:
            connection.execute(
                "DELETE FROM odds_snapshot_runners WHERE snapshot_id = ? AND horse_number = 2",
                (newer["id"],),
            )

        weekly = client.get(
            f"/api/races/{race['race_id']}/weekly-decision-view",
            params={"snapshot_id": snapshot["id"], "judgement_id": judgement["id"]},
        )
        comparison = client.get(f"/api/races/{race['race_id']}/market-rule-comparison", params={
            "snapshot_id": snapshot["id"], "rule_version_id": rule["id"],
        })

    assert weekly.status_code == 200, weekly.text
    assert comparison.status_code == 200, comparison.text
    horse_two = next(runner for runner in weekly.json()["runners"] if runner["horse_number"] == 2)
    assert horse_two["win_odds"] == 20.0
    assert horse_two["market_rank"] == 2
    compared_horse_two = next(row for row in comparison.json()["rows"] if row["horse_number"] == 2)
    assert compared_horse_two["rule_conditions"][0]["observed_value"] == 2
    assert compared_horse_two["rule_conditions"][1]["observed_value"] == 20.0


def test_failed_refresh_keeps_races_from_the_last_successful_run_visible(tmp_path: Path) -> None:
    database_path = tmp_path / "weekly-failed-refresh.sqlite3"
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
        with sqlite3.connect(database_path) as connection:
            old_run_id = connection.execute(
                """INSERT INTO meeting_week_runs
                (week_start,week_end,started_at,completed_at,status,target_count,processed_count,
                 ready_count,waiting_count,failed_count)
                VALUES ('2026-08-31','2026-09-06','2026-09-05T04:00:00Z','2026-09-05T04:01:00Z',
                        'completed',1,1,0,1,0)""",
            ).lastrowid
            connection.execute(
                """INSERT INTO meeting_week_races
                (run_id,week_start,race_date,racecourse,meeting_number,meeting_day,race_number,
                 race_name,start_time,surface,distance_m,condition_text,source_url,state,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'entries_waiting',?)""",
                (old_run_id, "2026-08-31", "2026-09-06", "中山", 4, 2, 12, "古い開催予定",
                 "16:30", "芝", 2000, "3歳以上", "https://www.jra.go.jp/old",
                 "2026-09-05T04:01:00Z"),
            )
        seed_ready_week(database_path, race["race_id"], snapshot["id"], judgement["id"])
        with sqlite3.connect(database_path) as connection:
            stopped_run_id = connection.execute(
                """INSERT INTO meeting_week_runs
                (week_start,week_end,started_at,completed_at,status,target_count,processed_count,
                 ready_count,waiting_count,failed_count,stop_reason)
                VALUES ('2026-08-31','2026-09-06','2026-09-05T05:10:00Z','2026-09-05T05:11:00Z',
                        'stopped',1,0,0,0,1,'JRAからの取得を停止しました。')""",
            ).lastrowid
            connection.execute(
                """INSERT INTO meeting_week_races
                (run_id,week_start,race_date,racecourse,meeting_number,meeting_day,race_number,
                 race_name,start_time,surface,distance_m,condition_text,source_url,state,error_code,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'stopped','network_error',?)""",
                (stopped_run_id, "2026-08-31", "2026-09-05", "東京", 4, 1, 11, "架空記念",
                 "15:40", "芝", 2000, "3歳以上", "https://www.jra.go.jp/program",
                 "2026-09-05T05:11:00Z"),
            )

        response = client.get("/api/acquisition/jra/meeting-weeks/current")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "stopped"
    assert payload["failed_count"] == 1
    assert [(item["racecourse"], item["race_number"]) for item in payload["races"]] == [("東京", 11)]
    assert payload["races"][0]["display_state"] == "acquisition_failed"
    assert payload["races"][0]["judgement_id"] == judgement["id"]
    assert payload["races"][0]["attention_horse_count"] == 2
