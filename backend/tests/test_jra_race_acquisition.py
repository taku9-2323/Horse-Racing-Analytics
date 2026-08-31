from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from fastapi.testclient import TestClient

from app.jra_acquisition import FetchResponse
from app.main import create_app


FIXTURE = (Path(__file__).parent / "fixtures" / "jra-race-card.html").read_bytes()
RESULT_FIXTURE = (Path(__file__).parent / "fixtures" / "jra-race-result.html").read_bytes()
URL = "https://www.jra.go.jp/JRADB/accessD.html?CNAME=pw01dde0199202603020720260830/AA"
DETAILED_URL = "https://www.jra.go.jp/JRADB/accessD.html?CNAME=pw01dde1001202602021120260823/D1"
NORMAL_SAPPORO_11_URL = "https://www.jra.go.jp/JRADB/accessD.html?CNAME=pw01dde0101202602021120260823/ZZ"
RESULT_URL = "https://www.jra.go.jp/JRADB/accessS.html?CNAME=pw01sde0101202602021120260823/AE"
DETAILED_RESULT_URL = "https://www.jra.go.jp/JRADB/accessS.html?CNAME=pw01sde1001202602021120260823/AF"
SAPPORO_11_FIXTURE = (FIXTURE
    .replace("2026年8月30日（日曜） 3回架空2日".encode(), "2026年8月23日（日曜） 2回札幌2日".encode())
    .replace("7レース".encode(), "11レース".encode()))


class FakeFetcher:
    def __init__(self, page: FetchResponse | None = None) -> None:
        self.page = page or FetchResponse(
            status=200, final_url=URL,
            headers={"content-type": "text/html; charset=utf-8"}, body=FIXTURE,
        )
        self.urls: list[str] = []

    def __call__(self, url: str) -> FetchResponse:
        self.urls.append(url)
        if url == "https://www.jra.go.jp/robots.txt":
            return FetchResponse(
                status=200, final_url=url,
                headers={"content-type": "text/plain; charset=utf-8"},
                body=b"User-agent: *\nDisallow:\n",
            )
        return self.page


def test_user_acquires_and_registers_a_fictional_jra_race_card(tmp_path: Path) -> None:
    fetcher = FakeFetcher()
    app = create_app(
        tmp_path / "analysis.sqlite3", jra_fetcher=fetcher,
        now_provider=lambda: datetime(2026, 8, 29, 4, 0, tzinfo=timezone.utc),
    )

    with TestClient(app) as client:
        response = client.post("/api/acquisition/jra/race-card", json={"url": URL})

    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["race"] == {
        "organizer": "JRA", "country": "JP", "racecourse": "架空",
        "race_date": "2026-08-30", "race_number": 7,
        "start_time": "13:25", "timezone": "Asia/Tokyo",
        "start_utc": "2026-08-30T04:25:00Z", "surface": "芝",
        "distance_m": 1800, "going": "良", "field_size": 2,
    }
    assert payload["runners"] == [
        {"gate": 1, "horse_number": 1, "horse_name": "アサヒノソラ", "age": 3,
         "sex": "牡", "assigned_weight": 56.0, "status": "出走"},
        {"gate": 2, "horse_number": 2, "horse_name": "ツキノミチ", "age": 4,
         "sex": "牝", "assigned_weight": 54.0, "status": "取消"},
    ]
    assert payload["source"] == {
        "url": URL, "source_race_id": "JRA-20260830-99-03-02-07",
        "received_at": "2026-08-29T04:00:00Z",
        "source_updated_at": None, "parser_version": "jra-race-entry/2",
        "response_sha256": payload["source"]["response_sha256"],
        "validation_status": "valid",
    }
    assert len(payload["source"]["response_sha256"]) == 64
    assert fetcher.urls == ["https://www.jra.go.jp/robots.txt", URL]


