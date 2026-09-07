from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from app.jra_acquisition import FetchResponse
from app.main import create_app


FIXTURES = Path(__file__).parent / "fixtures"
CARD_HTML = (FIXTURES / "jra-race-card.html").read_bytes()
RESULT_HTML = (FIXTURES / "jra-race-result.html").read_bytes()
CARD_URL = "https://www.jra.go.jp/JRADB/accessD.html?CNAME=pw01dde0101202602021120260823/D1"
ODDS_URL = "https://www.jra.go.jp/JRADB/accessO.html?CNAME=pw151ouS301202602021120260823Z/76"
RESULT_URL = "https://www.jra.go.jp/JRADB/accessS.html?CNAME=pw01sde0101202602021120260823/AE"
CORRECTED_RESULT_URL = "https://www.jra.go.jp/JRADB/accessS.html?CNAME=pw01sde0101202602021120260823/AF"
WRONG_RACE_RESULT_URL = "https://www.jra.go.jp/JRADB/accessS.html?CNAME=pw01sde0101202602021020260823/AA"


def sapporo_card() -> bytes:
    return (CARD_HTML
        .replace("2026年8月30日（日曜） 3回架空2日".encode(), "2026年8月23日（日曜） 2回札幌2日".encode())
        .replace("7レース".encode(), "11レース".encode())
        .replace("13時25分".encode(), "15時45分".encode())
        .replace("1,800メートル（芝・右）".encode(), "1,200メートル（芝・右）".encode()))


ODDS_HTML = """<!doctype html><html><head><meta charset="utf-8"></head><body>
<h1>単勝・複勝オッズ（馬番順） 2026年8月23日（日曜） 2回札幌2日 11レース</h1>
<table><caption>単勝・複勝オッズ（馬番順）</caption>
<tr><td>枠1白</td><td>1</td><td>アサヒノソラ</td><td>4.2</td><td>1.2 - 1.4</td></tr>
<tr><td>枠2黒</td><td>2</td><td>ツキノミチ</td><td>8.0</td><td>2.0 - 2.5</td></tr>
</table></body></html>""".encode()


def five_runner_card() -> bytes:
    extra_rows = """
        <tr><td class="waku"><img alt="枠3赤"></td><td class="num">3</td><td class="horse"><span class="name">ミナモ</span></td><td class="jockey"><p class="age">牡4</p><p class="weight">57.0</p></td></tr>
        <tr><td class="waku"><img alt="枠4青"></td><td class="num">4</td><td class="horse"><span class="name">ホシカゲ</span></td><td class="jockey"><p class="age">牡4</p><p class="weight">57.0</p></td></tr>
        <tr><td class="waku"><img alt="枠5黄"></td><td class="num">5</td><td class="horse"><span class="name">ヤマナミ</span></td><td class="jockey"><p class="age">牡4</p><p class="weight">57.0</p></td></tr>
    """.encode()
    return (sapporo_card()
        .replace(b'class="field_size">2', b'class="field_size">5')
        .replace(b'<tr class="cancel">', b'<tr>')
        .replace('<span class="status">取消</span>'.encode(), b"")
        .replace(b"</tbody>", extra_rows + b"</tbody>", 1))


FIVE_ODDS_HTML = (ODDS_HTML
    .replace(b"</table>", """
<tr><td>枠3赤</td><td>3</td><td>ミナモ</td><td>10.0</td><td>3.0 - 4.0</td></tr>
<tr><td>枠4青</td><td>4</td><td>ホシカゲ</td><td>12.0</td><td>4.0 - 5.0</td></tr>
<tr><td>枠5黄</td><td>5</td><td>ヤマナミ</td><td>20.0</td><td>5.0 - 7.0</td></tr>
</table>""".encode(), 1))


