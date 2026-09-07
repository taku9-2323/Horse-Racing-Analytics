from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from fastapi.testclient import TestClient

from app.bulk_results import result_url_from_source
from app.jra_acquisition import FetchResponse
from app.main import create_app


FIXTURES = Path(__file__).parent / "fixtures"
RESULT_HTML = (FIXTURES / "jra-race-result.html").read_bytes()
CARD_URL = "https://www.jra.go.jp/JRADB/accessD.html?CNAME=pw01dde0101202602021120260823/D1"
RESULT_URL = "https://www.jra.go.jp/JRADB/accessS.html?CNAME=pw01sde0101202602021120260823/D1"


def seed_missing_attention(
    database_path: Path, *, race_number: int = 11, with_source: bool = True,
) -> None:
    with sqlite3.connect(database_path) as connection:
        race_id = connection.execute("""INSERT INTO races
            (organizer,country,racecourse,race_date,race_number,start_time,timezone,start_utc,
             surface,distance_m,going,field_size)
            VALUES ('JRA','JP','札幌','2026-08-23',?,'15:45','Asia/Tokyo',
                    '2026-08-23T06:45:00Z','芝',1200,'良',2)""", (race_number,)).lastrowid
        assert race_id is not None
        for gate, number, name, age, sex, weight, status in (
            (1, 1, "アサヒノソラ", 3, "牡", 56, "出走"),
            (2, 2, "ツキノミチ", 4, "牝", 54, "取消"),
        ):
            connection.execute("""INSERT INTO runners
                (race_id,gate,horse_number,horse_name,age,sex,assigned_weight,status,win_odds,
                 place_odds_min,place_odds_max,raw_inverse_win_odds,normalized_win_market_share)
                VALUES (?,?,?,?,?,?,?,?,2,1.2,1.5,.5,.5)""",
                (race_id, gate, number, name, age, sex, weight, status))
        if with_source:
            card_id = connection.execute("""INSERT INTO acquired_race_cards
                (organizer,country,racecourse,race_date,race_number,start_time,timezone,start_utc,
                 surface,distance_m,going,field_size,source_url,source_race_id,received_at,
                 parser_version,response_sha256,validation_status,version,status)
                VALUES ('JRA','JP','札幌','2026-08-23',11,'15:45','Asia/Tokyo',
                        '2026-08-23T06:45:00Z','芝',1200,'良',2,?,
                        'JRA-20260823-01-02-02-11','2026-08-23T05:00:00Z','test',?,
                        'valid',1,'active')""", (CARD_URL, "a" * 64)).lastrowid
            assert card_id is not None
            connection.execute(
                "INSERT INTO jra_race_registrations (card_id,race_id) VALUES (?,?)", (card_id, race_id),
            )
        snapshot_id = connection.execute("""INSERT INTO odds_snapshots
            (race_id,observed_at,received_at,source) VALUES (?, '2026-08-23T05:00:00Z',
            '2026-08-23T05:01:00Z','test')""", (race_id,)).lastrowid
        rule_id = connection.execute("SELECT id FROM rule_versions LIMIT 1").fetchone()[0]
        judgement_id = connection.execute("""INSERT INTO rule_judgement_runs
            (race_id,input_snapshot_id,rule_version_id,judgement_as_of,frozen_at,status,
             official_pre_race_eligible) VALUES (?,?,?,'2026-08-23T05:00:00Z',
             '2026-08-23T05:02:00Z','active',1)""", (race_id, snapshot_id, rule_id)).lastrowid
        connection.execute("""INSERT INTO runner_rule_judgements
            (judgement_run_id,horse_number,horse_name,judgement,satisfied_conditions_json,
             failed_conditions_json,missing_reasons_json)
            VALUES (?,1,'アサヒノソラ','注目','[]','[]','[]')""", (judgement_id,))


def test_result_url_is_derived_from_saved_jra_race_identity() -> None:
    assert result_url_from_source(CARD_URL) == RESULT_URL


def test_bulk_run_acquires_only_missing_attention_results(tmp_path: Path) -> None:
    tasks: list[object] = []

    def fetcher(url: str) -> FetchResponse:
        if url == "https://www.jra.go.jp/robots.txt":
            return FetchResponse(200, url, {"content-type": "text/plain"}, b"User-agent: *\nDisallow:\n")
        return FetchResponse(200, url, {"content-type": "text/html; charset=utf-8"}, RESULT_HTML)

    database_path = tmp_path / "bulk.sqlite3"
    app = create_app(
        database_path, jra_fetcher=fetcher,
        now_provider=lambda: datetime(2026, 9, 7, 3, 0, tzinfo=timezone.utc),
        weekly_task_starter=lambda task: tasks.append(task),
    )
    with TestClient(app) as client:
        seed_missing_attention(database_path)
        started = client.post("/api/past-attention/result-runs", params={"page": 1})
        assert started.status_code == 202
        assert started.json()["target_count"] == 1
        assert len(tasks) == 1
        task = tasks.pop()
        assert callable(task)
        task()
        completed = client.get(f"/api/past-attention/result-runs/{started.json()['run_id']}")
        page = client.get("/api/past-attention", params={"page": 1})

    assert completed.status_code == 200
    assert completed.json()["status"] == "completed"
    assert completed.json()["processed_count"] == 1
    assert completed.json()["succeeded_count"] == 1
    assert page.json()["weeks"][0]["horses"][0]["finish_position"] == 1


