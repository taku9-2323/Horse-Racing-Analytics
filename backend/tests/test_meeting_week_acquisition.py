from collections.abc import Callable
from datetime import date, datetime, timezone
from hashlib import sha256
from pathlib import Path
import sqlite3

from fastapi.testclient import TestClient

from app.jra_acquisition import FetchResponse
from app.main import create_app
from app.meeting_week_acquisition import extract_race_card_urls


INDEX_URL = "https://www.jra.go.jp/keiba/calendar2026/index.html"
SATURDAY_URL = "https://www.jra.go.jp/keiba/calendar2026/2026/9/0905.html"
SUNDAY_URL = "https://www.jra.go.jp/keiba/calendar2026/2026/9/0906.html"
HOLIDAY_URL = "https://www.jra.go.jp/keiba/calendar2026/2026/8/0831.html"
SELECTION_URL = "https://www.jra.go.jp/JRADB/accessD.html?CNAME=pw01dli00/F3"
CARD_URL = "https://www.jra.go.jp/JRADB/accessD.html?CNAME=pw01dde0106202604010120260905/42"
ODDS_URL = "https://www.jra.go.jp/JRADB/accessO.html?CNAME=pw151ouS306202604010120260905Z/DD"
SECOND_CARD_URL = "https://www.jra.go.jp/JRADB/accessD.html?CNAME=pw01dde0106202604010220260905/43"
SECOND_ODDS_URL = "https://www.jra.go.jp/JRADB/accessO.html?CNAME=pw151ouS306202604010220260905Z/DE"
SAPPORO_SEED_URL = "https://www.jra.go.jp/JRADB/accessD.html?CNAME=pw01dde0101202602051120260905/AA"
SAPPORO_CARD_URL = "https://www.jra.go.jp/JRADB/accessD.html?CNAME=pw01dde0101202602050220260905/BB"
ROBOTS_URL = "https://www.jra.go.jp/robots.txt"


def test_race_card_links_are_canonicalized_without_page_fragments() -> None:
    html = (
        f'<a href="{CARD_URL.removeprefix("https://www.jra.go.jp")}">race</a>'
        f'<a href="{CARD_URL.removeprefix("https://www.jra.go.jp")}#contents">contents</a>'
        f'<a href="{CARD_URL.removeprefix("https://www.jra.go.jp")}#same_unit">same</a>'
    ).encode()

    assert extract_race_card_urls(
        html, "text/html", CARD_URL, date(2026, 8, 31), date(2026, 9, 6),
    ) == [CARD_URL]

INDEX_HTML = f"""<!doctype html><html><body>
<a href="/keiba/calendar2026/2026/9/0905.html">9月5日</a>
<a href="/keiba/calendar2026/2026/9/0906.html">9月6日</a>
<a href="/keiba/calendar2026/2026/9/0912.html">翌週</a>
</body></html>""".encode()

SATURDAY_PROGRAM = """<!doctype html><html><body>
<h1>2026年9月5日（土曜） 競馬番組</h1>
<h2>4回中山1日</h2>
<table><tr><th>レース番号</th><th>レース名・条件</th><th>発走時刻</th></tr>
<tr><td>1レース</td><td>2歳未勝利 1,200（ダ）</td><td>9時50分</td></tr></table>
</body></html>""".encode()

SUNDAY_PROGRAM = """<!doctype html><html><body>
<h1>2026年9月6日（日曜） 競馬番組</h1>
<h2>4回中山2日</h2>
<table><tr><th>レース番号</th><th>レース名・条件</th><th>発走時刻</th></tr>
<tr><td>1レース</td><td>2歳未勝利 1,800（芝）</td><td>10時05分</td></tr></table>
</body></html>""".encode()

CURRENT_JRA_PROGRAM = """<!doctype html><html><body>
<h1>2026年9月5日（土曜） 競馬番組</h1>
<table><caption><div><div>4回中山1日</div></div></caption>
<tbody><tr><th scope="row">1<span>レース</span></th><td>
<p>2歳未勝利</p><p><span>1,200</span><span>（ダ）</span></p>
</td><td>9時50分</td></tr></tbody></table>
</body></html>""".encode()

HOLIDAY_PROGRAM = """<!doctype html><html><body>
<h1>2026年8月31日（月曜） 競馬番組</h1>
<h2>4回中山3日</h2>
<table><tr><th>レース番号</th><th>レース名・条件</th><th>発走時刻</th></tr>
<tr><td>9レース</td><td>祝日特別 2,000（芝）</td><td>14時35分</td></tr></table>
</body></html>""".encode()

SELECTION_HTML = f"""<!doctype html><html><body>
<a href="{CARD_URL.removeprefix('https://www.jra.go.jp')}">4回中山1日</a>
</body></html>""".encode()

