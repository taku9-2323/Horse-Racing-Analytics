from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from fastapi.testclient import TestClient

from app.main import create_app


SAMPLE_CSV = (Path(__file__).parents[2] / "examples" / "sample-race.csv").read_bytes()


def result_csv(
    field_size: int = 5, statuses: dict[int, str] | None = None,
    winner_payout: int = 420,
) -> bytes:
    status_by_horse = statuses or {}
    lines = ["horse_number,finish_position,status,win_payout_per_100,place_payout_per_100"]
    for horse_number in range(1, field_size + 1):
        status = status_by_horse.get(horse_number, "確定")
        if status != "確定":
            lines.append(f"{horse_number},,{status},0,0")
            continue
        finish = horse_number
        win_payout = winner_payout if finish == 1 else 0
        place_payout = (150 if finish == 1 else 180 if finish == 2 else 0) if field_size > 4 else 0
        lines.append(f"{horse_number},{finish},確定,{win_payout},{place_payout}")
    return ("\n".join(lines) + "\n").encode()


def create_frozen_run(
    client: TestClient, race_id: int, rule_version_id: int, observed_at: str,
    win_odds_by_horse: dict[int, float] | None = None,
) -> dict[str, object]:
    race = client.get(f"/api/races/{race_id}").json()
    snapshot = client.post(f"/api/races/{race_id}/odds-snapshots", json={
        "observed_at": observed_at,
        "source": "synthetic rule-performance fixture",
        "runners": [{
            "horse_number": runner["horse_number"],
            "win_odds": (win_odds_by_horse or {}).get(runner["horse_number"], runner["win_odds"]),
            "place_odds_min": runner["place_odds_min"],
            "place_odds_max": runner["place_odds_max"],
        } for runner in race["runners"]],
    })
    assert snapshot.status_code == 201
    frozen = client.post(f"/api/races/{race_id}/rule-judgements/freeze", json={
        "snapshot_id": snapshot.json()["id"],
        "rule_version_id": rule_version_id,
        "judgement_as_of": observed_at,
    })
    assert frozen.status_code == 201
    return frozen.json()


def add_valid_jra_observation(database_path: Path, result_version_id: int) -> None:
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """INSERT INTO jra_result_observations (
                   result_version_id, source_url, source_race_id, received_at,
                   source_updated_at, parser_version, response_sha256, validation_status
               ) VALUES (?, ?, ?, ?, ?, ?, ?, 'valid')""",
            (
                result_version_id, "https://example.invalid/synthetic-result",
                "synthetic-race", "2026-08-30T07:00:00Z", None,
                "synthetic-test", "a" * 64,
            ),
        )


def add_second_rule_version(database_path: Path) -> int:
    with sqlite3.connect(database_path) as connection:
        cursor = connection.execute(
            """INSERT INTO rule_versions (
                   rule_key, version, title, conditions_json, priority_json,
                   missing_policy, vocabulary_json, allowed_fields_json, created_at
               )
               SELECT rule_key, version + 1, title || '（履歴評価）', conditions_json,
                      priority_json, missing_policy, vocabulary_json,
                      allowed_fields_json, '2026-09-01T00:00:00Z'
               FROM rule_versions WHERE id=1"""
        )
        assert cursor.lastrowid is not None
        return int(cursor.lastrowid)