def test_user_acquires_current_jra_card_without_declared_headcount(tmp_path: Path) -> None:
    current_page = (FIXTURE
        .replace(b'<span class="field_size">2\xe9\xa0\xad</span>', b"")
        .replace(
            '<span>\u30b3\u30fc\u30b9\uff1a1,800\u30e1\u30fc\u30c8\u30eb\uff08\u829d\u30fb\u53f3\uff09</span>'.encode(),
            ('<div class="cell course"><span class="cap">\u30b3\u30fc\u30b9\uff1a</span>1,800'
             '<span class="unit">\u30e1\u30fc\u30c8\u30eb</span><span class="detail">\uff08\u829d\u30fb\u53f3\uff09</span></div>').encode(),
        )
        .replace(
            '<span class="name"><a>\u30a2\u30b5\u30d2\u30ce\u30bd\u30e9</a></span></span></td>'.encode(),
            ('<span class="name"><a>\u30a2\u30b5\u30d2\u30ce\u30bd\u30e9</a></span></span>'
             '<div class="cell weight">466kg<span>(-6)</span></div></td>').encode(),
        )
        .replace('<p class="age">\u725d4/\u9ed2\u9e7f</p>'.encode(), '<p class="age">\u305b\u30934/\u9ed2\u9e7f</p>'.encode()))
    app = create_app(tmp_path / "current.sqlite3", now_provider=lambda: datetime(2026, 8, 29, tzinfo=timezone.utc), jra_fetcher=FakeFetcher(FetchResponse(
        status=200, final_url=URL,
        headers={"content-type": "text/html; charset=utf-8"}, body=current_page,
    )))

    with TestClient(app) as client:
        response = client.post("/api/acquisition/jra/race-card", json={"url": URL})

    assert response.status_code == 201, response.text
    assert response.json()["race"]["field_size"] == 2
    assert response.json()["runners"][1]["sex"] == "\u30bb\u30f3"


def test_current_card_without_declared_headcount_requires_post_table_marker(tmp_path: Path) -> None:
    incomplete_page = (FIXTURE
        .replace(b'<span class="field_size">2\xe9\xa0\xad</span>', b"")
        .replace(b'<div id="odds_area"><p>\xe3\x82\xaa\xe3\x83\x83\xe3\x82\xba\xe6\xa1\x88\xe5\x86\x85</p></div>', b""))
    app = create_app(tmp_path / "incomplete-current.sqlite3", jra_fetcher=FakeFetcher(FetchResponse(
        status=200, final_url=URL,
        headers={"content-type": "text/html; charset=utf-8"}, body=incomplete_page,
    )))

    with TestClient(app) as client:
        response = client.post("/api/acquisition/jra/race-card", json={"url": URL})
        cards = client.get("/api/acquisition/jra/race-cards")

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "race_card_validation_failed"
    assert cards.json() == []


def test_current_card_rejects_terminal_marker_before_entry_table(tmp_path: Path) -> None:
    marker = b'<div id="odds_area"><p>\xe3\x82\xaa\xe3\x83\x83\xe3\x82\xba\xe6\xa1\x88\xe5\x86\x85</p></div>'
    misplaced_marker_page = (FIXTURE
        .replace(marker, b"")
        .replace(b'<div id="syutsuba">', marker + b'<div id="syutsuba">'))
    app = create_app(tmp_path / "misplaced-marker.sqlite3", jra_fetcher=FakeFetcher(FetchResponse(
        status=200, final_url=URL,
        headers={"content-type": "text/html; charset=utf-8"}, body=misplaced_marker_page,
    )))

    with TestClient(app) as client:
        response = client.post("/api/acquisition/jra/race-card", json={"url": URL})

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "race_card_validation_failed"


def test_current_card_rejects_terminal_marker_nested_before_inner_table(tmp_path: Path) -> None:
    marker = b'<div id="odds_area"><p>\xe3\x82\xaa\xe3\x83\x83\xe3\x82\xba\xe6\xa1\x88\xe5\x86\x85</p></div>'
    nested_marker_page = (FIXTURE
        .replace(marker, b"")
        .replace(b'<div id="syutsuba">', b'<div id="syutsuba">' + marker))
    app = create_app(tmp_path / "nested-marker.sqlite3", jra_fetcher=FakeFetcher(FetchResponse(
        status=200, final_url=URL,
        headers={"content-type": "text/html; charset=utf-8"}, body=nested_marker_page,
    )))

    with TestClient(app) as client:
        response = client.post("/api/acquisition/jra/race-card", json={"url": URL})

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "race_card_validation_failed"


def test_user_acquires_cp932_page_declared_as_shift_jis(tmp_path: Path) -> None:
    cp932_page = (FIXTURE.decode()
        .replace('charset="utf-8"', 'charset="Shift_JIS"')
        .replace("</body>", "<p>\u9ad9</p></body>")
        .encode("cp932"))
    app = create_app(tmp_path / "cp932.sqlite3", now_provider=lambda: datetime(2026, 8, 29, tzinfo=timezone.utc), jra_fetcher=FakeFetcher(FetchResponse(
        status=200, final_url=URL, headers={"content-type": "text/html"}, body=cp932_page,
    )))

    with TestClient(app) as client:
        response = client.post("/api/acquisition/jra/race-card", json={"url": URL})

    assert response.status_code == 201, response.text
    assert response.json()["race"]["race_number"] == 7