BASE_CARD = (Path(__file__).parent / "fixtures" / "jra-race-card.html").read_bytes()
CARD_HTML = (BASE_CARD
    .replace("2026年8月30日（日曜） 3回架空2日".encode(), "2026年9月5日（土曜） 4回中山1日".encode())
    .replace("7レース".encode(), "1レース".encode())
    .replace("13時25分".encode(), "9時50分".encode())
    .replace("1,800メートル（芝・右）".encode(), "1,200メートル（ダート・右）".encode())
    .replace("架空記念".encode(), "2歳未勝利".encode())
    .replace(b"</body>", (
        f'<a href="{CARD_URL.removeprefix("https://www.jra.go.jp")}">1レース</a>'
        f'<a href="#" onclick="return doAction(\'/JRADB/accessO.html\', \'{ODDS_URL.split("CNAME=")[1]}\');">オッズ</a>'
        '<p>オッズ更新時刻：9時05分</p></body>'
    ).encode()))

ODDS_HTML = """<!doctype html><html><body>
<h1>単勝・複勝オッズ（馬番順） 2026年9月5日（土曜）4回中山1日 1レース</h1>
<p>オッズ更新時刻：9時05分</p>
<table><caption>単勝・複勝オッズ（馬番順）</caption>
<tr><th>枠</th><th>馬番</th><th>馬名</th><th>単勝</th><th>複勝（3着払い）</th></tr>
<tr><td>枠1白</td><td>1</td><td>アサヒノソラ</td><td>18.1</td><td>2.8 - 4.4</td></tr>
<tr><td>枠2黒</td><td>2</td><td>ツキノミチ</td><td>2.5</td><td>1.2 - 1.4</td></tr>
</table></body></html>""".encode()

TWO_RACE_PROGRAM = SATURDAY_PROGRAM.replace(
    b"</table>", "<tr><td>2レース</td><td>3歳未勝利 1,800（芝）</td><td>10時40分</td></tr></table>".encode(),
)
SECOND_CARD_HTML = (BASE_CARD
    .replace("2026年8月30日（日曜） 3回架空2日".encode(), "2026年9月5日（土曜） 4回中山1日".encode())
    .replace("7レース".encode(), "2レース".encode())
    .replace("13時25分".encode(), "10時40分".encode())
    .replace("架空記念".encode(), "3歳未勝利".encode())
    .replace(b"</body>", (
        f'<a href="{SECOND_CARD_URL.removeprefix("https://www.jra.go.jp")}">2レース</a>'
        f'<a href="#" onclick="return doAction(\'/JRADB/accessO.html\', \'{SECOND_ODDS_URL.split("CNAME=")[1]}\');">オッズ</a>'
        '<p>オッズ更新時刻：9時05分</p></body>'
    ).encode()))
SECOND_ODDS_HTML = ODDS_HTML.replace("1レース".encode(), "2レース".encode())
SAPPORO_SEED_HTML = (BASE_CARD
    .replace("2026年8月30日（日曜） 3回架空2日".encode(), "2026年9月5日（土曜） 2回札幌5日".encode())
    .replace("7レース".encode(), "11レース".encode())
    .replace("13時25分".encode(), "15時20分".encode())
    .replace("架空記念".encode(), "札幌メイン".encode())
    .replace(b"</body>", (
        f'<a href="{SAPPORO_CARD_URL.removeprefix("https://www.jra.go.jp")}">2レース</a></body>'
    ).encode()))
SAPPORO_CARD_HTML = (BASE_CARD
    .replace("2026年8月30日（日曜） 3回架空2日".encode(), "2026年9月5日（土曜） 2回札幌5日".encode())
    .replace("7レース".encode(), "2レース".encode())
    .replace("13時25分".encode(), "10時20分".encode())
    .replace("架空記念".encode(), "2歳未勝利".encode()))


class MappingFetcher:
    def __init__(self, pages: dict[str, FetchResponse]) -> None:
        self.pages = pages
        self.urls: list[str] = []

    def __call__(self, url: str) -> FetchResponse:
        self.urls.append(url)
        if url == ROBOTS_URL:
            return FetchResponse(200, url, {"content-type": "text/plain"}, b"User-agent: *\nDisallow:\n")
        return self.pages.get(url, FetchResponse(404, url, {"content-type": "text/html"}, b""))


def page(url: str, body: bytes, status: int = 200, final_url: str | None = None) -> FetchResponse:
    return FetchResponse(status, final_url or url, {"content-type": "text/html; charset=utf-8"}, body)


def deferred_tasks() -> tuple[list[Callable[[], None]], Callable[[Callable[[], None]], None]]:
    tasks: list[Callable[[], None]] = []
    return tasks, tasks.append


