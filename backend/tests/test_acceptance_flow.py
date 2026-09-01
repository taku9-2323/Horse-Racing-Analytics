from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from app.jra_acquisition import FetchResponse
from app.main import create_app


SAMPLE_CSV = (Path(__file__).parents[2] / "examples" / "sample-race.csv").read_text(
    encoding="utf-8"
)


def race_csv(*, racecourse: str, race_number: int, start_utc: str) -> bytes:
    return (
        SAMPLE_CSV.replace("東京", racecourse)
        .replace(",11,", f",{race_number},")
        .replace("2026-08-30T06:40:00Z", start_utc)
        .encode("utf-8")
    )


def result_csv(*, excluded_horse: int | None = None) -> bytes:
    rows = [
        "horse_number,finish_position,status,win_payout_per_100,place_payout_per_100",
        "1,1,確定,420,150",
        "2,2,確定,0,180",
        "3,3,確定,0,0",
        "4,4,確定,0,0",
        "5,5,確定,0,0",
    ]
    if excluded_horse is not None:
        rows[excluded_horse] = f"{excluded_horse},,除外,0,0"
    return ("\n".join(rows) + "\n").encode("utf-8")


def import_and_freeze(client: TestClient, content: bytes) -> dict[str, object]:
    imported = client.post(
        "/api/races/import", content=content, headers={"Content-Type": "text/csv"}
    )
    assert imported.status_code == 201, imported.text
    race = imported.json()
    snapshot = client.post(
        f"/api/races/{race['race_id']}/odds-snapshots",
        json={
            "observed_at": "2026-08-30T05:00:00Z",
            "source": "acceptance-csv-fallback",
            "runners": [
                {
                    "horse_number": runner["horse_number"],
                    "win_odds": runner["win_odds"],
                    "place_odds_min": runner["place_odds_min"],
                    "place_odds_max": runner["place_odds_max"],
                }
                for runner in race["runners"]
            ],
        },
    )
    assert snapshot.status_code == 201, snapshot.text
    frozen = client.post(
        f"/api/odds-snapshots/{snapshot.json()['id']}/freeze",
        json={"model_identifier": "market-baseline", "model_version": "1.0"},
    )
    assert frozen.status_code == 201, frozen.text
    assert race["candidate_status"] == "期待値候補なし"
    return race


def test_multiple_race_acceptance_flow_survives_backup_restore(tmp_path: Path) -> None:
    def stopped_fetcher(url: str) -> FetchResponse:
        return FetchResponse(
            status=429,
            final_url=url,
            headers={"content-type": "text/html; charset=utf-8"},
            body=b"rate limited",
        )

    app = create_app(
        tmp_path / "acceptance.sqlite3",
        now_provider=lambda: datetime(2026, 8, 30, 5, 5, tzinfo=timezone.utc),
        jra_fetcher=stopped_fetcher,
    )

    with TestClient(app) as client:
        stopped = client.post(
            "/api/acquisition/jra/race-card",
            json={
                "url": "https://www.jra.go.jp/JRADB/accessD.html?"
                "CNAME=pw01dde0199202603020720260830/AA"
            },
        )
        assert stopped.status_code == 503
        assert stopped.json()["detail"]["code"] == "acquisition_stopped"

        normal = import_and_freeze(
            client,
            race_csv(racecourse="東京", race_number=11, start_utc="2026-08-30T06:40:00Z"),
        )
        no_purchase = import_and_freeze(
            client,
            race_csv(racecourse="札幌", race_number=10, start_utc="2026-08-30T06:30:00Z"),
        )
        refund = import_and_freeze(
            client,
            race_csv(racecourse="新潟", race_number=9, start_utc="2026-08-30T06:20:00Z"),
        )

        assert client.post(
            f"/api/races/{normal['race_id']}/bets",
            json={
                "horse_number": 1,
                "bet_type": "win",
                "decision_type": "discretionary",
                "amount_yen": 200,
            },
        ).status_code == 201
        assert client.post(
            f"/api/races/{refund['race_id']}/bets",
            json={
                "horse_number": 4,
                "bet_type": "win",
                "decision_type": "discretionary",
                "amount_yen": 500,
            },
        ).status_code == 201

        for race, results in (
            (normal, result_csv()),
            (no_purchase, result_csv()),
            (refund, result_csv(excluded_horse=4)),
        ):
            settled = client.post(
                f"/api/races/{race['race_id']}/results/import",
                content=results,
                headers={"Content-Type": "text/csv"},
            )
            assert settled.status_code == 201, settled.text

        expected_races = client.get("/api/races").json()
        expected_evaluation = client.get("/api/evaluation").json()
        expected_ledgers = {
            race["race_id"]: client.get(f"/api/races/{race['race_id']}/ledger").json()
            for race in (normal, no_purchase, refund)
        }

        assert len(expected_races) == 3
        assert expected_ledgers[normal["race_id"]]["totals"] == {
            "stake_yen": 200,
            "payout_yen": 840,
            "refund_yen": 0,
            "profit_yen": 640,
            "return_rate": 4.2,
        }
        assert expected_ledgers[no_purchase["race_id"]]["bets"] == []
        assert expected_ledgers[no_purchase["race_id"]]["totals"]["return_rate"] is None
        assert expected_ledgers[refund["race_id"]]["totals"] == {
            "stake_yen": 500,
            "payout_yen": 0,
            "refund_yen": 500,
            "profit_yen": 0,
            "return_rate": 1.0,
        }
        assert expected_evaluation["calibration"]["eligible_prediction_runs"] == 3
        # The scratched runner is preserved in the race but excluded from calibration.
        assert expected_evaluation["calibration"]["runner_count"] == 14
        assert expected_evaluation["returns"]["candidate"]["stake_yen"] == 0
        assert expected_evaluation["returns"]["discretionary"] == {
            "stake_yen": 700,
            "payout_yen": 840,
            "refund_yen": 500,
            "profit_yen": 640,
            "return_rate": 1340 / 700,
        }

        backup = client.post("/api/data/backups")
        assert backup.status_code == 201, backup.text
        extra_race = client.post(
            "/api/races/import",
            content=race_csv(
                racecourse="中山", race_number=8, start_utc="2026-08-30T06:10:00Z"
            ),
            headers={"Content-Type": "text/csv"},
        )
        assert extra_race.status_code == 201

        restored = client.post(f"/api/data/backups/{backup.json()['id']}/restore")
        assert restored.status_code == 200, restored.text
        assert client.get("/api/races").json() == expected_races
        assert client.get("/api/evaluation").json() == expected_evaluation
        for race_id, ledger in expected_ledgers.items():
            assert client.get(f"/api/races/{race_id}/ledger").json() == ledger

        exported = client.get("/api/data/export?format=json").json()
        assert len(exported["tables"]["races"]) == 3
        assert len(exported["tables"]["runners"]) == 15
        assert len(exported["tables"]["prediction_runs"]) == 3
        assert len(exported["tables"]["result_versions"]) == 3