def test_bulk_run_keeps_partial_success_and_retries_only_the_failure(tmp_path: Path) -> None:
    tasks: list[object] = []

    def fetcher(url: str) -> FetchResponse:
        body = b"User-agent: *\nDisallow:\n" if url.endswith("robots.txt") else RESULT_HTML
        content_type = "text/plain" if url.endswith("robots.txt") else "text/html; charset=utf-8"
        return FetchResponse(200, url, {"content-type": content_type}, body)

    database_path = tmp_path / "partial.sqlite3"
    app = create_app(
        database_path, jra_fetcher=fetcher,
        now_provider=lambda: datetime(2026, 9, 7, 3, 0, tzinfo=timezone.utc),
        weekly_task_starter=lambda task: tasks.append(task),
    )
    with TestClient(app) as client:
        seed_missing_attention(database_path)
        seed_missing_attention(database_path, race_number=10, with_source=False)
        first = client.post("/api/past-attention/result-runs", params={"page": 1})
        task = tasks.pop()
        assert callable(task)
        task()
        completed = client.get(f"/api/past-attention/result-runs/{first.json()['run_id']}").json()
        retry = client.post("/api/past-attention/result-runs", params={"page": 1})

    assert completed["status"] == "completed"
    assert completed["succeeded_count"] == 1
    assert completed["failed_count"] == 1
    assert retry.json()["target_count"] == 1
    assert retry.json()["targets"][0]["race_number"] == 10


def test_bulk_run_stops_on_access_restriction_and_keeps_fallback_reason(tmp_path: Path) -> None:
    tasks: list[object] = []

    def fetcher(url: str) -> FetchResponse:
        if url.endswith("robots.txt"):
            return FetchResponse(200, url, {"content-type": "text/plain"}, b"User-agent: *\nDisallow:\n")
        return FetchResponse(429, url, {"content-type": "text/html"}, b"rate limited")

    database_path = tmp_path / "stopped.sqlite3"
    app = create_app(
        database_path, jra_fetcher=fetcher,
        now_provider=lambda: datetime(2026, 9, 7, 3, 0, tzinfo=timezone.utc),
        weekly_task_starter=lambda task: tasks.append(task),
    )
    with TestClient(app) as client:
        seed_missing_attention(database_path)
        started = client.post("/api/past-attention/result-runs", params={"page": 1})
        task = tasks.pop()
        assert callable(task)
        task()
        stopped = client.get(f"/api/past-attention/result-runs/{started.json()['run_id']}")

    assert stopped.json()["status"] == "stopped"
    assert stopped.json()["stop_reason"] == "acquisition_stopped"
    assert stopped.json()["targets"][0]["status"] == "stopped"


def test_unpublished_result_is_counted_as_missing_and_does_not_stop_later_targets(tmp_path: Path) -> None:
    tasks: list[object] = []

    def fetcher(url: str) -> FetchResponse:
        if url.endswith("robots.txt"):
            return FetchResponse(200, url, {"content-type": "text/plain"}, b"User-agent: *\nDisallow:\n")
        return FetchResponse(404, url, {"content-type": "text/html"}, b"not published")

    database_path = tmp_path / "missing.sqlite3"
    app = create_app(
        database_path, jra_fetcher=fetcher,
        now_provider=lambda: datetime(2026, 9, 7, 3, 0, tzinfo=timezone.utc),
        weekly_task_starter=lambda task: tasks.append(task),
    )
    with TestClient(app) as client:
        seed_missing_attention(database_path)
        seed_missing_attention(database_path, race_number=10, with_source=False)
        started = client.post("/api/past-attention/result-runs", params={"page": 1})
        task = tasks.pop()
        assert callable(task)
        task()
        completed = client.get(f"/api/past-attention/result-runs/{started.json()['run_id']}").json()

    assert completed["status"] == "completed"
    assert completed["processed_count"] == 2
    assert completed["missing_count"] == 1
    assert completed["failed_count"] == 1
    missing = next(target for target in completed["targets"] if target["status"] == "missing")
    assert missing["error_message"] == "JRA結果ページはまだ公開されていません。"


def test_second_bulk_run_is_rejected_and_interrupted_run_is_stopped_on_restart(tmp_path: Path) -> None:
    tasks: list[object] = []
    database_path = tmp_path / "single-run.sqlite3"
    now = lambda: datetime(2026, 9, 7, 3, 0, tzinfo=timezone.utc)
    app = create_app(
        database_path, now_provider=now, weekly_task_starter=lambda task: tasks.append(task),
    )
    with TestClient(app) as client:
        seed_missing_attention(database_path)
        first = client.post("/api/past-attention/result-runs", params={"page": 1})
        duplicate = client.post("/api/past-attention/result-runs", params={"page": 1})

    restarted = create_app(database_path, now_provider=now)
    with TestClient(restarted) as client:
        recovered = client.get(f"/api/past-attention/result-runs/{first.json()['run_id']}")

    assert first.status_code == 202
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["code"] == "bulk_result_run_in_progress"
    assert recovered.json()["status"] == "stopped"
    assert recovered.json()["stop_reason"] == "interrupted"