def test_starts_one_user_initiated_week_run_and_rejects_a_duplicate(tmp_path: Path) -> None:
    tasks, starter = deferred_tasks()
    app = create_app(
        tmp_path / "week.sqlite3",
        now_provider=lambda: datetime(2026, 9, 2, 3, 0, tzinfo=timezone.utc),
        jra_fetcher=MappingFetcher({}), weekly_task_starter=starter,
    )

    with TestClient(app) as client:
        started = client.post("/api/acquisition/jra/meeting-weeks/current/runs")
        duplicate = client.post("/api/acquisition/jra/meeting-weeks/current/runs")
        current = client.get("/api/acquisition/jra/meeting-weeks/current")

    assert started.status_code == 202, started.text
    assert started.json()["status"] == "running"
    assert started.json()["week_start"] == "2026-08-31"
    assert started.json()["week_end"] == "2026-09-06"
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["code"] == "meeting_week_run_in_progress"
    assert current.json()["status"] == "running"
    assert len(tasks) == 1


def test_week_run_keeps_program_rows_when_entries_are_not_published(tmp_path: Path) -> None:
    tasks, starter = deferred_tasks()
    fetcher = MappingFetcher({
        INDEX_URL: page(INDEX_URL, INDEX_HTML),
        SATURDAY_URL: page(SATURDAY_URL, SATURDAY_PROGRAM),
        SUNDAY_URL: page(SUNDAY_URL, SUNDAY_PROGRAM),
    })
    app = create_app(
        tmp_path / "waiting.sqlite3",
        now_provider=lambda: datetime(2026, 9, 2, 3, 0, tzinfo=timezone.utc),
        jra_fetcher=fetcher, weekly_task_starter=starter,
    )

    with TestClient(app) as client:
        assert client.post("/api/acquisition/jra/meeting-weeks/current/runs").status_code == 202
        tasks.pop()()
        current = client.get("/api/acquisition/jra/meeting-weeks/current")

    assert current.status_code == 200
    payload = current.json()
    assert payload["status"] == "completed"
    assert payload["target_count"] == 2
    assert payload["waiting_count"] == 2
    assert [(race["race_date"], race["racecourse"], race["race_number"], race["state"]) for race in payload["races"]] == [
        ("2026-09-05", "中山", 1, "entries_waiting"),
        ("2026-09-06", "中山", 1, "entries_waiting"),
    ]
    assert payload["races"][0]["race_name"] == "2歳未勝利"
    assert payload["races"][0]["start_time"] == "09:50"
    assert payload["races"][0]["distance_m"] == 1200
    assert payload["races"][0]["surface"] == "ダート"
    assert payload["last_updated_at"] == "2026-09-02T03:00:00Z"


def test_week_run_parses_the_current_jra_caption_program_structure(tmp_path: Path) -> None:
    tasks, starter = deferred_tasks()
    app = create_app(
        tmp_path / "current-program.sqlite3",
        now_provider=lambda: datetime(2026, 9, 2, 3, 0, tzinfo=timezone.utc),
        jra_fetcher=MappingFetcher({
            INDEX_URL: page(INDEX_URL, f'<a href="{SATURDAY_URL}">開催日</a>'.encode()),
            SATURDAY_URL: page(SATURDAY_URL, CURRENT_JRA_PROGRAM),
        }),
        weekly_task_starter=starter,
    )

    with TestClient(app) as client:
        client.post("/api/acquisition/jra/meeting-weeks/current/runs")
        tasks.pop()()
        current = client.get("/api/acquisition/jra/meeting-weeks/current").json()

    assert current["status"] == "completed"
    assert [(race["racecourse"], race["race_number"], race["race_name"]) for race in current["races"]] == [
        ("中山", 1, "2歳未勝利"),
    ]


def test_week_run_includes_a_weekday_meeting_listed_by_the_official_calendar(tmp_path: Path) -> None:
    tasks, starter = deferred_tasks()
    holiday_index = f"""<html><body>
      <a href="/keiba/calendar2026/2026/8/0831.html">祝日開催</a>
    </body></html>""".encode()
    app = create_app(
        tmp_path / "holiday.sqlite3",
        now_provider=lambda: datetime(2026, 9, 2, 3, 0, tzinfo=timezone.utc),
        jra_fetcher=MappingFetcher({
            INDEX_URL: page(INDEX_URL, holiday_index),
            HOLIDAY_URL: page(HOLIDAY_URL, HOLIDAY_PROGRAM),
        }),
        weekly_task_starter=starter,
    )

    with TestClient(app) as client:
        client.post("/api/acquisition/jra/meeting-weeks/current/runs")
        tasks.pop()()
        current = client.get("/api/acquisition/jra/meeting-weeks/current").json()

    assert [(race["race_date"], race["race_name"]) for race in current["races"]] == [
        ("2026-08-31", "祝日特別"),
    ]


def test_week_run_stops_when_the_calendar_link_structure_is_unrecognizable(tmp_path: Path) -> None:
    tasks, starter = deferred_tasks()
    app = create_app(
        tmp_path / "calendar-change.sqlite3",
        now_provider=lambda: datetime(2026, 9, 2, 3, 0, tzinfo=timezone.utc),
        jra_fetcher=MappingFetcher({INDEX_URL: page(INDEX_URL, b"<html><body>changed</body></html>")}),
        weekly_task_starter=starter,
    )

    with TestClient(app) as client:
        client.post("/api/acquisition/jra/meeting-weeks/current/runs")
        tasks.pop()()
        current = client.get("/api/acquisition/jra/meeting-weeks/current").json()

    assert current["status"] == "stopped"
    assert current["last_target"] == INDEX_URL