def five_runner_result(*, dead_heat: bool = False, did_not_finish: bool = False) -> bytes:
    second_position = 1 if dead_heat else 2
    third_position = 3
    win_lines = (
        '<div class="line"><div class="num">1</div><div class="yen">210<span class="unit">円</span></div></div>'
        '<div class="line"><div class="num">2</div><div class="yen">210<span class="unit">円</span></div></div>'
        if dead_heat else
        '<div class="line"><div class="num">1</div><div class="yen">420<span class="unit">円</span></div></div>'
    )
    place_lines = (
        '<div class="line"><div class="num">1</div><div class="yen">130<span class="unit">円</span></div></div>'
        '<div class="line"><div class="num">2</div><div class="yen">140<span class="unit">円</span></div></div>'
    ) if dead_heat else (
        '<div class="line"><div class="num">1</div><div class="yen">150<span class="unit">円</span></div></div>'
        '<div class="line"><div class="num">2</div><div class="yen">180<span class="unit">円</span></div></div>'
    )
    return f"""<!doctype html><html lang="ja"><head><meta charset="utf-8"></head><body>
<div id="race_result"><table class="basic narrow-xy striped"><caption><div class="race_header">
<div class="date_line"><div class="cell date">2026年8月23日（日曜） 2回札幌2日</div></div>
<div class="race_number"><img alt="11レース"></div><span>発走時刻：15時45分</span>
<span>コース：1,200メートル（芝・右）</span><ul><li class="turf"><span class="txt">良</span></li></ul>
</div></caption><thead><tr><th class="place">着順</th><th class="waku">枠</th><th class="num">馬番</th><th class="horse">馬名</th><th class="age">性齢</th><th class="weight">負担重量</th></tr></thead><tbody>
<tr><td class="place">1</td><td class="waku"><img alt="枠1白"></td><td class="num">1</td><td class="horse">アサヒノソラ</td><td class="age">牡3</td><td class="weight">56.0</td></tr>
<tr><td class="place">{second_position}</td><td class="waku"><img alt="枠2黒"></td><td class="num">2</td><td class="horse">ツキノミチ</td><td class="age">牝4</td><td class="weight">54.0</td></tr>
<tr><td class="place">{third_position}</td><td class="waku"><img alt="枠3赤"></td><td class="num">3</td><td class="horse">ミナモ</td><td class="age">牡4</td><td class="weight">57.0</td></tr>
<tr><td class="place">{'中止' if did_not_finish else '4'}</td><td class="waku"><img alt="枠4青"></td><td class="num">4</td><td class="horse">ホシカゲ</td><td class="age">牡4</td><td class="weight">57.0</td></tr>
<tr><td class="place">{'4' if did_not_finish else '5'}</td><td class="waku"><img alt="枠5黄"></td><td class="num">5</td><td class="horse">ヤマナミ</td><td class="age">牡4</td><td class="weight">57.0</td></tr>
</tbody></table><div class="refund_area"><h2>払戻金</h2><ul>
<li class="win"><dl><dt>単勝</dt><dd>{win_lines}</dd></dl></li>
<li class="place"><dl><dt>複勝</dt><dd>{place_lines}</dd></dl></li>
</ul></div></div></body></html>""".encode()


class JraResultFetcher:
    def __init__(
        self, *, card_html: bytes | None = None, odds_html: bytes | None = None,
        result_pages: dict[str, bytes] | None = None, result_status: int = 200,
    ) -> None:
        self.card_html = card_html or sapporo_card()
        self.odds_html = odds_html or ODDS_HTML
        self.result_pages = result_pages or {RESULT_URL: RESULT_HTML}
        self.result_status = result_status

    def __call__(self, url: str) -> FetchResponse:
        if url == "https://www.jra.go.jp/robots.txt":
            return FetchResponse(200, url, {"content-type": "text/plain"}, b"User-agent: *\nDisallow:\n")
        body = self.result_pages.get(url, self.odds_html if url == ODDS_URL else self.card_html)
        status = self.result_status if url in self.result_pages else 200
        return FetchResponse(status, url, {"content-type": "text/html; charset=utf-8"}, body)


def registered_race(client: TestClient) -> int:
    card = client.post("/api/acquisition/jra/race-card", json={"url": CARD_URL})
    assert card.status_code == 201, card.text
    odds = client.post(
        f"/api/acquisition/jra/race-cards/{card.json()['card_id']}/odds",
        json={"url": ODDS_URL},
    )
    assert odds.status_code == 201, odds.text
    return int(odds.json()["race_id"])


