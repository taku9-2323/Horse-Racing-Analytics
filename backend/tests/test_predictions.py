from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app


SAMPLE_CSV = (Path(__file__).parents[2] / "examples" / "sample-race.csv").read_bytes()


class MutableClock:
    def __init__(self, current: datetime) -> None:
        self.current = current

    def __call__(self) -> datetime:
        return self.current


def import_race(client: TestClient) -> dict[str, object]:
    response = client.post(
        "/api/races/import",
        content=SAMPLE_CSV,
        headers={"Content-Type": "text/csv"},
    )
    assert response.status_code == 201
    return response.json()


def snapshot_payload(analysis: dict[str, object], observed_at: str, odds_delta: float = 0) -> dict[str, object]:
    runners = analysis["runners"]
    assert isinstance(runners, list)
    return {
        "observed_at": observed_at,
        "source": "user_csv",
        "runners": [
            {
                "horse_number": runner["horse_number"],
                "win_odds": runner["win_odds"] + odds_delta,
                "place_odds_min": runner["place_odds_min"],
                "place_odds_max": runner["place_odds_max"],
            }
            for runner in runners
        ],
    }


def test_user_can_save_multiple_odds_snapshots_with_observed_and_received_times(tmp_path: Path) -> None:
    clock = MutableClock(datetime(2026, 8, 30, 5, 0, tzinfo=timezone.utc))
    app = create_app(tmp_path / "snapshots.sqlite3", now_provider=clock)

    with TestClient(app) as client:
        race = import_race(client)
        race_id = race["race_id"]
        first = client.post(
            f"/api/races/{race_id}/odds-snapshots",
            json=snapshot_payload(race, "2026-08-30T04:55:00Z"),
        )
        clock.current = datetime(2026, 8, 30, 5, 30, tzinfo=timezone.utc)
        second = client.post(
            f"/api/races/{race_id}/odds-snapshots",
            json=snapshot_payload(race, "2026-08-30T05:25:00Z", 0.5),
        )
        listed = client.get(f"/api/races/{race_id}/odds-snapshots")

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["observed_at"] == "2026-08-30T04:55:00Z"
    assert first.json()["received_at"] == "2026-08-30T05:00:00Z"
    assert second.json()["received_at"] == "2026-08-30T05:30:00Z"
    assert [item["id"] for item in listed.json()] == [first.json()["id"], second.json()["id"]]


def test_user_can_freeze_a_market_baseline_prediction_and_cannot_edit_it(tmp_path: Path) -> None:
    clock = MutableClock(datetime(2026, 8, 30, 5, 0, tzinfo=timezone.utc))
    app = create_app(tmp_path / "freeze.sqlite3", now_provider=clock)

    with TestClient(app) as client:
        race = import_race(client)
        snapshot = client.post(
            f"/api/races/{race['race_id']}/odds-snapshots",
            json=snapshot_payload(race, "2026-08-30T04:55:00Z"),
        ).json()
        frozen = client.post(
            f"/api/odds-snapshots/{snapshot['id']}/freeze",
            json={"model_identifier": "market-baseline", "model_version": "1.0"},
        )
        edit = client.put(
            f"/api/predictions/{frozen.json()['id']}",
            json={"model_version": "changed"},
        )

    assert frozen.status_code == 201
    prediction = frozen.json()
    assert prediction["input_snapshot_id"] == snapshot["id"]
    assert prediction["model_identifier"] == "market-baseline"
    assert prediction["model_version"] == "1.0"
    assert prediction["frozen_at"] == "2026-08-30T05:00:00Z"
    assert prediction["status"] == "active"
    assert prediction["official_evaluation_eligible"] is True
    assert sum(item["win_market_share"] for item in prediction["runners"]) == 1.0
    assert edit.status_code == 409
    assert edit.json()["detail"]["code"] == "frozen_prediction_immutable"