def test_invalid_runner_set_is_rejected_without_registering_a_card(tmp_path: Path) -> None:
    malformed = FIXTURE.replace(b'<td class="num">2</td>', b'<td class="num">1</td>')
    fetcher = FakeFetcher(FetchResponse(
        status=200, final_url=URL,
        headers={"content-type": "text/html; charset=utf-8"}, body=malformed,
    ))
    app = create_app(tmp_path / "invalid.sqlite3", jra_fetcher=fetcher)

    with TestClient(app) as client:
        response = client.post("/api/acquisition/jra/race-card", json={"url": URL})
        cards = client.get("/api/acquisition/jra/race-cards")
        failures = client.get("/api/acquisition/jra/failures")

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "race_card_validation_failed"
    assert cards.json() == []
    assert failures.json()[0]["validation_status"] == "invalid"
    assert failures.json()[0]["error_code"] == "race_card_validation_failed"
    assert len(failures.json()[0]["response_sha256"]) == 64


def test_disallowed_url_and_http_refusal_stop_with_csv_fallback(tmp_path: Path) -> None:
    fetcher = FakeFetcher(FetchResponse(
        status=429, final_url=URL, headers={"content-type": "text/html"}, body=b"",
    ))
    app = create_app(tmp_path / "stopped.sqlite3", jra_fetcher=fetcher)

    with TestClient(app) as client:
        disallowed = client.post(
            "/api/acquisition/jra/race-card",
            json={"url": "https://example.test/JRADB/accessD.html?CNAME=x"},
        )
        refused = client.post("/api/acquisition/jra/race-card", json={"url": URL})

    assert disallowed.status_code == 422
    assert disallowed.json()["detail"]["code"] == "url_not_allowed"
    assert refused.status_code == 503
    assert refused.json()["detail"] == {
        "code": "acquisition_stopped",
        "message": "JRAからの取得を停止しました。CSV取込を使用してください。",
    }


def test_same_url_uses_recent_html_cache_without_another_request(tmp_path: Path) -> None:
    fetcher = FakeFetcher()
    app = create_app(
        tmp_path / "cached.sqlite3", jra_fetcher=fetcher,
        now_provider=lambda: datetime(2026, 8, 29, 4, 0, tzinfo=timezone.utc),
    )

    with TestClient(app) as client:
        first = client.post("/api/acquisition/jra/race-card", json={"url": URL})
        second = client.post("/api/acquisition/jra/race-card", json={"url": URL})

    assert first.status_code == 201
    assert second.status_code == 201
    assert second.json()["card_id"] == first.json()["card_id"]
    assert fetcher.urls == ["https://www.jra.go.jp/robots.txt", URL]


def test_source_identity_and_declared_headcount_must_match_page(tmp_path: Path) -> None:
    wrong_headcount = FIXTURE.replace(b'class="field_size">2', b'class="field_size">3')
    app = create_app(tmp_path / "identity.sqlite3", jra_fetcher=FakeFetcher(FetchResponse(
        status=200, final_url=URL, headers={"content-type": "text/html; charset=utf-8"},
        body=wrong_headcount,
    )))

    with TestClient(app) as client:
        headcount = client.post("/api/acquisition/jra/race-card", json={"url": URL})

    wrong_race_url = URL.replace("0720260830", "0820260830")
    identity_app = create_app(tmp_path / "wrong-identity.sqlite3", jra_fetcher=FakeFetcher(FetchResponse(
        status=200, final_url=wrong_race_url,
        headers={"content-type": "text/html; charset=utf-8"}, body=FIXTURE,
    )))
    with TestClient(identity_app) as client:
        identity = client.post("/api/acquisition/jra/race-card", json={"url": wrong_race_url})

    assert headcount.status_code == 422
    assert identity.status_code == 422
    assert headcount.json()["detail"]["code"] == "race_card_validation_failed"
    assert identity.json()["detail"]["code"] == "race_card_validation_failed"


