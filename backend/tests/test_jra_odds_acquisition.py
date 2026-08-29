from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from fastapi.testclient import TestClient

from app.jra_acquisition import FetchResponse, parse_jra_odds_page, parse_jra_odds_update_time, validate_odds_url
from app.main import create_app


URL = "https://www.jra.go.jp/JRADB/accessO.html?CNAME=pw151ouS301202602031120260829Z/76"
CARD_URL = "https://www.jra.go.jp/JRADB/accessD.html?CNAME=pw01dde0199202603020720260830/AA"
FICTIONAL_ODDS_URL = "https://www.jra.go.jp/JRADB/accessO.html?CNAME=pw151ouS399202603020720260830Z/76"
CARD_HTML = (Path(__file__).parent / "fixtures" / "jra-race-card.html").read_bytes()
ODDS_HTML = """<!doctype html><h1>単勝・複勝オッズ（馬番順） 2026年8月30日（日曜）3回架空2日 7レース</h1><table><caption>単勝・複勝オッズ（馬番順）</caption>
<tr><th>枠</th><th>馬番</th><th>馬名</th><th>単勝</th><th>複勝（3着払い）</th></tr>
<tr><td>枠1白</td><td>1</td><td>アサヒノソラ</td><td>18.1</td><td>2.8 - 4.4</td></tr>
<tr><td>枠2黒</td><td>2</td><td>ツキノミチ</td><td>2.5</td><td>1.2 - 1.4</td></tr></table>""".encode()


class JraFetcher:
    def __init__(self, odds_html: bytes = ODDS_HTML, odds_status: int = 200) -> None:
        self.odds_html = odds_html
        self.odds_status = odds_status
        self.urls: list[str] = []

    def __call__(self, url: str) -> FetchResponse:
        self.urls.append(url)
        if url == "https://www.jra.go.jp/robots.txt":
            return FetchResponse(200, url, {"content-type": "text/plain"}, b"User-agent: *\nDisallow:\n")
        body = self.odds_html if "accessO.html" in url else CARD_HTML
        status = self.odds_status if "accessO.html" in url else 200
        return FetchResponse(status, url, {"content-type": "text/html; charset=utf-8"}, body)


def test_parses_win_and_place_ranges_from_jra_horse_number_odds_table() -> None:
    html = """<!doctype html><table><caption>単勝・複勝オッズ（馬番順）</caption>
    <tr><th>枠</th><th>馬番</th><th>馬名</th><th>単勝</th><th>複勝（3着払い）</th></tr>
    <tr><td>枠1白</td><td>1</td><td>架空一号</td><td>18.1</td><td>2.8 - 4.4</td></tr>
    <tr><td>2</td><td>架空二号</td><td>2.5</td><td>1.2 - 1.4</td><td>牝4</td></tr></table>""".encode()

    validate_odds_url(URL)
    assert parse_jra_odds_page(html, "text/html; charset=utf-8") == [
        {"horse_number": 1, "win_odds": 18.1, "place_odds_min": 2.8, "place_odds_max": 4.4},
        {"horse_number": 2, "win_odds": 2.5, "place_odds_min": 1.2, "place_odds_max": 1.4},
    ]


def test_rejects_an_odds_page_with_a_missing_horse_number() -> None:
    html = """<table><caption>単勝・複勝オッズ（馬番順）</caption>
    <tr><td>枠1白</td><td>1</td><td>架空一号</td><td>2.0</td><td>1.1 - 1.2</td></tr>
    <tr><td>枠2黒</td><td>3</td><td>架空三号</td><td>3.0</td><td>1.2 - 1.4</td></tr></table>""".encode()

    try:
        parse_jra_odds_page(html, "text/html; charset=utf-8")
    except Exception as error:
        assert getattr(error, "code") == "odds_validation_failed"
    else:
        raise AssertionError("expected validation failure")


def test_records_only_a_declared_jra_odds_update_time() -> None:
    assert parse_jra_odds_update_time(b"<p>no timestamp</p>", "text/html; charset=utf-8", URL) is None
    body = "<p>オッズ更新時刻：14時55分</p>".encode()
    assert parse_jra_odds_update_time(body, "text/html; charset=utf-8", URL) == "2026-08-29T05:55:00Z"