def test_correction_invalidates_old_prediction_and_post_start_version_is_excluded(tmp_path: Path) -> None:
    clock = MutableClock(datetime(2026, 8, 30, 5, 0, tzinfo=timezone.utc))
    app = create_app(tmp_path / "correction.sqlite3", now_provider=clock)

    with TestClient(app) as client:
        race = import_race(client)
        first_snapshot = client.post(
            f"/api/races/{race['race_id']}/odds-snapshots",
            json=snapshot_payload(race, "2026-08-30T04:55:00Z"),
        ).json()
        original = client.post(
            f"/api/odds-snapshots/{first_snapshot['id']}/freeze",
            json={"model_identifier": "market-baseline", "model_version": "1.0"},
        ).json()
        clock.current = datetime(2026, 8, 30, 7, 0, tzinfo=timezone.utc)
        corrected_snapshot = client.post(
            f"/api/races/{race['race_id']}/odds-snapshots",
            json=snapshot_payload(race, "2026-08-30T05:30:00Z", 0.5),
        ).json()
        corrected = client.post(
            f"/api/predictions/{original['id']}/correct",
            json={
                "reason": "オッズ入力誤りの訂正",
                "input_snapshot_id": corrected_snapshot["id"],
            },
        )
        history = client.get(f"/api/races/{race['race_id']}/predictions")

    assert corrected.status_code == 201
    replacement = corrected.json()
    assert replacement["replaces_prediction_id"] == original["id"]
    assert replacement["model_version"] == original["model_version"]
    assert replacement["official_evaluation_eligible"] is False
    assert replacement["evaluation_exclusion_reason"] == "発走後に固定された事後訂正"
    assert history.status_code == 200
    old, new = history.json()
    assert old["status"] == "invalidated"
    assert old["invalidation_reason"] == "オッズ入力誤りの訂正"
    assert old["official_evaluation_eligible"] is False
    assert old["evaluation_exclusion_reason"] == "理由付きで無効化された旧版"
    assert new["id"] == replacement["id"]


def test_blank_correction_reason_is_rejected_without_invalidating_original(tmp_path: Path) -> None:
    clock = MutableClock(datetime(2026, 8, 30, 5, 0, tzinfo=timezone.utc))
    app = create_app(tmp_path / "blank-reason.sqlite3", now_provider=clock)

    with TestClient(app) as client:
        race = import_race(client)
        snapshot = client.post(
            f"/api/races/{race['race_id']}/odds-snapshots",
            json=snapshot_payload(race, "2026-08-30T04:55:00Z"),
        ).json()
        original = client.post(
            f"/api/odds-snapshots/{snapshot['id']}/freeze",
            json={"model_identifier": "market-baseline", "model_version": "1.0"},
        ).json()
        rejected = client.post(
            f"/api/predictions/{original['id']}/correct",
            json={"reason": "   ", "input_snapshot_id": snapshot["id"]},
        )
        history = client.get(f"/api/races/{race['race_id']}/predictions").json()

    assert rejected.status_code == 422
    assert len(history) == 1
    assert history[0]["status"] == "active"


def test_initial_prediction_frozen_after_start_is_not_called_a_correction(tmp_path: Path) -> None:
    clock = MutableClock(datetime(2026, 8, 30, 7, 0, tzinfo=timezone.utc))
    app = create_app(tmp_path / "post-start.sqlite3", now_provider=clock)

    with TestClient(app) as client:
        race = import_race(client)
        snapshot = client.post(
            f"/api/races/{race['race_id']}/odds-snapshots",
            json=snapshot_payload(race, "2026-08-30T06:50:00Z"),
        ).json()
        frozen = client.post(
            f"/api/odds-snapshots/{snapshot['id']}/freeze",
            json={"model_identifier": "market-baseline", "model_version": "1.0"},
        ).json()

    assert frozen["official_evaluation_eligible"] is False
    assert frozen["evaluation_exclusion_reason"] == "発走後に固定されたため公式評価対象外"