def test_week_run_discovers_programs_from_the_dynamic_calendar_shell(tmp_path: Path) -> None:
    tasks, starter = deferred_tasks()
    dynamic_index = b"""<!doctype html><html><head>
      <script src="/keiba/common/calendar/cal.js?version=2026"></script>
    </head><body><div id="cal_unit"></div></body></html>"""
    fetcher = MappingFetcher({
        INDEX_URL: page(INDEX_URL, dynamic_index),
        HOLIDAY_URL: page(HOLIDAY_URL, b"", status=403),
        SATURDAY_URL: page(SATURDAY_URL, SATURDAY_PROGRAM),
        SUNDAY_URL: page(SUNDAY_URL, SUNDAY_PROGRAM),
    })
    app = create_app(
        tmp_path / "dynamic-calendar.sqlite3",
        now_provider=lambda: datetime(2026, 9, 2, 3, 0, tzinfo=timezone.utc),
        jra_fetcher=fetcher, weekly_task_starter=starter,
    )

    with TestClient(app) as client:
        client.post("/api/acquisition/jra/meeting-weeks/current/runs")
        tasks.pop()()
        current = client.get("/api/acquisition/jra/meeting-weeks/current").json()

    assert current["status"] == "completed"
    assert [(race["race_date"], race["racecourse"]) for race in current["races"]] == [
        ("2026-09-05", "中山"),
        ("2026-09-06", "中山"),
    ]
    assert SATURDAY_URL in fetcher.urls
    assert SUNDAY_URL in fetcher.urls


def test_week_run_persists_progress_before_the_run_finishes(tmp_path: Path) -> None:
    database_path = tmp_path / "progress.sqlite3"
    observed_progress: list[tuple[int, int, str]] = []

    class ObservingFetcher(MappingFetcher):
        def __call__(self, url: str) -> FetchResponse:
            if url == SELECTION_URL:
                with sqlite3.connect(database_path) as connection:
                    row = connection.execute(
                        "SELECT target_count, processed_count, status FROM meeting_week_runs",
                    ).fetchone()
                assert row is not None
                observed_progress.append((int(row[0]), int(row[1]), str(row[2])))
            return super().__call__(url)

    tasks, starter = deferred_tasks()
    fetcher = ObservingFetcher({
        INDEX_URL: page(INDEX_URL, INDEX_HTML.replace(
            f'<a href="/keiba/calendar2026/2026/9/0906.html">9月6日</a>'.encode(), b"",
        )),
        SATURDAY_URL: page(SATURDAY_URL, SATURDAY_PROGRAM),
    })
    app = create_app(
        database_path,
        now_provider=lambda: datetime(2026, 9, 2, 3, 0, tzinfo=timezone.utc),
        jra_fetcher=fetcher, weekly_task_starter=starter,
    )

    with TestClient(app) as client:
        client.post("/api/acquisition/jra/meeting-weeks/current/runs")
        tasks.pop()()

    assert observed_progress == [(1, 1, "running")]


def test_week_run_registers_available_card_odds_and_fixed_rule_judgement(tmp_path: Path) -> None:
    tasks, starter = deferred_tasks()
    fetcher = MappingFetcher({
        INDEX_URL: page(INDEX_URL, INDEX_HTML.replace(
            f'<a href="/keiba/calendar2026/2026/9/0906.html">9月6日</a>'.encode(), b"",
        )),
        SATURDAY_URL: page(SATURDAY_URL, SATURDAY_PROGRAM),
        SELECTION_URL: page(SELECTION_URL, SELECTION_HTML, final_url="https://www.jra.go.jp/JRADB/accessD.html"),
        CARD_URL: page(CARD_URL, CARD_HTML),
        ODDS_URL: page(
            ODDS_URL,
            ODDS_HTML.replace("オッズ更新時刻：9時05分".encode(), "9時05分現在オッズ".encode()),
            final_url="https://www.jra.go.jp/JRADB/accessO.html",
        ),
    })
    app = create_app(
        tmp_path / "ready.sqlite3",
        now_provider=lambda: datetime(2026, 9, 5, 0, 10, tzinfo=timezone.utc),
        jra_fetcher=fetcher, weekly_task_starter=starter,
    )

    with TestClient(app) as client:
        client.post("/api/acquisition/jra/meeting-weeks/current/runs")
        tasks.pop()()
        current = client.get("/api/acquisition/jra/meeting-weeks/current").json()
        races = client.get("/api/races").json()
        client.post("/api/acquisition/jra/meeting-weeks/current/runs")
        tasks.pop()()
        snapshots = client.get(f"/api/races/{races[0]['race_id']}/odds-snapshots").json()
        judgements = client.get(f"/api/races/{races[0]['race_id']}/rule-judgements").json()

    assert current["status"] == "completed"
    assert current["ready_count"] == 1
    assert current["waiting_count"] == 0
    assert current["races"][0]["state"] == "ready"
    assert current["races"][0]["race_id"] == races[0]["race_id"]
    assert current["races"][0]["snapshot_id"] > 0
    assert current["races"][0]["judgement_id"] > 0
    assert len(snapshots) == 1
    assert len(judgements) == 1
    odds_digest = sha256(ODDS_URL.encode("utf-8")).hexdigest()
    assert list((tmp_path / "jra-html-cache").glob(f"odds-{odds_digest}-*.html"))
    assert fetcher.urls.index(CARD_URL) < fetcher.urls.index(ODDS_URL)


