from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app


SAMPLE_CSV = (Path(__file__).parents[2] / "examples" / "sample-race.csv").read_bytes()


def import_race(client: TestClient) -> dict[str, object]:
    response = client.post(
        "/api/races/import", content=SAMPLE_CSV,
        headers={"Content-Type": "text/csv"},
    )
    assert response.status_code == 201
    return response.json()


def results_csv(
    *, horse_1_win: int = 420, horse_2_place: int = 180,
    horse_4_status: str = "確定", horse_4_finish: str = "4",
) -> bytes:
    return (
        "horse_number,finish_position,status,win_payout_per_100,place_payout_per_100\n"
        f"1,1,確定,{horse_1_win},150\n"
        f"2,2,確定,0,{horse_2_place}\n"
        "3,3,確定,0,0\n"
        f"4,{horse_4_finish},{horse_4_status},0,0\n"
        "5,5,確定,0,0\n"
    ).encode("utf-8")


def test_real_bets_are_discretionary_until_a_persisted_candidate_exists_and_use_100_yen_units(tmp_path: Path) -> None:
    app = create_app(tmp_path / "bets.sqlite3")

    with TestClient(app) as client:
        race = import_race(client)
        race_id = race["race_id"]
        unavailable_candidate = client.post(f"/api/races/{race_id}/bets", json={
            "horse_number": 1, "bet_type": "win", "decision_type": "candidate", "amount_yen": 200,
        })
        discretionary = client.post(f"/api/races/{race_id}/bets", json={
            "horse_number": 2, "bet_type": "place", "decision_type": "discretionary", "amount_yen": 300,
        })
        invalid = [client.post(f"/api/races/{race_id}/bets", json={
            "horse_number": 1, "bet_type": "win", "decision_type": "candidate", "amount_yen": amount,
        }) for amount in (0, -100, 150)]
        ledger = client.get(f"/api/races/{race_id}/ledger")

    assert unavailable_candidate.status_code == 409
    assert unavailable_candidate.json()["detail"]["code"] == "candidate_not_available"
    assert discretionary.status_code == 201
    assert discretionary.json()["decision_type"] == "discretionary"
    assert [response.status_code for response in invalid] == [422, 422, 422]
    assert [bet["amount_yen"] for bet in ledger.json()["bets"]] == [300]
    assert ledger.json()["totals"] == {
        "stake_yen": 300, "payout_yen": 0, "refund_yen": 0,
        "profit_yen": None, "return_rate": None,
    }


def test_official_payouts_settle_wins_losses_and_totals_by_decision_type(tmp_path: Path) -> None:
    app = create_app(tmp_path / "settlement.sqlite3")

    with TestClient(app) as client:
        race_id = import_race(client)["race_id"]
        for payload in (
            {"horse_number": 1, "bet_type": "win", "decision_type": "discretionary", "amount_yen": 200},
            {"horse_number": 2, "bet_type": "place", "decision_type": "discretionary", "amount_yen": 300},
            {"horse_number": 3, "bet_type": "win", "decision_type": "discretionary", "amount_yen": 100},
        ):
            assert client.post(f"/api/races/{race_id}/bets", json=payload).status_code == 201
        imported = client.post(
            f"/api/races/{race_id}/results/import", content=results_csv(),
            headers={"Content-Type": "text/csv"},
        )
        ledger = client.get(f"/api/races/{race_id}/ledger")

    assert imported.status_code == 201
    assert imported.json()["version"] == 1
    assert [result["finish_position"] for result in imported.json()["runners"][:3]] == [1, 2, 3]
    settlements = ledger.json()["settlements"]
    assert [(item["stake_yen"], item["payout_yen"], item["refund_yen"], item["profit_yen"]) for item in settlements] == [
        (200, 840, 0, 640), (300, 540, 0, 240), (100, 0, 0, -100),
    ]
    assert ledger.json()["totals"] == {
        "stake_yen": 600, "payout_yen": 1380, "refund_yen": 0,
        "profit_yen": 780, "return_rate": 2.3,
    }
    assert ledger.json()["by_decision_type"]["candidate"]["return_rate"] is None
    assert ledger.json()["by_decision_type"]["discretionary"]["return_rate"] == 2.3


def test_scratched_or_excluded_bet_is_refunded_in_full(tmp_path: Path) -> None:
    app = create_app(tmp_path / "refund.sqlite3")

    with TestClient(app) as client:
        race_id = import_race(client)["race_id"]
        client.post(f"/api/races/{race_id}/bets", json={
            "horse_number": 4, "bet_type": "win", "decision_type": "discretionary", "amount_yen": 500,
        })
        imported = client.post(
            f"/api/races/{race_id}/results/import",
            content=results_csv(horse_4_status="除外", horse_4_finish=""),
            headers={"Content-Type": "text/csv"},
        )
        ledger = client.get(f"/api/races/{race_id}/ledger").json()

    assert imported.status_code == 201
    assert ledger["settlements"][0] == {
        "id": ledger["settlements"][0]["id"], "bet_id": ledger["bets"][0]["id"],
        "result_version_id": imported.json()["id"], "stake_yen": 500,
        "payout_yen": 0, "refund_yen": 500, "profit_yen": 0,
    }
    assert ledger["totals"]["return_rate"] == 1.0


