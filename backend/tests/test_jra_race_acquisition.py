from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from app.jra_acquisition import FetchResponse
from app.main import create_app


FIXTURE = (Path(__file__).parent / "fixtures" / "jra-race-card.html").read_bytes()
URL = "https://www.jra.go.jp/JRADB/accessD.html?CNAME=pw01dde0199202603020720260830/AA"


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
        "url": URL, "source_race_id": "pw01dde0199202603020720260830/AA",
        "received_at": "2026-08-29T04:00:00Z",
        "source_updated_at": None, "parser_version": "jra-race-card/1",
        "response_sha256": payload["source"]["response_sha256"],
        "validation_status": "valid",
    }
    assert len(payload["source"]["response_sha256"]) == 64
    assert fetcher.urls == ["https://www.jra.go.jp/robots.txt", URL]


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
    app = create_app(tmp_path / "updated.sqlite3", jra_fetcher=FakeFetcher(FetchResponse(
        status=200, final_url=URL, headers={"content-type": "text/html; charset=utf-8"}, body=updated,
    )))

    with TestClient(app) as client:
        response = client.post("/api/acquisition/jra/race-card", json={"url": URL})

    assert response.status_code == 201
    assert response.json()["source"]["source_updated_at"] == "2026-08-29T03:55:00Z"