def test_week_run_treats_timestamp_free_post_start_odds_as_final(tmp_path: Path) -> None:
    tasks, starter = deferred_tasks()
    fetcher = MappingFetcher({
        INDEX_URL: page(INDEX_URL, INDEX_HTML.replace(
            f'<a href="/keiba/calendar2026/2026/9/0906.html">9月6日</a>'.encode(), b"",
        )),
        SATURDAY_URL: page(SATURDAY_URL, SATURDAY_PROGRAM),
        SELECTION_URL: page(
            SELECTION_URL, SELECTION_HTML, final_url="https://www.jra.go.jp/JRADB/accessD.html",
        ),
        CARD_URL: page(CARD_URL, CARD_HTML),
        ODDS_URL: page(
            ODDS_URL,
            ODDS_HTML.replace("<p>オッズ更新時刻：9時05分</p>".encode(), b""),
            final_url="https://www.jra.go.jp/JRADB/accessO.html",
        ),
    })
    app = create_app(
        tmp_path / "post-start-final-odds.sqlite3",
        now_provider=lambda: datetime(2026, 9, 5, 1, 0, tzinfo=timezone.utc),
        jra_fetcher=fetcher, weekly_task_starter=starter,
    )

    with TestClient(app) as client:
        client.post("/api/acquisition/jra/meeting-weeks/current/runs")
        tasks.pop()()
        current = client.get("/api/acquisition/jra/meeting-weeks/current").json()
        races = client.get("/api/races").json()
        snapshots = client.get(f"/api/races/{races[0]['race_id']}/odds-snapshots").json()
        judgements = client.get(f"/api/races/{races[0]['race_id']}/rule-judgements").json()

    assert current["ready_count"] == 1
    assert current["races"][0]["state"] == "ready"
    assert snapshots[-1]["observed_at"] == "2026-09-05T01:00:00Z"
    assert judgements[-1]["official_pre_race_eligible"] is False
    assert judgements[-1]["exclusion_reason"] == "発走後の判定です。"


def test_week_run_recovers_the_published_odds_time_from_cached_html(tmp_path: Path) -> None:
    tasks, starter = deferred_tasks()
    clock = [datetime(2026, 9, 5, 0, 10, tzinfo=timezone.utc)]
    database_path = tmp_path / "legacy-odds-time.sqlite3"
    current_odds_html = (
        ODDS_HTML.replace(b"<html>", b'<html><meta charset="utf-8">')
        .replace("オッズ更新時刻：9時05分".encode(), "9時05分現在オッズ".encode())
    )
    fetcher = MappingFetcher({
        INDEX_URL: page(INDEX_URL, INDEX_HTML.replace(
            f'<a href="/keiba/calendar2026/2026/9/0906.html">9月6日</a>'.encode(), b"",
        )),
        SATURDAY_URL: page(SATURDAY_URL, SATURDAY_PROGRAM),
        SELECTION_URL: page(
            SELECTION_URL, SELECTION_HTML, final_url="https://www.jra.go.jp/JRADB/accessD.html",
        ),
        CARD_URL: page(CARD_URL, CARD_HTML),
        ODDS_URL: page(
            ODDS_URL, current_odds_html, final_url="https://www.jra.go.jp/JRADB/accessO.html",
        ),
    })
    app = create_app(
        database_path, now_provider=lambda: clock[0], jra_fetcher=fetcher,
        weekly_task_starter=starter,
    )

    with TestClient(app) as client:
        client.post("/api/acquisition/jra/meeting-weeks/current/runs")
        tasks.pop()()
        with sqlite3.connect(database_path) as connection:
            connection.execute("DELETE FROM runner_rule_judgements")
            connection.execute("DELETE FROM rule_judgement_runs")
            connection.execute("UPDATE odds_snapshots SET observed_at=NULL")
            connection.execute(
                "UPDATE jra_odds_observations SET source_updated_at=NULL, parser_version='jra-odds/1'",
            )
        fetcher.pages[ODDS_URL] = page(
            ODDS_URL,
            current_odds_html.replace("9時05分現在オッズ".encode(), b""),
            final_url="https://www.jra.go.jp/JRADB/accessO.html",
        )
        clock[0] = datetime(2026, 9, 5, 0, 26, tzinfo=timezone.utc)
        client.post("/api/acquisition/jra/meeting-weeks/current/runs")
        tasks.pop()()
        current = client.get("/api/acquisition/jra/meeting-weeks/current").json()
        races = client.get("/api/races").json()
        snapshots = client.get(f"/api/races/{races[0]['race_id']}/odds-snapshots").json()

    assert current["ready_count"] == 1
    assert len(snapshots) == 2
    assert snapshots[-1]["observed_at"] == "2026-09-05T00:05:00Z"