def test_user_acquires_jra_result_and_atomically_settles_win_and_refund(tmp_path: Path) -> None:
    app = create_app(
        tmp_path / "jra-result.sqlite3", jra_fetcher=JraResultFetcher(),
        now_provider=lambda: datetime(2026, 8, 23, 8, 0, tzinfo=timezone.utc),
    )

    with TestClient(app) as client:
        race_id = registered_race(client)
        win_bet = client.post(f"/api/races/{race_id}/bets", json={
            "horse_number": 1, "bet_type": "win", "decision_type": "discretionary", "amount_yen": 200,
        })
        refund_bet = client.post(f"/api/races/{race_id}/bets", json={
            "horse_number": 2, "bet_type": "win", "decision_type": "discretionary", "amount_yen": 300,
        })
        acquired = client.post(f"/api/races/{race_id}/results/acquire", json={"url": RESULT_URL})
        ledger = client.get(f"/api/races/{race_id}/ledger")

    assert win_bet.status_code == refund_bet.status_code == 201
    assert acquired.status_code == 201, acquired.text
    payload = acquired.json()
    assert payload["result"]["version"] == 1
    assert payload["result"]["runners"] == [
        {"horse_number": 1, "finish_position": 1, "status": "確定", "win_payout_per_100": 420, "place_payout_per_100": 0},
        {"horse_number": 2, "finish_position": None, "status": "取消", "win_payout_per_100": 0, "place_payout_per_100": 0},
    ]
    assert payload["source"]["url"] == RESULT_URL
    assert payload["source"]["parser_version"] == "jra-result/1"
    assert payload["changes"] == []
    assert [(item["payout_yen"], item["refund_yen"], item["profit_yen"]) for item in ledger.json()["settlements"]] == [
        (840, 0, 640), (0, 300, 0),
    ]


def test_jra_result_saves_did_not_finish_without_treating_it_as_a_refund(tmp_path: Path) -> None:
    fetcher = JraResultFetcher(
        card_html=five_runner_card(), odds_html=FIVE_ODDS_HTML,
        result_pages={RESULT_URL: five_runner_result(did_not_finish=True)},
    )
    app = create_app(
        tmp_path / "jra-did-not-finish.sqlite3", jra_fetcher=fetcher,
        now_provider=lambda: datetime(2026, 8, 23, 8, 0, tzinfo=timezone.utc),
    )

    with TestClient(app) as client:
        race_id = registered_race(client)
        client.post(f"/api/races/{race_id}/bets", json={
            "horse_number": 4, "bet_type": "win", "decision_type": "discretionary", "amount_yen": 100,
        })
        acquired = client.post(f"/api/races/{race_id}/results/acquire", json={"url": RESULT_URL})
        ledger = client.get(f"/api/races/{race_id}/ledger").json()

    assert acquired.status_code == 201, acquired.text
    assert acquired.json()["result"]["runners"][3]["status"] == "競走中止"
    assert ledger["settlements"][0]["refund_yen"] == 0
    assert ledger["settlements"][0]["profit_yen"] == -100


def test_jra_dead_heat_correction_preserves_history_differences_and_frozen_prediction(tmp_path: Path) -> None:
    fetcher = JraResultFetcher(
        card_html=five_runner_card(), odds_html=FIVE_ODDS_HTML,
        result_pages={RESULT_URL: five_runner_result(), CORRECTED_RESULT_URL: five_runner_result(dead_heat=True)},
    )
    app = create_app(
        tmp_path / "jra-correction.sqlite3", jra_fetcher=fetcher,
        now_provider=lambda: datetime(2026, 8, 23, 8, 0, tzinfo=timezone.utc),
    )

    with TestClient(app) as client:
        race_id = registered_race(client)
        snapshot_id = client.get(f"/api/races/{race_id}/odds-snapshots").json()[-1]["id"]
        frozen = client.post(
            f"/api/odds-snapshots/{snapshot_id}/freeze",
            json={"model_identifier": "market-baseline", "model_version": "1.0"},
        ).json()
        client.post(f"/api/races/{race_id}/bets", json={
            "horse_number": 2, "bet_type": "win", "decision_type": "discretionary", "amount_yen": 100,
        })
        client.post(f"/api/races/{race_id}/bets", json={
            "horse_number": 2, "bet_type": "place", "decision_type": "discretionary", "amount_yen": 100,
        })
        original = client.post(f"/api/races/{race_id}/results/acquire", json={"url": RESULT_URL})
        corrected = client.post(
            f"/api/races/{race_id}/results/acquire", json={"url": CORRECTED_RESULT_URL},
        )
        versions = client.get(f"/api/races/{race_id}/results").json()
        predictions = client.get(f"/api/races/{race_id}/predictions").json()
        ledger = client.get(f"/api/races/{race_id}/ledger").json()

    assert original.status_code == corrected.status_code == 201
    assert corrected.json()["result"]["version"] == 2
    assert corrected.json()["result"]["correction_reason"] == "JRA公開ページの再取得で公式結果の変更を検出"
    assert any("2番 finish_position: 2→1" in change for change in corrected.json()["changes"])
    assert [(version["version"], version["status"]) for version in versions] == [(1, "superseded"), (2, "active")]
    assert predictions == [frozen]
    assert [(item["payout_yen"], item["profit_yen"]) for item in ledger["settlements"]] == [(210, 110), (140, 40)]