def test_page_update_time_is_recorded_only_when_declared(tmp_path: Path) -> None:
    updated = FIXTURE.replace(
        b'<div class="race_header">',
        b'<div class="race_header"><time class="update_time" datetime="2026-08-29T03:55:00Z"></time>',
    )
    app = create_app(tmp_path / "updated.sqlite3", now_provider=lambda: datetime(2026, 8, 29, tzinfo=timezone.utc), jra_fetcher=FakeFetcher(FetchResponse(
        status=200, final_url=URL, headers={"content-type": "text/html; charset=utf-8"}, body=updated,
    )))

    with TestClient(app) as client:
        response = client.post("/api/acquisition/jra/race-card", json={"url": URL})

    assert response.status_code == 201
    assert response.json()["source"]["source_updated_at"] == "2026-08-29T03:55:00Z"


def test_past_race_card_url_is_rejected_in_favor_of_result_page(tmp_path: Path) -> None:
    fetcher = FakeFetcher(FetchResponse(
        status=200, final_url=DETAILED_URL,
        headers={"content-type": "text/html; charset=utf-8"}, body=SAPPORO_11_FIXTURE,
    ))
    app = create_app(
        tmp_path / "detailed.sqlite3", jra_fetcher=fetcher,
        now_provider=lambda: datetime(2026, 8, 24, 0, 0, tzinfo=timezone.utc),
    )

    with TestClient(app) as client:
        response = client.post("/api/acquisition/jra/race-card", json={"url": DETAILED_URL})

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "past_race_requires_result"


def test_normal_and_detailed_views_share_one_race_version_chain(tmp_path: Path) -> None:
    class ViewFetcher(FakeFetcher):
        def __call__(self, url: str) -> FetchResponse:
            self.urls.append(url)
            if url == "https://www.jra.go.jp/robots.txt":
                return FetchResponse(200, url, {"content-type": "text/plain"}, b"User-agent: *\nDisallow:\n")
            if "accessS.html" in url:
                return FetchResponse(200, url, {"content-type": "text/html; charset=utf-8"}, RESULT_FIXTURE)
            return FetchResponse(
                200, url, {"content-type": "text/html; charset=utf-8"},
                SAPPORO_11_FIXTURE if "dde01" in url else SAPPORO_11_FIXTURE.replace("架空記念".encode(), "架空記念・詳細".encode()),
            )

    times = iter([
        datetime(2026, 8, 23, 0, 0, tzinfo=timezone.utc),
        datetime(2026, 8, 23, 0, 16, tzinfo=timezone.utc),
        datetime(2026, 8, 23, 0, 32, tzinfo=timezone.utc),
    ])
    app = create_app(
        tmp_path / "views.sqlite3", jra_fetcher=ViewFetcher(), now_provider=lambda: next(times),
    )
    with TestClient(app) as client:
        normal = client.post("/api/acquisition/jra/race-card", json={"url": NORMAL_SAPPORO_11_URL})
        detailed = client.post("/api/acquisition/jra/race-card", json={"url": DETAILED_URL})
        result = client.post("/api/acquisition/jra/race-card", json={"url": RESULT_URL})
        cards = client.get("/api/acquisition/jra/race-cards")

    assert normal.status_code == 201
    assert detailed.status_code == 201
    assert result.status_code == 201
    assert len({normal.json()["source"]["source_race_id"], detailed.json()["source"]["source_race_id"], result.json()["source"]["source_race_id"]}) == 1
    assert [(card["version"], card["status"]) for card in cards.json()] == [
        (1, "superseded"), (2, "superseded"), (3, "active"),
    ]


def test_expired_historical_card_without_runner_content_fails_closed(tmp_path: Path) -> None:
    expired = """<!doctype html><html lang="ja"><body data-keiba-related-menu="syutsuba">
    <div id="contentsBody"><div class="caution"><p>このレースの出馬表の掲載は終了しております。</p></div></div>
    </body></html>""".encode()
    app = create_app(tmp_path / "expired.sqlite3", jra_fetcher=FakeFetcher(FetchResponse(
        status=200, final_url=DETAILED_URL,
        headers={"content-type": "text/html; charset=utf-8"}, body=expired,
    )))

    with TestClient(app) as client:
        response = client.post("/api/acquisition/jra/race-card", json={"url": DETAILED_URL})
        cards = client.get("/api/acquisition/jra/race-cards")
        failures = client.get("/api/acquisition/jra/failures")

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "race_card_validation_failed"
    assert cards.json() == []
    assert failures.json()[0]["error_code"] == "race_card_validation_failed"