def test_week_run_reuses_the_registered_race_for_a_new_card_version(tmp_path: Path) -> None:
    tasks, starter = deferred_tasks()
    clock = [datetime(2026, 9, 5, 0, 10, tzinfo=timezone.utc)]
    fetcher = MappingFetcher({
        INDEX_URL: page(INDEX_URL, INDEX_HTML.replace(
            f'<a href="/keiba/calendar2026/2026/9/0906.html">9月6日</a>'.encode(), b"",
        )),
        SATURDAY_URL: page(SATURDAY_URL, SATURDAY_PROGRAM),
        SELECTION_URL: page(
            SELECTION_URL, SELECTION_HTML, final_url="https://www.jra.go.jp/JRADB/accessD.html",
        ),
        CARD_URL: page(CARD_URL, CARD_HTML),
        ODDS_URL: page(ODDS_URL, ODDS_HTML, final_url="https://www.jra.go.jp/JRADB/accessO.html"),
    })
    app = create_app(
        tmp_path / "new-card-version.sqlite3", now_provider=lambda: clock[0],
        jra_fetcher=fetcher, weekly_task_starter=starter,
    )

    with TestClient(app) as client:
        client.post("/api/acquisition/jra/meeting-weeks/current/runs")
        tasks.pop()()
        fetcher.pages[CARD_URL] = page(
            CARD_URL, CARD_HTML.replace(b"</body>", b"<p>refreshed</p></body>"),
        )
        clock[0] = datetime(2026, 9, 5, 0, 26, tzinfo=timezone.utc)
        client.post("/api/acquisition/jra/meeting-weeks/current/runs")
        tasks.pop()()
        current = client.get("/api/acquisition/jra/meeting-weeks/current").json()
        cards = client.get("/api/acquisition/jra/race-cards").json()

    assert current["status"] == "completed"
    assert current["ready_count"] == 1
    assert current["failed_count"] == 0
    assert [(card["version"], card["status"]) for card in cards] == [
        (1, "superseded"),
        (2, "active"),
    ]


def test_week_run_keeps_processing_after_one_race_card_fails(tmp_path: Path) -> None:
    tasks, starter = deferred_tasks()
    selection = f"""<html><body>
      <a href="{CARD_URL.removeprefix('https://www.jra.go.jp')}">1レース</a>
      <a href="{SECOND_CARD_URL.removeprefix('https://www.jra.go.jp')}">2レース</a>
    </body></html>""".encode()
    app = create_app(
        tmp_path / "partial-card-failure.sqlite3",
        now_provider=lambda: datetime(2026, 9, 5, 0, 10, tzinfo=timezone.utc),
        jra_fetcher=MappingFetcher({
            INDEX_URL: page(INDEX_URL, INDEX_HTML.replace(
                f'<a href="/keiba/calendar2026/2026/9/0906.html">9月6日</a>'.encode(), b"",
            )),
            SATURDAY_URL: page(SATURDAY_URL, TWO_RACE_PROGRAM),
            SELECTION_URL: page(
                SELECTION_URL, selection, final_url="https://www.jra.go.jp/JRADB/accessD.html",
            ),
            CARD_URL: page(CARD_URL, b"<html>invalid race card</html>"),
            SECOND_CARD_URL: page(SECOND_CARD_URL, SECOND_CARD_HTML),
            SECOND_ODDS_URL: page(
                SECOND_ODDS_URL, SECOND_ODDS_HTML,
                final_url="https://www.jra.go.jp/JRADB/accessO.html",
            ),
        }),
        weekly_task_starter=starter,
    )

    with TestClient(app) as client:
        client.post("/api/acquisition/jra/meeting-weeks/current/runs")
        tasks.pop()()
        current = client.get("/api/acquisition/jra/meeting-weeks/current").json()

    assert current["status"] == "completed"
    assert current["ready_count"] == 1
    assert current["failed_count"] == 1
    assert [(race["race_number"], race["state"], race["error_code"]) for race in current["races"]] == [
        (1, "stopped", "race_card_validation_failed"),
        (2, "ready", None),
    ]


