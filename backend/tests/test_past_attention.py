from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from fastapi.testclient import TestClient

from app.main import create_app


def seed_attention_race(
    database_path: Path, race_date: str, *, finish_position: int | None = None,
    result_status: str | None = None, corrected: bool = False, include_post_start: bool = False,
) -> None:
    start_utc = f"{race_date}T06:00:00Z"
    with sqlite3.connect(database_path) as connection:
        race_id = connection.execute(
            """INSERT INTO races
            (organizer,country,racecourse,race_date,race_number,start_time,timezone,start_utc,
             surface,distance_m,going,field_size)
            VALUES ('JRA','JP','東京',?,11,'15:00','Asia/Tokyo',?,'芝',2000,'良',1)""",
            (race_date, start_utc),
        ).lastrowid
        assert race_id is not None
        connection.execute(
            """INSERT INTO runners
            (race_id,gate,horse_number,horse_name,age,sex,assigned_weight,status,win_odds,
             place_odds_min,place_odds_max,raw_inverse_win_odds,normalized_win_market_share)
            VALUES (?,1,1,'アカツキ',4,'牡',57,'出走',2.0,1.2,1.5,0.5,1.0)""",
            (race_id,),
        )
        snapshot_id = connection.execute(
            """INSERT INTO odds_snapshots (race_id,observed_at,received_at,source)
            VALUES (?,?,?,'test')""",
            (race_id, f"{race_date}T05:00:00Z", f"{race_date}T05:01:00Z"),
        ).lastrowid
        assert snapshot_id is not None
        rule_id = int(connection.execute("SELECT id FROM rule_versions ORDER BY id DESC LIMIT 1").fetchone()[0])
        judgement_id = connection.execute(
            """INSERT INTO rule_judgement_runs
            (race_id,input_snapshot_id,rule_version_id,judgement_as_of,frozen_at,status,
             official_pre_race_eligible)
            VALUES (?,?,?,?,?,'active',1)""",
            (race_id, snapshot_id, rule_id, f"{race_date}T05:00:00Z", f"{race_date}T05:02:00Z"),
        ).lastrowid
        assert judgement_id is not None
        connection.execute(
            """INSERT INTO runner_rule_judgements
            (judgement_run_id,horse_number,horse_name,judgement,satisfied_conditions_json,
             failed_conditions_json,missing_reasons_json)
            VALUES (?,1,'アカツキ','注目','[""市場順位が2位以内""]','[]','[]')""",
            (judgement_id,),
        )
        if include_post_start:
            post_id = connection.execute(
                """INSERT INTO rule_judgement_runs
                (race_id,input_snapshot_id,rule_version_id,judgement_as_of,frozen_at,status,
                 official_pre_race_eligible,exclusion_reason)
                VALUES (?,?,?,?,?,'active',0,'発走後の判定です。')""",
                (race_id, snapshot_id, rule_id, f"{race_date}T07:00:00Z", f"{race_date}T07:01:00Z"),
            ).lastrowid
            assert post_id is not None
            connection.execute(
                """INSERT INTO runner_rule_judgements
                (judgement_run_id,horse_number,horse_name,judgement,satisfied_conditions_json,
                 failed_conditions_json,missing_reasons_json)
                VALUES (?,1,'アカツキ','注目','[""単勝オッズが10.0以下""]','[]','[]')""",
                (post_id,),
            )
        if result_status is not None:
            if corrected:
                first_id = connection.execute(
                    """INSERT INTO result_versions
                    (race_id,version,received_at,status,change_summary_json)
                    VALUES (?,1,?,'superseded','[]')""",
                    (race_id, f"{race_date}T08:00:00Z"),
                ).lastrowid
                assert first_id is not None
                connection.execute(
                    """INSERT INTO runner_results
                    (result_version_id,horse_number,finish_position,status,win_payout_per_100,
                     place_payout_per_100) VALUES (?,1,4,'確定',0,0)""",
                    (first_id,),
                )
                version = 2
                supersedes = first_id
            else:
                version = 1
                supersedes = None
            result_id = connection.execute(
                """INSERT INTO result_versions
                (race_id,version,received_at,status,correction_reason,supersedes_result_version_id,
                 change_summary_json) VALUES (?,?,?,'active',?,?, '[]')""",
                (race_id, version, f"{race_date}T09:00:00Z", "公式訂正" if corrected else None, supersedes),
            ).lastrowid
            assert result_id is not None
            connection.execute(
                """INSERT INTO runner_results
                (result_version_id,horse_number,finish_position,status,win_payout_per_100,
                 place_payout_per_100) VALUES (?,?,?,?,0,0)""",
                (result_id, 1, finish_position, result_status),
            )


def test_lists_past_attention_horses_four_meeting_weeks_at_a_time(tmp_path: Path) -> None:
    database_path = tmp_path / "past-attention.sqlite3"
    app = create_app(
        database_path,
        now_provider=lambda: datetime(2026, 9, 7, 3, 0, tzinfo=timezone.utc),
    )
    with TestClient(app) as client:
        seed_attention_race(database_path, "2026-09-12", finish_position=3, result_status="確定")
        seed_attention_race(
            database_path, "2026-09-06", finish_position=1, result_status="確定",
            corrected=True, include_post_start=True,
        )
        seed_attention_race(database_path, "2026-08-30", result_status="取消")
        seed_attention_race(database_path, "2026-08-23", finish_position=2, result_status="確定")
        seed_attention_race(database_path, "2026-08-16", finish_position=4, result_status="確定")
        seed_attention_race(database_path, "2026-08-09")
        seed_attention_race(database_path, "2026-08-02", result_status="競走中止")

        first = client.get("/api/past-attention", params={"page": 1})
        second = client.get("/api/past-attention", params={"page": 2})

    assert first.status_code == 200
    page = first.json()
    assert page["page"] == 1
    assert page["total_week_count"] == 6
    assert page["has_newer"] is False
    assert page["has_older"] is True
    assert [week["week_start"] for week in page["weeks"]] == [
        "2026-08-31", "2026-08-24", "2026-08-17", "2026-08-10",
    ]
    latest = page["weeks"][0]["horses"][0]
    assert latest == {
        "race_id": latest["race_id"], "race_date": "2026-09-06", "racecourse": "東京",
        "race_number": 11, "start_time": "15:00", "horse_number": 1,
        "horse_name": "アカツキ", "pre_race_attention": True,
        "post_start_attention": True, "pre_race_snapshot_id": latest["pre_race_snapshot_id"],
        "pre_race_judgement_id": latest["pre_race_judgement_id"],
        "post_start_snapshot_id": latest["post_start_snapshot_id"],
        "post_start_judgement_id": latest["post_start_judgement_id"],
        "result_status": "確定", "finish_position": 1, "has_result_correction": True,
    }
    assert page["weeks"][1]["horses"][0]["result_status"] == "取消"
    assert page["weeks"][2]["horses"][0]["finish_position"] == 2
    assert page["weeks"][3]["horses"][0]["finish_position"] == 4
    assert second.status_code == 200
    assert second.json()["has_newer"] is True
    assert second.json()["has_older"] is False
    assert second.json()["weeks"][0]["horses"][0]["result_status"] is None
    assert second.json()["weeks"][1]["horses"][0]["result_status"] == "競走中止"