def test_rule_performance_uses_one_latest_saved_run_and_official_ticket_payouts(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "rule-performance.sqlite3"
    clock = [datetime(2026, 8, 30, 5, 15, tzinfo=timezone.utc)]
    app = create_app(database_path, now_provider=lambda: clock[0])
    with TestClient(app) as client:
        race_id = client.post(
            "/api/races/import", content=SAMPLE_CSV,
            headers={"Content-Type": "text/csv"},
        ).json()["race_id"]
        rule_version_id = client.get("/api/rule-versions").json()[0]["id"]
        first_run = create_frozen_run(
            client, race_id, rule_version_id, "2026-08-30T05:00:00Z",
        )
        latest_run = create_frozen_run(
            client, race_id, rule_version_id, "2026-08-30T05:10:00Z",
            {1: 2.0, 2: 20.0, 3: 30.0, 4: 40.0, 5: 50.0},
        )
        imported_result = client.post(
            f"/api/races/{race_id}/results/import", content=result_csv(),
            headers={"Content-Type": "text/csv"},
        )
        assert imported_result.status_code == 201
        add_valid_jra_observation(database_path, imported_result.json()["id"])

        with sqlite3.connect(database_path) as connection:
            before = {
                table: connection.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
                for table in (
                    "rule_versions", "rule_judgement_runs", "runner_rule_judgements",
                    "result_versions", "runner_results", "jra_result_observations",
                    "bets", "settlements",
                )
            }
        response = client.get("/api/rule-performance", params={
            "race_date_from": "2026-08-30", "race_date_to": "2026-08-30",
        })
        with sqlite3.connect(database_path) as connection:
            after = {
                table: connection.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
                for table in before
            }

    assert response.status_code == 200, response.text
    report = response.json()
    assert len(report["groups"]) == 1
    group = report["groups"][0]
    assert group["rule_version"]["id"] == rule_version_id
    assert group["counts"]["candidate_runs"] == 2
    assert group["counts"]["selected_runs"] == 1
    assert group["counts"]["duplicate_runs_excluded"] == 1
    assert group["counts"]["selected_races"] == 1
    assert group["counts"]["selected_snapshots"] == 1
    assert group["counts"]["selected_runner_observations"] == 5
    assert group["counts"]["attention_runners"] == 1
    assert group["selected_runs"] == [{
        "race_id": race_id,
        "race_date": "2026-08-30",
        "racecourse": "東京",
        "race_number": 11,
        "judgement_run_id": latest_run["id"],
        "input_snapshot_id": latest_run["input_snapshot_id"],
        "frozen_at": "2026-08-30T05:15:00Z",
        "active_result_version_id": imported_result.json()["id"],
        "active_result_version_number": imported_result.json()["version"],
    }]
    assert group["bet_types"]["win"]["candidate_tickets"] == 1
    assert group["bet_types"]["win"]["evaluated_tickets"] == 1
    assert group["bet_types"]["win"]["hit_tickets"] == 1
    assert group["bet_types"]["win"]["stake_yen"] == 100
    assert group["bet_types"]["win"]["payout_yen"] == 420
    assert group["bet_types"]["win"]["return_rate"] == 4.2
    assert group["bet_types"]["place"]["candidate_tickets"] == 1
    assert group["bet_types"]["place"]["hit_tickets"] == 1
    assert group["bet_types"]["place"]["payout_yen"] == 150
    assert group["bet_types"]["place"]["return_rate"] == 1.5
    assert before == after
    assert latest_run["id"] != first_run["id"]


def test_rule_versions_are_separate_and_date_filters_are_inclusive(tmp_path: Path) -> None:
    database_path = tmp_path / "rule-performance-versions.sqlite3"
    app = create_app(
        database_path,
        now_provider=lambda: datetime(2026, 8, 30, 5, 15, tzinfo=timezone.utc),
    )
    with TestClient(app) as client:
        race_id = client.post(
            "/api/races/import", content=SAMPLE_CSV,
            headers={"Content-Type": "text/csv"},
        ).json()["race_id"]
        first_version_id = client.get("/api/rule-versions").json()[0]["id"]
        second_version_id = add_second_rule_version(database_path)
        create_frozen_run(
            client, race_id, first_version_id, "2026-08-30T05:00:00Z",
        )
        create_frozen_run(
            client, race_id, second_version_id, "2026-08-30T05:10:00Z",
        )
        with sqlite3.connect(database_path) as connection:
            connection.execute(
                "UPDATE rule_versions SET conditions_json=? WHERE id=?",
                ('{"attention":[{"field":"market_rank","operator":"lte","value":1},'
                 '{"field":"win_odds","operator":"lte","value":1}]}', second_version_id),
            )
        all_versions = client.get("/api/rule-performance", params={
            "race_date_from": "2026-08-30", "race_date_to": "2026-08-30",
        })
        filtered = client.get("/api/rule-performance", params={
            "race_date_from": "2026-08-31", "rule_version_id": first_version_id,
        })

    assert all_versions.status_code == 200
    groups = all_versions.json()["groups"]
    assert [group["rule_version"]["id"] for group in groups] == [
        first_version_id, second_version_id,
    ]
    assert all(group["counts"]["selected_races"] == 1 for group in groups)
    assert groups[0]["counts"]["attention_runners"] == 2
    assert groups[1]["counts"]["attention_runners"] == 2
    assert groups[0]["bet_types"]["win"]["candidate_tickets"] == 2
    assert groups[1]["bet_types"]["win"]["candidate_tickets"] == 2
    assert filtered.status_code == 200
    assert len(filtered.json()["groups"]) == 1
    assert filtered.json()["groups"][0]["status"] == "no_runs"


def test_pending_and_unverified_results_are_not_counted_as_losses(tmp_path: Path) -> None:
    database_path = tmp_path / "rule-performance-pending.sqlite3"
    app = create_app(
        database_path,
        now_provider=lambda: datetime(2026, 8, 30, 5, 15, tzinfo=timezone.utc),
    )
    with TestClient(app) as client:
        race_id = client.post(
            "/api/races/import", content=SAMPLE_CSV,
            headers={"Content-Type": "text/csv"},
        ).json()["race_id"]
        rule_version_id = client.get("/api/rule-versions").json()[0]["id"]
        create_frozen_run(client, race_id, rule_version_id, "2026-08-30T05:00:00Z")
        pending = client.get("/api/rule-performance", params={"rule_version_id": rule_version_id})
        imported_result = client.post(
            f"/api/races/{race_id}/results/import", content=result_csv(),
            headers={"Content-Type": "text/csv"},
        )
        assert imported_result.status_code == 201
        unverified = client.get("/api/rule-performance", params={"rule_version_id": rule_version_id})

    pending_group = pending.json()["groups"][0]
    assert pending_group["selected_runs"][0]["active_result_version_id"] is None
    assert pending_group["selected_runs"][0]["active_result_version_number"] is None
    assert pending_group["result_exclusion_races"] == {"result_pending": 1}
    assert pending_group["bet_types"]["win"]["evaluated_tickets"] == 0
    assert pending_group["bet_types"]["win"]["hit_rate"] is None
    assert pending_group["bet_types"]["win"]["stake_yen"] is None
    unverified_group = unverified.json()["groups"][0]
    assert unverified_group["result_exclusion_races"] == {"result_source_unverified": 1}
    assert unverified_group["bet_types"]["win"]["evaluated_tickets"] == 0
    assert unverified_group["bet_types"]["win"]["hit_tickets"] == 0
    assert unverified_group["bet_types"]["win"]["payout_yen"] is None


def test_place_market_unavailable_and_refunds_stay_unverified(tmp_path: Path) -> None:
    database_path = tmp_path / "rule-performance-place.sqlite3"
    four_runner_csv = b"\n".join(SAMPLE_CSV.splitlines()[:5]) + b"\n"
    app = create_app(
        database_path,
        now_provider=lambda: datetime(2026, 8, 30, 5, 15, tzinfo=timezone.utc),
    )
    with TestClient(app) as client:
        race_id = client.post(
            "/api/races/import", content=four_runner_csv,
            headers={"Content-Type": "text/csv"},
        ).json()["race_id"]
        rule_version_id = client.get("/api/rule-versions").json()[0]["id"]
        create_frozen_run(client, race_id, rule_version_id, "2026-08-30T05:00:00Z")
        pending_result = client.get("/api/rule-performance", params={"rule_version_id": rule_version_id})
        imported_result = client.post(
            f"/api/races/{race_id}/results/import", content=result_csv(field_size=4),
            headers={"Content-Type": "text/csv"},
        )
        assert imported_result.status_code == 201
        add_valid_jra_observation(database_path, imported_result.json()["id"])
        unavailable = client.get("/api/rule-performance", params={"rule_version_id": rule_version_id})

    pending_group = pending_result.json()["groups"][0]
    assert pending_group["result_exclusion_races"] == {"result_pending": 1}
    assert pending_group["bet_types"]["win"]["exclusions"] == {"result_pending": 2}
    assert pending_group["bet_types"]["place"]["status"] == "market_unavailable"
    assert pending_group["bet_types"]["place"]["exclusions"] == {"place_market_unavailable": 2}
    place = unavailable.json()["groups"][0]["bet_types"]["place"]
    assert place["status"] == "market_unavailable"
    assert place["candidate_tickets"] == 2
    assert place["evaluated_tickets"] == 0
    assert place["hit_rate"] is None
    assert place["stake_yen"] is None
    assert place["payout_yen"] is None
    assert place["return_rate"] is None
    assert place["exclusions"] == {"place_market_unavailable": 2}

    refund_database_path = tmp_path / "rule-performance-refund.sqlite3"
    refund_app = create_app(
        refund_database_path,
        now_provider=lambda: datetime(2026, 8, 30, 5, 15, tzinfo=timezone.utc),
    )
    with TestClient(refund_app) as client:
        race_id = client.post(
            "/api/races/import", content=SAMPLE_CSV,
            headers={"Content-Type": "text/csv"},
        ).json()["race_id"]
        rule_version_id = client.get("/api/rule-versions").json()[0]["id"]
        create_frozen_run(client, race_id, rule_version_id, "2026-08-30T05:00:00Z")
        imported_result = client.post(
            f"/api/races/{race_id}/results/import",
            content=result_csv(statuses={1: "取消"}),
            headers={"Content-Type": "text/csv"},
        )
        assert imported_result.status_code == 201
        add_valid_jra_observation(refund_database_path, imported_result.json()["id"])
        refund_report = client.get("/api/rule-performance", params={"rule_version_id": rule_version_id})

    win = refund_report.json()["groups"][0]["bet_types"]["win"]
    assert win["candidate_tickets"] == 2
    assert win["evaluated_tickets"] == 1
    assert win["refund_unverified_tickets"] == 1
    assert win["refund_yen"] is None
    assert win["refund_state"] == "unverified"
    assert win["exclusions"] == {"refund_unverified": 1}


def test_mixed_place_market_and_unverified_candidates_are_not_all_unavailable(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "rule-performance-mixed-place.sqlite3"
    four_runner_csv = b"\n".join(SAMPLE_CSV.splitlines()[:5]) + b"\n"
    five_runner_csv = SAMPLE_CSV.replace(b"2026-08-30", b"2026-08-31")
    app = create_app(
        database_path,
        now_provider=lambda: datetime(2026, 8, 30, 5, 15, tzinfo=timezone.utc),
    )
    with TestClient(app) as client:
        four_runner_race_id = client.post(
            "/api/races/import", content=four_runner_csv,
            headers={"Content-Type": "text/csv"},
        ).json()["race_id"]
        five_runner_race_id = client.post(
            "/api/races/import", content=five_runner_csv,
            headers={"Content-Type": "text/csv"},
        ).json()["race_id"]
        rule_version_id = client.get("/api/rule-versions").json()[0]["id"]
        create_frozen_run(client, four_runner_race_id, rule_version_id, "2026-08-30T05:00:00Z")
        create_frozen_run(client, five_runner_race_id, rule_version_id, "2026-08-30T05:10:00Z")
        result = client.post(
            f"/api/races/{five_runner_race_id}/results/import", content=result_csv(),
            headers={"Content-Type": "text/csv"},
        )
        assert result.status_code == 201
        report = client.get("/api/rule-performance", params={"rule_version_id": rule_version_id})

    group = report.json()["groups"][0]
    place = group["bet_types"]["place"]
    assert place["candidate_tickets"] == 4
    assert place["evaluated_tickets"] == 0
    assert place["status"] == "no_evaluated_tickets"
    assert place["exclusions"] == {
        "place_market_unavailable": 2,
        "result_source_unverified": 2,
    }


def test_after_start_correction_does_not_revive_invalidated_pre_race_run(tmp_path: Path) -> None:
    clock = [datetime(2026, 8, 30, 5, 15, tzinfo=timezone.utc)]
    app = create_app(tmp_path / "rule-performance-invalidated.sqlite3", now_provider=lambda: clock[0])
    with TestClient(app) as client:
        race_id = client.post(
            "/api/races/import", content=SAMPLE_CSV,
            headers={"Content-Type": "text/csv"},
        ).json()["race_id"]
        rule_version_id = client.get("/api/rule-versions").json()[0]["id"]
        original = create_frozen_run(
            client, race_id, rule_version_id, "2026-08-30T05:00:00Z",
        )
        clock[0] = datetime(2026, 8, 30, 6, 45, tzinfo=timezone.utc)
        correction = client.post(f"/api/rule-judgements/{original['id']}/correct", json={
            "snapshot_id": original["input_snapshot_id"],
            "rule_version_id": rule_version_id,
            "judgement_as_of": "2026-08-30T05:00:00Z",
            "reason": "発走後の再固定テスト",
        })
        report = client.get("/api/rule-performance", params={"rule_version_id": rule_version_id})

    assert correction.status_code == 201
    group = report.json()["groups"][0]
    assert group["status"] == "no_runs"
    assert group["counts"]["selected_runs"] == 0
    assert group["counts"]["ineligible_runs_excluded"] == 1
    assert group["counts"]["invalidated_runs_excluded"] == 1
    assert group["counts"]["invalidated_without_active_pre_race_run"] == 1


def test_corrected_active_result_uses_only_its_exact_source_observation(tmp_path: Path) -> None:
    database_path = tmp_path / "rule-performance-correction.sqlite3"
    app = create_app(
        database_path,
        now_provider=lambda: datetime(2026, 8, 30, 5, 15, tzinfo=timezone.utc),
    )
    with TestClient(app) as client:
        race_id = client.post(
            "/api/races/import", content=SAMPLE_CSV,
            headers={"Content-Type": "text/csv"},
        ).json()["race_id"]
        rule_version_id = client.get("/api/rule-versions").json()[0]["id"]
        create_frozen_run(client, race_id, rule_version_id, "2026-08-30T05:00:00Z")
        original = client.post(
            f"/api/races/{race_id}/results/import", content=result_csv(),
            headers={"Content-Type": "text/csv"},
        )
        assert original.status_code == 201
        add_valid_jra_observation(database_path, original.json()["id"])
        correction = client.post(
            f"/api/races/{race_id}/results/correct?reason=synthetic-correction",
            content=result_csv(winner_payout=400),
            headers={"Content-Type": "text/csv"},
        )
        assert correction.status_code == 201
        report = client.get("/api/rule-performance", params={"rule_version_id": rule_version_id})

    group = report.json()["groups"][0]
    assert group["corrected_result_races"] == 1, group
    assert group["selected_runs"][0]["active_result_version_id"] == correction.json()["id"]
    assert group["selected_runs"][0]["active_result_version_number"] == correction.json()["version"]
    assert group["result_exclusion_races"] == {"result_source_unverified": 1}
    assert group["bet_types"]["win"]["evaluated_tickets"] == 0
    assert group["bet_types"]["win"]["payout_yen"] is None


def test_missing_official_runner_result_is_incomplete_not_a_loss(tmp_path: Path) -> None:
    database_path = tmp_path / "rule-performance-incomplete.sqlite3"
    app = create_app(
        database_path,
        now_provider=lambda: datetime(2026, 8, 30, 5, 15, tzinfo=timezone.utc),
    )
    with TestClient(app) as client:
        race_id = client.post(
            "/api/races/import", content=SAMPLE_CSV,
            headers={"Content-Type": "text/csv"},
        ).json()["race_id"]
        rule_version_id = client.get("/api/rule-versions").json()[0]["id"]
        create_frozen_run(client, race_id, rule_version_id, "2026-08-30T05:00:00Z")
        imported_result = client.post(
            f"/api/races/{race_id}/results/import", content=result_csv(),
            headers={"Content-Type": "text/csv"},
        )
        assert imported_result.status_code == 201
        result_version_id = imported_result.json()["id"]
        add_valid_jra_observation(database_path, result_version_id)
        with sqlite3.connect(database_path) as connection:
            connection.execute(
                "DELETE FROM runner_results WHERE result_version_id=? AND horse_number=2",
                (result_version_id,),
            )
        response = client.get("/api/rule-performance", params={"rule_version_id": rule_version_id})

    group = response.json()["groups"][0]
    assert group["result_exclusion_races"] == {"result_incomplete": 1}
    assert group["bet_types"]["win"]["evaluated_tickets"] == 0
    assert group["bet_types"]["win"]["hit_tickets"] == 0
    assert group["bet_types"]["win"]["stake_yen"] is None


def test_verified_did_not_finish_counts_as_a_loss_not_a_refund(tmp_path: Path) -> None:
    database_path = tmp_path / "rule-performance-did-not-finish.sqlite3"
    app = create_app(
        database_path,
        now_provider=lambda: datetime(2026, 8, 30, 5, 15, tzinfo=timezone.utc),
    )
    with TestClient(app) as client:
        race_id = client.post(
            "/api/races/import", content=SAMPLE_CSV,
            headers={"Content-Type": "text/csv"},
        ).json()["race_id"]
        rule_version_id = client.get("/api/rule-versions").json()[0]["id"]
        create_frozen_run(client, race_id, rule_version_id, "2026-08-30T05:00:00Z")
        imported_result = client.post(
            f"/api/races/{race_id}/results/import",
            content=result_csv(statuses={2: "競走中止"}),
            headers={"Content-Type": "text/csv"},
        )
        assert imported_result.status_code == 201
        add_valid_jra_observation(database_path, imported_result.json()["id"])
        with sqlite3.connect(database_path) as connection:
            connection.execute(
                """UPDATE runner_results
                   SET win_payout_per_100=999, place_payout_per_100=999
                   WHERE result_version_id=? AND horse_number=2""",
                (imported_result.json()["id"],),
            )
        response = client.get("/api/rule-performance", params={"rule_version_id": rule_version_id})

    win = response.json()["groups"][0]["bet_types"]["win"]
    assert win["candidate_tickets"] == 2
    assert win["evaluated_tickets"] == 2
    assert win["hit_tickets"] == 1
    assert win["refund_unverified_tickets"] == 0
    assert win["payout_yen"] == 420


def test_rule_performance_rejects_reversed_or_timezone_free_filters(tmp_path: Path) -> None:
    app = create_app(tmp_path / "rule-performance-filter-validation.sqlite3")
    with TestClient(app) as client:
        reversed_days = client.get("/api/rule-performance", params={
            "race_date_from": "2026-08-31", "race_date_to": "2026-08-30",
        })
        timezone_free = client.get("/api/rule-performance", params={
            "frozen_at_from": "2026-08-30T05:00:00",
        })
        unknown_version = client.get("/api/rule-performance", params={"rule_version_id": 999})

    assert reversed_days.status_code == 422
    assert reversed_days.json()["detail"]["code"] == "invalid_race_date_range"
    assert timezone_free.status_code == 422
    assert timezone_free.json()["detail"]["code"] == "timezone_required"
    assert unknown_version.status_code == 404
    assert unknown_version.json()["detail"]["code"] == "rule_version_not_found"