def test_week_run_expands_a_new_racecourse_seed_to_find_all_published_cards(tmp_path: Path) -> None:
    tasks, starter = deferred_tasks()
    program = """<html><body><h1>2026年9月5日（土曜） 競馬番組</h1>
      <h2>4回中山1日</h2><table>
        <tr><td>1レース</td><td>2歳未勝利 1,200（ダ）</td><td>9時50分</td></tr>
      </table>
      <h2>2回札幌5日</h2><table>
        <tr><td>2レース</td><td>2歳未勝利 1,800（芝）</td><td>10時20分</td></tr>
      </table></body></html>""".encode()
    first_seed = CARD_HTML.replace(
        b"</body>",
        f'<a href="{SAPPORO_SEED_URL.removeprefix("https://www.jra.go.jp")}">札幌</a></body>'.encode(),
    )
    app = create_app(
        tmp_path / "nested-racecourse-seed.sqlite3",
        now_provider=lambda: datetime(2026, 9, 5, 0, 10, tzinfo=timezone.utc),
        jra_fetcher=MappingFetcher({
            INDEX_URL: page(INDEX_URL, INDEX_HTML.replace(
                f'<a href="/keiba/calendar2026/2026/9/0906.html">9月6日</a>'.encode(), b"",
            )),
            SATURDAY_URL: page(SATURDAY_URL, program),
            SELECTION_URL: page(
                SELECTION_URL, SELECTION_HTML,
                final_url="https://www.jra.go.jp/JRADB/accessD.html",
            ),
            CARD_URL: page(CARD_URL, first_seed),
            ODDS_URL: page(ODDS_URL, ODDS_HTML, final_url="https://www.jra.go.jp/JRADB/accessO.html"),
            SAPPORO_SEED_URL: page(SAPPORO_SEED_URL, SAPPORO_SEED_HTML),
            SAPPORO_CARD_URL: page(SAPPORO_CARD_URL, SAPPORO_CARD_HTML),
        }),
        weekly_task_starter=starter,
    )

    with TestClient(app) as client:
        client.post("/api/acquisition/jra/meeting-weeks/current/runs")
        tasks.pop()()
        current = client.get("/api/acquisition/jra/meeting-weeks/current").json()

    assert current["status"] == "completed"
    assert [(race["racecourse"], race["race_number"], race["state"]) for race in current["races"]] == [
        ("中山", 1, "ready"),
        ("札幌", 2, "odds_waiting"),
    ]


def test_week_run_stops_after_a_refusal_but_keeps_discovered_races(tmp_path: Path) -> None:
    tasks, starter = deferred_tasks()
    fetcher = MappingFetcher({
        INDEX_URL: page(INDEX_URL, INDEX_HTML),
        SATURDAY_URL: page(SATURDAY_URL, SATURDAY_PROGRAM),
        SUNDAY_URL: page(SUNDAY_URL, b"", status=429),
    })
    app = create_app(
        tmp_path / "stopped.sqlite3",
        now_provider=lambda: datetime(2026, 9, 2, 3, 0, tzinfo=timezone.utc),
        jra_fetcher=fetcher, weekly_task_starter=starter,
    )

    with TestClient(app) as client:
        client.post("/api/acquisition/jra/meeting-weeks/current/runs")
        tasks.pop()()
        current = client.get("/api/acquisition/jra/meeting-weeks/current").json()

    assert current["status"] == "stopped"
    assert current["target_count"] == 2
    assert current["processed_count"] == 1
    assert current["failed_count"] == 1
    assert current["stop_reason"] == "JRAからの取得を停止しました。CSV取込を使用してください。"
    assert current["last_target"] == SUNDAY_URL
    assert current["races"][0]["race_date"] == "2026-09-05"


def test_race_state_records_the_error_when_odds_acquisition_stops(tmp_path: Path) -> None:
    tasks, starter = deferred_tasks()
    one_day_index = INDEX_HTML.replace(
        f'<a href="/keiba/calendar2026/2026/9/0906.html">9月6日</a>'.encode(), b"",
    )
    app = create_app(
        tmp_path / "odds-stop.sqlite3",
        now_provider=lambda: datetime(2026, 9, 5, 0, 10, tzinfo=timezone.utc),
        jra_fetcher=MappingFetcher({
            INDEX_URL: page(INDEX_URL, one_day_index),
            SATURDAY_URL: page(SATURDAY_URL, SATURDAY_PROGRAM),
            SELECTION_URL: page(
                SELECTION_URL, SELECTION_HTML,
                final_url="https://www.jra.go.jp/JRADB/accessD.html",
            ),
            CARD_URL: page(CARD_URL, CARD_HTML),
            ODDS_URL: page(ODDS_URL, b"", status=429),
        }),
        weekly_task_starter=starter,
    )

    with TestClient(app) as client:
        client.post("/api/acquisition/jra/meeting-weeks/current/runs")
        tasks.pop()()
        current = client.get("/api/acquisition/jra/meeting-weeks/current").json()

    assert current["status"] == "completed"
    assert current["failed_count"] == 1
    assert current["races"][0]["state"] == "stopped"
    assert current["races"][0]["error_code"] == "acquisition_stopped"