def test_invalid_result_csv_is_rejected_atomically(tmp_path: Path) -> None:
    app = create_app(tmp_path / "invalid-results.sqlite3")

    with TestClient(app) as client:
        race_id = import_race(client)["race_id"]
        client.post(f"/api/races/{race_id}/bets", json={
            "horse_number": 1, "bet_type": "win", "decision_type": "discretionary", "amount_yen": 100,
        })
        invalid_csv = results_csv().replace(b"5,5,", b"5,not-a-position,")
        rejected = client.post(
            f"/api/races/{race_id}/results/import", content=invalid_csv,
            headers={"Content-Type": "text/csv"},
        )
        ledger = client.get(f"/api/races/{race_id}/ledger").json()

    assert rejected.status_code == 422
    assert rejected.json()["detail"]["code"] == "result_csv_validation_failed"
    assert ledger["result_version"] is None
    assert ledger["settlements"] == []


def test_result_correction_preserves_old_version_and_replaces_settlement(tmp_path: Path) -> None:
    app = create_app(tmp_path / "result-correction.sqlite3")

    with TestClient(app) as client:
        race_id = import_race(client)["race_id"]
        client.post(f"/api/races/{race_id}/bets", json={
            "horse_number": 1, "bet_type": "win", "decision_type": "discretionary", "amount_yen": 100,
        })
        original = client.post(
            f"/api/races/{race_id}/results/import", content=results_csv(horse_1_win=420),
            headers={"Content-Type": "text/csv"},
        )
        blank_reason = client.post(
            f"/api/races/{race_id}/results/correct?reason=%20%20", content=results_csv(horse_1_win=400),
            headers={"Content-Type": "text/csv"},
        )
        corrected = client.post(
            f"/api/races/{race_id}/results/correct?reason=JRA%E7%99%BA%E8%A1%A8%E5%80%A4%E3%82%92%E8%A8%82%E6%AD%A3",
            content=results_csv(horse_1_win=400), headers={"Content-Type": "text/csv"},
        )
        versions = client.get(f"/api/races/{race_id}/results").json()
        ledger = client.get(f"/api/races/{race_id}/ledger").json()

    assert original.status_code == 201
    assert blank_reason.status_code == 422
    assert corrected.status_code == 201
    assert corrected.json()["version"] == 2
    assert corrected.json()["correction_reason"] == "JRA発表値を訂正"
    assert [(version["version"], version["status"]) for version in versions] == [
        (1, "superseded"), (2, "active"),
    ]
    assert len(ledger["settlements"]) == 1
    assert ledger["settlements"][0]["payout_yen"] == 400
    assert ledger["totals"]["profit_yen"] == 300


def test_identical_result_reimport_is_a_successful_no_op(tmp_path: Path) -> None:
    app = create_app(tmp_path / "idempotent-result.sqlite3")

    with TestClient(app) as client:
        race_id = import_race(client)["race_id"]
        original = client.post(
            f"/api/races/{race_id}/results/import", content=results_csv(),
            headers={"Content-Type": "text/csv"},
        )
        retried = client.post(
            f"/api/races/{race_id}/results/import", content=results_csv(),
            headers={"Content-Type": "text/csv"},
        )
        versions = client.get(f"/api/races/{race_id}/results").json()

    assert original.status_code == 201
    assert retried.status_code == 201
    assert retried.json()["id"] == original.json()["id"]
    assert len(versions) == 1


def test_reasoned_correction_accepts_dead_heat_and_official_payouts(tmp_path: Path) -> None:
    app = create_app(tmp_path / "dead-heat.sqlite3")
    dead_heat = results_csv(horse_1_win=210).replace(
        "2,2,確定,0,180".encode(), "2,1,確定,210,180".encode()
    )

    with TestClient(app) as client:
        race_id = import_race(client)["race_id"]
        for horse_number in (1, 2):
            client.post(f"/api/races/{race_id}/bets", json={
                "horse_number": horse_number, "bet_type": "win",
                "decision_type": "discretionary", "amount_yen": 100,
            })
        client.post(
            f"/api/races/{race_id}/results/import", content=results_csv(),
            headers={"Content-Type": "text/csv"},
        )
        corrected = client.post(
            f"/api/races/{race_id}/results/correct?reason=%E5%90%8C%E7%9D%80%E3%81%AE%E5%85%AC%E5%BC%8F%E6%89%95%E6%88%BB",
            content=dead_heat, headers={"Content-Type": "text/csv"},
        )
        ledger = client.get(f"/api/races/{race_id}/ledger").json()

    assert corrected.status_code == 201
    assert [runner["finish_position"] for runner in corrected.json()["runners"][:2]] == [1, 1]
    assert [settlement["payout_yen"] for settlement in ledger["settlements"]] == [210, 210]