def test_registers_a_jra_card_and_odds_as_one_analysis_race_atomically(tmp_path: Path) -> None:
    database_path = tmp_path / "jra-odds.sqlite3"
    app = create_app(
        database_path, jra_fetcher=JraFetcher(),
        now_provider=lambda: datetime(2026, 8, 29, 5, 0, tzinfo=timezone.utc),
    )
    with TestClient(app) as client:
        card = client.post("/api/acquisition/jra/race-card", json={"url": CARD_URL}).json()
        response = client.post(
            f"/api/acquisition/jra/race-cards/{card['card_id']}/odds",
            json={"url": FICTIONAL_ODDS_URL},
        )

    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["race_id"] > 0
    assert payload["snapshot_id"] > 0
    assert payload["observed_at"] is None
    assert payload["received_at"] == "2026-08-29T05:00:00Z"
    assert payload["source"]["parser_version"] == "jra-odds/1"
    assert [runner["horse_number"] for runner in payload["runners"]] == [1, 2]
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM races").fetchone() == (1,)
        assert connection.execute("SELECT COUNT(*) FROM odds_snapshots").fetchone() == (1,)
        assert connection.execute("SELECT COUNT(*) FROM jra_odds_observations").fetchone() == (1,)


def test_reacquisition_adds_a_snapshot_and_uses_the_recent_url_cache(tmp_path: Path) -> None:
    fetcher = JraFetcher()
    app = create_app(tmp_path / "versions.sqlite3", jra_fetcher=fetcher,
                     now_provider=lambda: datetime(2026, 8, 29, 5, 0, tzinfo=timezone.utc))
    with TestClient(app) as client:
        card = client.post("/api/acquisition/jra/race-card", json={"url": CARD_URL}).json()
        first = client.post(f"/api/acquisition/jra/race-cards/{card['card_id']}/odds", json={"url": FICTIONAL_ODDS_URL})
        second = client.post(f"/api/acquisition/jra/race-cards/{card['card_id']}/odds", json={"url": FICTIONAL_ODDS_URL})
    assert first.status_code == second.status_code == 201
    assert first.json()["snapshot_id"] != second.json()["snapshot_id"]
    assert fetcher.urls.count(FICTIONAL_ODDS_URL) == 1
    assert first.json()["received_at"] == second.json()["received_at"]


def test_http_refusal_and_runner_mismatch_save_no_odds_snapshot(tmp_path: Path) -> None:
    for name, fetcher in (
        ("forbidden", JraFetcher(odds_status=403)),
        ("rate-limited", JraFetcher(odds_status=429)),
        ("wrong-race", JraFetcher(odds_html=ODDS_HTML.replace("3回架空2日 7レース".encode(), "3回札幌2日 7レース".encode()))),
        ("mismatch", JraFetcher(odds_html=ODDS_HTML.replace(
            b'<tr><td>\xe6\x9e\xa02\xe9\xbb\x92</td><td>2</td><td>\xe3\x83\x84\xe3\x82\xad\xe3\x83\x8e\xe3\x83\x9f\xe3\x83\x81</td><td>2.5</td><td>1.2 - 1.4</td></tr>', b"",
        ))),
    ):
        database_path = tmp_path / f"{name}.sqlite3"
        app = create_app(database_path, jra_fetcher=fetcher,
                         now_provider=lambda: datetime(2026, 8, 29, 5, 0, tzinfo=timezone.utc))
        with TestClient(app) as client:
            card = client.post("/api/acquisition/jra/race-card", json={"url": CARD_URL}).json()
            response = client.post(f"/api/acquisition/jra/race-cards/{card['card_id']}/odds", json={"url": FICTIONAL_ODDS_URL})
        assert response.status_code in {422, 503}
        with sqlite3.connect(database_path) as connection:
            assert connection.execute("SELECT COUNT(*) FROM odds_snapshots").fetchone() == (0,)