def test_invalid_jra_payout_is_rejected_without_result_or_settlement(tmp_path: Path) -> None:
    invalid_result = RESULT_HTML.replace(
        '<div class="num">1</div><div class="yen">420'.encode(),
        '<div class="num">2</div><div class="yen">420'.encode(),
    )
    app = create_app(
        tmp_path / "invalid-jra-result.sqlite3",
        jra_fetcher=JraResultFetcher(result_pages={RESULT_URL: invalid_result}),
        now_provider=lambda: datetime(2026, 8, 23, 8, 0, tzinfo=timezone.utc),
    )

    with TestClient(app) as client:
        race_id = registered_race(client)
        client.post(f"/api/races/{race_id}/bets", json={
            "horse_number": 1, "bet_type": "win", "decision_type": "discretionary", "amount_yen": 100,
        })
        rejected = client.post(f"/api/races/{race_id}/results/acquire", json={"url": RESULT_URL})
        ledger = client.get(f"/api/races/{race_id}/ledger").json()
        failures = client.get("/api/acquisition/jra/failures").json()

    assert rejected.status_code == 422
    assert rejected.json()["detail"]["code"] == "result_validation_failed"
    assert ledger["result_version"] is None
    assert ledger["settlements"] == []
    assert failures[-1]["parser_version"] == "jra-result/1"


def test_result_http_refusal_stops_with_audited_csv_fallback(tmp_path: Path) -> None:
    app = create_app(
        tmp_path / "stopped-jra-result.sqlite3",
        jra_fetcher=JraResultFetcher(result_status=429),
        now_provider=lambda: datetime(2026, 8, 23, 8, 0, tzinfo=timezone.utc),
    )

    with TestClient(app) as client:
        race_id = registered_race(client)
        stopped = client.post(f"/api/races/{race_id}/results/acquire", json={"url": RESULT_URL})
        ledger = client.get(f"/api/races/{race_id}/ledger").json()
        failures = client.get("/api/acquisition/jra/failures").json()

    assert stopped.status_code == 503
    assert stopped.json()["detail"] == {
        "code": "acquisition_stopped",
        "message": "JRAからの取得を停止しました。CSV取込を使用してください。",
    }
    assert ledger["result_version"] is None
    assert failures[-1]["parser_version"] == "jra-result/1"


def test_wrong_race_or_runner_set_commits_no_result_settlement_or_valid_observation(tmp_path: Path) -> None:
    wrong_race_page = RESULT_HTML.replace("11レース".encode(), "10レース".encode())
    extra_runner = """
        <tr><td class="place">2</td><td class="waku"><img alt="枠3赤"></td><td class="num">3</td><td class="horse">ミナモ</td><td class="age">牡4</td><td class="weight">57.0</td></tr>
    """.encode()
    wrong_runner_page = RESULT_HTML.replace(b"</tbody>", extra_runner + b"</tbody>", 1)
    cases = (
        ("wrong-race", WRONG_RACE_RESULT_URL, wrong_race_page, "result_race_mismatch"),
        ("wrong-runners", RESULT_URL, wrong_runner_page, "result_runner_mismatch"),
    )

    for name, url, page, expected_code in cases:
        app = create_app(
            tmp_path / f"{name}.sqlite3",
            jra_fetcher=JraResultFetcher(result_pages={url: page}),
            now_provider=lambda: datetime(2026, 8, 23, 8, 0, tzinfo=timezone.utc),
        )
        with TestClient(app) as client:
            race_id = registered_race(client)
            client.post(f"/api/races/{race_id}/bets", json={
                "horse_number": 1, "bet_type": "win", "decision_type": "discretionary", "amount_yen": 100,
            })
            rejected = client.post(f"/api/races/{race_id}/results/acquire", json={"url": url})
            ledger = client.get(f"/api/races/{race_id}/ledger").json()
            exported = client.get("/api/data/export?format=json").json()
            failures = client.get("/api/acquisition/jra/failures").json()

        assert rejected.status_code == 422
        assert rejected.json()["detail"]["code"] == expected_code
        assert ledger["result_version"] is None
        assert ledger["settlements"] == []
        assert exported["tables"]["jra_result_observations"] == []
        assert failures[-1]["error_code"] == expected_code
