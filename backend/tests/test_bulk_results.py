from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
import sqlite3

from fastapi.testclient import TestClient
import pytest

from app.jra_acquisition import AcquisitionError, FetchResponse, JraResultAcquirer
from app.main import create_app


FIXTURES = Path(__file__).parent / "fixtures"
RESULT_HTML = (FIXTURES / "jra-race-result.html").read_bytes()
CARD_URL = "https://www.jra.go.jp/JRADB/accessD.html?CNAME=pw01dde0101202602021120260823/D1"
OFFICIAL_RESULT_URL = "https://www.jra.go.jp/JRADB/accessS.html?CNAME=pw01sde1001202602021120260823/AE"
CARD_WITH_RESULT_LINK_HTML = f"""<!doctype html><html><head><meta charset="utf-8"></head><body>
<div id="race_related_link"><ul><li class="result">
<a href="{OFFICIAL_RESULT_URL.removeprefix('https://www.jra.go.jp')}">レース結果</a>
</li></ul></div></body></html>""".encode()
PARAMETER_ERROR_HTML = b"<!doctype html><html><head><meta charset='utf-8'></head><body>parameter error</body></html>"


def successful_result_flow(url: str) -> FetchResponse:
    if url.endswith("robots.txt"):
        return FetchResponse(200, url, {"content-type": "text/plain"}, b"User-agent: *\nDisallow:\n")
    if url == CARD_URL:
        return FetchResponse(200, url, {"content-type": "text/html; charset=utf-8"}, CARD_WITH_RESULT_LINK_HTML)
    if url == OFFICIAL_RESULT_URL:
        return FetchResponse(200, url, {"content-type": "text/html; charset=utf-8"}, RESULT_HTML)
    raise AssertionError(f"unexpected JRA URL: {url}")


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
                        'valid',1,'active')""", (CARD_URL, f"{race_number:064d}")).lastrowid
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


def test_bulk_run_acquires_only_missing_attention_results(tmp_path: Path) -> None:
    tasks: list[object] = []
    fetched_urls: list[str] = []

    def fetcher(url: str) -> FetchResponse:
        fetched_urls.append(url)
        if url == "https://www.jra.go.jp/robots.txt":
            return FetchResponse(200, url, {"content-type": "text/plain"}, b"User-agent: *\nDisallow:\n")
        if url == CARD_URL:
            return FetchResponse(200, url, {"content-type": "text/html; charset=utf-8"}, CARD_WITH_RESULT_LINK_HTML)
        if url == OFFICIAL_RESULT_URL:
            return FetchResponse(200, url, {"content-type": "text/html; charset=utf-8"}, RESULT_HTML)
        return FetchResponse(200, url, {"content-type": "text/html; charset=utf-8"}, PARAMETER_ERROR_HTML)

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
    assert CARD_URL in fetched_urls
    assert OFFICIAL_RESULT_URL in fetched_urls
    with sqlite3.connect(database_path) as connection:
        link_observation = connection.execute("""SELECT link_source_url,resolved_result_url,
            link_received_at,link_parser_version,link_response_sha256,link_validation_status
            FROM jra_result_observations ORDER BY id DESC LIMIT 1""").fetchone()
    assert link_observation == (
        CARD_URL, OFFICIAL_RESULT_URL, "2026-09-07T03:00:00Z", "jra-result-link/1",
        sha256(CARD_WITH_RESULT_LINK_HTML).hexdigest(), "valid",
    )


def test_result_acquirer_caches_the_validated_race_card_page() -> None:
    fetched_urls: list[str] = []

    def fetcher(url: str) -> FetchResponse:
        fetched_urls.append(url)
        return successful_result_flow(url)

    acquirer = JraResultAcquirer(fetcher)
    received_at = datetime(2026, 9, 7, 3, 0, tzinfo=timezone.utc)
    acquirer.acquire_from_race_card(CARD_URL, received_at)
    acquirer.acquire_from_race_card(CARD_URL, received_at + timedelta(minutes=1))

    assert fetched_urls.count(CARD_URL) == 1


def test_result_link_audit_is_kept_when_robots_disallows_the_resolved_url() -> None:
    def fetcher(url: str) -> FetchResponse:
        if url.endswith("robots.txt"):
            robots = b"User-agent: *\nDisallow: /JRADB/accessS.html\n"
            return FetchResponse(200, url, {"content-type": "text/plain"}, robots)
        if url == CARD_URL:
            return FetchResponse(
                200, url, {"content-type": "text/html; charset=utf-8"},
                CARD_WITH_RESULT_LINK_HTML,
            )
        raise AssertionError(f"unexpected JRA URL: {url}")

    received_at = datetime(2026, 9, 7, 3, 0, tzinfo=timezone.utc)
    with pytest.raises(AcquisitionError) as caught:
        JraResultAcquirer(fetcher).acquire_from_race_card(CARD_URL, received_at)

    assert caught.value.code == "acquisition_stopped"
    assert caught.value.observation is not None
    assert caught.value.observation["link_source_url"] == CARD_URL
    assert caught.value.observation["resolved_result_url"] == OFFICIAL_RESULT_URL
    assert caught.value.observation["link_validation_status"] == "valid"


def test_missing_saved_race_card_is_failed_not_unpublished(tmp_path: Path) -> None:
    tasks: list[object] = []

    def fetcher(url: str) -> FetchResponse:
        if url.endswith("robots.txt"):
            return FetchResponse(200, url, {"content-type": "text/plain"}, b"User-agent: *\nDisallow:\n")
        return FetchResponse(404, url, {"content-type": "text/html"}, b"not found")

    database_path = tmp_path / "missing-card.sqlite3"
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
        completed = client.get(f"/api/past-attention/result-runs/{started.json()['run_id']}").json()

    assert completed["failed_count"] == 1
    assert completed["missing_count"] == 0
    assert completed["targets"][0]["error_code"] == "result_link_fetch_failed"


def test_bulk_run_keeps_partial_success_and_retries_only_the_failure(tmp_path: Path) -> None:
    tasks: list[object] = []

    def fetcher(url: str) -> FetchResponse:
        return successful_result_flow(url)

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


def test_forbidden_result_is_failed_and_later_targets_are_processed(tmp_path: Path) -> None:
    tasks: list[object] = []

    def fetcher(url: str) -> FetchResponse:
        if url.endswith("robots.txt"):
            return FetchResponse(200, url, {"content-type": "text/plain"}, b"User-agent: *\nDisallow:\n")
        if url == CARD_URL:
            return FetchResponse(200, url, {"content-type": "text/html; charset=utf-8"}, CARD_WITH_RESULT_LINK_HTML)
        return FetchResponse(403, url, {"content-type": "text/html"}, b"forbidden")

    database_path = tmp_path / "forbidden-race.sqlite3"
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
    assert completed["failed_count"] == 2
    forbidden = next(target for target in completed["targets"] if target["race_number"] == 11)
    assert forbidden["status"] == "failed"
    assert forbidden["error_code"] == "result_access_forbidden"
    assert forbidden["error_message"] == "このレースのJRA結果ページを取得できませんでした。"
    with sqlite3.connect(database_path) as connection:
        failure_observation = connection.execute("""SELECT link_source_url,resolved_result_url,
            link_parser_version,link_response_sha256,link_validation_status
            FROM acquisition_failures ORDER BY id DESC LIMIT 1""").fetchone()
    assert failure_observation == (
        CARD_URL, OFFICIAL_RESULT_URL, "jra-result-link/1",
        sha256(CARD_WITH_RESULT_LINK_HTML).hexdigest(), "valid",
    )


def test_temporary_robots_failure_is_failed_and_next_race_is_acquired(tmp_path: Path) -> None:
    tasks: list[object] = []
    robots_requests = 0

    def fetcher(url: str) -> FetchResponse:
        nonlocal robots_requests
        if url.endswith("robots.txt"):
            robots_requests += 1
            if robots_requests == 1:
                return FetchResponse(503, url, {"content-type": "text/plain"}, b"temporary failure")
            return FetchResponse(200, url, {"content-type": "text/plain"}, b"User-agent: *\nDisallow:\n")
        if url == CARD_URL:
            return FetchResponse(200, url, {"content-type": "text/html; charset=utf-8"}, CARD_WITH_RESULT_LINK_HTML)
        return FetchResponse(200, url, {"content-type": "text/html; charset=utf-8"}, RESULT_HTML)

    database_path = tmp_path / "temporary-robots.sqlite3"
    app = create_app(
        database_path, jra_fetcher=fetcher,
        now_provider=lambda: datetime(2026, 9, 7, 3, 0, tzinfo=timezone.utc),
        weekly_task_starter=lambda task: tasks.append(task),
    )
    with TestClient(app) as client:
        seed_missing_attention(database_path)
        seed_missing_attention(database_path, race_number=10)
        started = client.post("/api/past-attention/result-runs", params={"page": 1})
        task = tasks.pop()
        assert callable(task)
        task()
        completed = client.get(f"/api/past-attention/result-runs/{started.json()['run_id']}").json()

    assert completed["status"] == "completed"
    assert completed["processed_count"] == 2
    assert completed["failed_count"] == 1
    assert completed["succeeded_count"] == 1
    assert robots_requests == 2
    failed = next(target for target in completed["targets"] if target["status"] == "failed")
    assert failed["error_code"] == "result_access_check_failed"
    assert failed["error_message"] == "JRAの取得可否を確認できませんでした。次のレースへ進みます。"


def test_bulk_run_stops_after_five_consecutive_failures(tmp_path: Path) -> None:
    tasks: list[object] = []
    database_path = tmp_path / "consecutive-failures.sqlite3"
    app = create_app(
        database_path,
        now_provider=lambda: datetime(2026, 9, 7, 3, 0, tzinfo=timezone.utc),
        weekly_task_starter=lambda task: tasks.append(task),
    )
    with TestClient(app) as client:
        for race_number in range(12, 6, -1):
            seed_missing_attention(database_path, race_number=race_number, with_source=False)
        started = client.post("/api/past-attention/result-runs", params={"page": 1})
        task = tasks.pop()
        assert callable(task)
        task()
        stopped = client.get(f"/api/past-attention/result-runs/{started.json()['run_id']}").json()

    assert stopped["status"] == "stopped"
    assert stopped["stop_reason"] == "consecutive_failures"
    assert stopped["processed_count"] == 5
    assert stopped["failed_count"] == 5
    assert sum(target["status"] == "pending" for target in stopped["targets"]) == 1


def test_success_resets_the_consecutive_failure_count(tmp_path: Path) -> None:
    tasks: list[object] = []

    def fetcher(url: str) -> FetchResponse:
        return successful_result_flow(url)

    database_path = tmp_path / "failure-count-reset.sqlite3"
    app = create_app(
        database_path, jra_fetcher=fetcher,
        now_provider=lambda: datetime(2026, 9, 7, 3, 0, tzinfo=timezone.utc),
        weekly_task_starter=lambda task: tasks.append(task),
    )
    with TestClient(app) as client:
        for race_number in range(12, 3, -1):
            seed_missing_attention(database_path, race_number=race_number, with_source=race_number == 8)
        started = client.post("/api/past-attention/result-runs", params={"page": 1})
        task = tasks.pop()
        assert callable(task)
        task()
        completed = client.get(f"/api/past-attention/result-runs/{started.json()['run_id']}").json()

    assert completed["status"] == "completed"
    assert completed["processed_count"] == 9
    assert completed["failed_count"] == 8
    assert completed["succeeded_count"] == 1


def test_unpublished_result_is_counted_as_missing_and_does_not_stop_later_targets(tmp_path: Path) -> None:
    tasks: list[object] = []

    def fetcher(url: str) -> FetchResponse:
        if url.endswith("robots.txt"):
            return FetchResponse(200, url, {"content-type": "text/plain"}, b"User-agent: *\nDisallow:\n")
        if url == CARD_URL:
            return FetchResponse(200, url, {"content-type": "text/html; charset=utf-8"}, CARD_WITH_RESULT_LINK_HTML)
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