def test_retry_reuses_week_rows_without_duplication(tmp_path: Path) -> None:
    tasks, starter = deferred_tasks()
    fetcher = MappingFetcher({
        INDEX_URL: page(INDEX_URL, INDEX_HTML),
        SATURDAY_URL: page(SATURDAY_URL, SATURDAY_PROGRAM),
        SUNDAY_URL: page(SUNDAY_URL, SUNDAY_PROGRAM),
    })
    app = create_app(
        tmp_path / "retry.sqlite3",
        now_provider=lambda: datetime(2026, 9, 2, 3, 0, tzinfo=timezone.utc),
        jra_fetcher=fetcher, weekly_task_starter=starter,
    )

    with TestClient(app) as client:
        client.post("/api/acquisition/jra/meeting-weeks/current/runs")
        tasks.pop()()
        client.post("/api/acquisition/jra/meeting-weeks/current/runs")
        tasks.pop()()
        current = client.get("/api/acquisition/jra/meeting-weeks/current").json()

    assert current["status"] == "completed"
    assert len(current["races"]) == 2
    assert current["run_id"] == 2


def test_schedule_change_keeps_prior_run_history_but_hides_removed_races_from_latest(tmp_path: Path) -> None:
    database_path = tmp_path / "schedule-change.sqlite3"
    clock = [datetime(2026, 9, 2, 3, 0, tzinfo=timezone.utc)]
    tasks, starter = deferred_tasks()
    fetcher = MappingFetcher({
        INDEX_URL: page(INDEX_URL, INDEX_HTML),
        SATURDAY_URL: page(SATURDAY_URL, SATURDAY_PROGRAM),
        SUNDAY_URL: page(SUNDAY_URL, SUNDAY_PROGRAM),
    })
    app = create_app(
        database_path,
        now_provider=lambda: clock[0],
        jra_fetcher=fetcher, weekly_task_starter=starter,
    )

    with TestClient(app) as client:
        client.post("/api/acquisition/jra/meeting-weeks/current/runs")
        tasks.pop()()
        fetcher.pages[INDEX_URL] = page(INDEX_URL, INDEX_HTML.replace(
            f'<a href="/keiba/calendar2026/2026/9/0906.html">9月6日</a>'.encode(), b"",
        ))
        clock[0] = datetime(2026, 9, 2, 3, 16, tzinfo=timezone.utc)
        client.post("/api/acquisition/jra/meeting-weeks/current/runs")
        tasks.pop()()
        latest = client.get("/api/acquisition/jra/meeting-weeks/current").json()

    with sqlite3.connect(database_path) as connection:
        historical_row_count = int(connection.execute(
            "SELECT COUNT(*) FROM meeting_week_races",
        ).fetchone()[0])

    assert [race["race_date"] for race in latest["races"]] == ["2026-09-05"]
    assert historical_row_count == 3


def test_unexpected_background_failure_releases_the_run_lock(tmp_path: Path) -> None:
    tasks, starter = deferred_tasks()

    def broken_fetcher(url: str) -> FetchResponse:
        raise RuntimeError(f"broken fetch: {url}")

    app = create_app(
        tmp_path / "broken.sqlite3",
        now_provider=lambda: datetime(2026, 9, 2, 3, 0, tzinfo=timezone.utc),
        jra_fetcher=broken_fetcher, weekly_task_starter=starter,
    )

    with TestClient(app) as client:
        client.post("/api/acquisition/jra/meeting-weeks/current/runs")
        tasks.pop()()
        stopped = client.get("/api/acquisition/jra/meeting-weeks/current").json()
        retry = client.post("/api/acquisition/jra/meeting-weeks/current/runs")

    assert stopped["status"] == "stopped"
    assert stopped["stop_reason"] == "開催週の取得を停止しました。CSV取込を使用してください。"
    assert retry.status_code == 202


def test_application_restart_marks_an_orphaned_run_as_stopped(tmp_path: Path) -> None:
    database_path = tmp_path / "restart.sqlite3"
    tasks, starter = deferred_tasks()
    first_app = create_app(
        database_path,
        now_provider=lambda: datetime(2026, 9, 2, 3, 0, tzinfo=timezone.utc),
        jra_fetcher=MappingFetcher({}), weekly_task_starter=starter,
    )
    with TestClient(first_app) as client:
        assert client.post("/api/acquisition/jra/meeting-weeks/current/runs").status_code == 202

    restarted_app = create_app(
        database_path,
        now_provider=lambda: datetime(2026, 9, 2, 3, 5, tzinfo=timezone.utc),
        jra_fetcher=MappingFetcher({}), weekly_task_starter=starter,
    )
    with TestClient(restarted_app) as client:
        current = client.get("/api/acquisition/jra/meeting-weeks/current").json()
        retry = client.post("/api/acquisition/jra/meeting-weeks/current/runs")

    assert current["status"] == "stopped"
    assert current["stop_reason"] == "アプリ再起動により前回の取得を終了しました。"
    assert retry.status_code == 202