def test_jra_percent_decoding_of_the_same_allowed_url_is_not_treated_as_a_redirect(tmp_path: Path) -> None:
    encoded_url = DETAILED_URL.replace("/D1", "%2FD1")
    app = create_app(
        tmp_path / "decoded-url.sqlite3", jra_fetcher=FakeFetcher(FetchResponse(
            status=200, final_url=DETAILED_URL,
            headers={"content-type": "text/html; charset=utf-8"}, body=SAPPORO_11_FIXTURE,
        )), now_provider=lambda: datetime(2026, 8, 23, 0, 0, tzinfo=timezone.utc),
    )

    with TestClient(app) as client:
        response = client.post("/api/acquisition/jra/race-card", json={"url": encoded_url})

    assert response.status_code == 201, response.text
    assert response.json()["race"]["race_number"] == 11


def test_parser_v1_source_ids_are_canonicalized_during_database_upgrade(tmp_path: Path) -> None:
    database_path = tmp_path / "upgrade.sqlite3"
    app = create_app(database_path, jra_fetcher=FakeFetcher())
    with TestClient(app):
        pass
    with sqlite3.connect(database_path) as connection:
        connection.execute("UPDATE application_metadata SET value = '7' WHERE key = 'schema_version'")
        connection.execute(
            """INSERT INTO acquired_race_cards (
                organizer, country, racecourse, race_date, race_number, start_time, timezone,
                start_utc, surface, distance_m, going, field_size, source_url, source_race_id,
                received_at, source_updated_at, parser_version, response_sha256, validation_status,
                version, status, supersedes_card_id
            ) VALUES ('JRA', 'JP', '札幌', '2026-08-23', 11, '15:45', 'Asia/Tokyo',
                '2026-08-23T06:45:00Z', '芝', 1200, '良', 16, ?, ?,
                '2026-08-23T00:00:00Z', NULL, 'jra-race-card/1', ?, 'valid', 1, 'active', NULL)""",
            (NORMAL_SAPPORO_11_URL, "pw01dde0101202602021120260823/ZZ", "a" * 64),
        )

    upgraded = create_app(database_path, jra_fetcher=FakeFetcher())
    with TestClient(upgraded) as client:
        health = client.get("/api/health")
        cards = client.get("/api/acquisition/jra/race-cards")

    assert health.status_code == 200
    assert cards.json()[0]["source"]["source_race_id"] == "JRA-20260823-01-02-02-11"


def test_past_race_metadata_and_runners_are_acquired_from_the_result_page(tmp_path: Path) -> None:
    database_path = tmp_path / "past-result.sqlite3"
    current_result = RESULT_FIXTURE.replace(
        b'<td class="weight">56.0</td>',
        b'<td class="jockey">Sample Jockey</td><td class="weight">56.0</td>',
    )
    app = create_app(database_path, jra_fetcher=FakeFetcher(FetchResponse(
        status=200, final_url=RESULT_URL,
        headers={"content-type": "text/html; charset=utf-8"}, body=current_result,
    )))

    with TestClient(app) as client:
        response = client.post("/api/acquisition/jra/race-card", json={"url": RESULT_URL})

    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["source"]["source_race_id"] == "JRA-20260823-01-02-02-11"
    assert payload["race"]["racecourse"] == "札幌"
    assert payload["race"]["race_number"] == 11
    assert payload["race"]["field_size"] == 2
    assert [(runner["horse_number"], runner["horse_name"], runner["status"]) for runner in payload["runners"]] == [
        (1, "アサヒノソラ", "出走"), (2, "ツキノミチ", "取消"),
    ]
    assert "finish_position" not in str(payload)
    assert "payout" not in str(payload)
    assert list((tmp_path / "jra-html-cache").glob("*.html")) == []
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM result_versions").fetchone() == (0,)
        assert connection.execute("SELECT COUNT(*) FROM runner_results").fetchone() == (0,)


def test_detailed_result_page_url_shape_is_explicitly_supported(tmp_path: Path) -> None:
    app = create_app(tmp_path / "detailed-result.sqlite3", jra_fetcher=FakeFetcher(FetchResponse(
        status=200, final_url=DETAILED_RESULT_URL,
        headers={"content-type": "text/html; charset=utf-8"}, body=RESULT_FIXTURE,
    )))

    with TestClient(app) as client:
        response = client.post("/api/acquisition/jra/race-card", json={"url": DETAILED_RESULT_URL})

    assert response.status_code == 201, response.text
    assert response.json()["source"]["source_race_id"] == "JRA-20260823-01-02-02-11"
