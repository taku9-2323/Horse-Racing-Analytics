from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


SAMPLE_CSV = (Path(__file__).parents[2] / "examples" / "sample-race.csv").read_bytes()
RESULT_CSV = (
    "horse_number,finish_position,status,win_payout_per_100,place_payout_per_100\n"
    "1,1,確定,420,150\n2,2,確定,0,180\n3,3,確定,0,0\n"
    "4,4,確定,0,0\n5,5,確定,0,0\n"
).encode("utf-8")


def prepare_snapshot(client: TestClient) -> tuple[dict[str, object], dict[str, object]]:
    race_response = client.post(
        "/api/races/import", content=SAMPLE_CSV, headers={"Content-Type": "text/csv"}
    )
    assert race_response.status_code == 201
    race = race_response.json()
    snapshot_response = client.post(
        f"/api/races/{race['race_id']}/odds-snapshots",
        json={
            "observed_at": "2026-08-30T04:55:00Z",
            "source": "model-boundary-test",
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
    assert snapshot_response.status_code == 201
    return race, snapshot_response.json()


def fake_model_payload(*, output: str = "both") -> dict[str, object]:
    win = [0.42, 0.24, 0.16, 0.11, 0.07]
    place = [0.78, 0.62, 0.51, 0.39, 0.25]
    return {
        "model_identifier": "fake-independent-model",
        "model_version": "test-1",
        "prediction_as_of": "2026-08-30T04:54:00Z",
        "rationale": "固定fixtureによるHTTP境界検証",
        "runners": [
            {
                "horse_number": index + 1,
                **({"win_probability": win[index]} if output in ("win", "both") else {}),
                **({"place_probability": place[index]} if output in ("place", "both") else {}),
            }
            for index in range(5)
        ],
    }


def test_fake_independent_model_output_is_frozen_with_version_time_and_rationale(
    tmp_path: Path,
) -> None:
    app = create_app(
        tmp_path / "independent.sqlite3",
        now_provider=lambda: datetime(2026, 8, 30, 5, 0, tzinfo=timezone.utc),
    )

    with TestClient(app) as client:
        race, snapshot = prepare_snapshot(client)
        frozen = client.post(
            f"/api/odds-snapshots/{snapshot['id']}/independent-predictions/freeze",
            json=fake_model_payload(),
        )
        history = client.get(f"/api/races/{race['race_id']}/predictions")
        assert client.post(
            f"/api/races/{race['race_id']}/results/import",
            content=RESULT_CSV, headers={"Content-Type": "text/csv"},
        ).status_code == 201
        win_evaluation = client.get("/api/evaluation", params={
            "model_identifier": "fake-independent-model", "bet_type": "win",
        }).json()
        place_evaluation = client.get("/api/evaluation", params={
            "model_identifier": "fake-independent-model", "bet_type": "place",
        }).json()

    assert frozen.status_code == 201, frozen.text
    prediction = frozen.json()
    assert prediction["prediction_kind"] == "independent"
    assert prediction["input_snapshot_id"] == snapshot["id"]
    assert prediction["model_identifier"] == "fake-independent-model"
    assert prediction["model_version"] == "test-1"
    assert prediction["prediction_as_of"] == "2026-08-30T04:54:00Z"
    assert prediction["rationale"] == "固定fixtureによるHTTP境界検証"
    assert prediction["output_capabilities"] == ["win", "place"]
    assert prediction["official_evaluation_eligible"] is True
    assert prediction["runners"][0] == {
        "horse_number": 1,
        "win_probability": 0.42,
        "place_probability": 0.78,
    }
    assert history.status_code == 200
    assert history.json() == [prediction]
    assert win_evaluation["calibration"]["runner_count"] == 5
    assert win_evaluation["calibration"]["brier_score"] == pytest.approx(
        sum((probability - outcome) ** 2 for probability, outcome in zip(
            [0.42, 0.24, 0.16, 0.11, 0.07], [1, 0, 0, 0, 0], strict=True,
        )) / 5
    )
    assert place_evaluation["calibration"]["runner_count"] == 5
    assert place_evaluation["calibration"]["brier_score"] == pytest.approx(
        sum((probability - outcome) ** 2 for probability, outcome in zip(
            [0.78, 0.62, 0.51, 0.39, 0.25], [1, 1, 0, 0, 0], strict=True,
        )) / 5
    )


def test_independent_model_contract_accepts_win_or_place_only_and_rejects_mixed_outputs(
    tmp_path: Path,
) -> None:
    app = create_app(tmp_path / "capabilities.sqlite3")

    with TestClient(app) as client:
        _, snapshot = prepare_snapshot(client)
        win_only = client.post(
            f"/api/odds-snapshots/{snapshot['id']}/independent-predictions/freeze",
            json=fake_model_payload(output="win"),
        )
        place_only = client.post(
            f"/api/odds-snapshots/{snapshot['id']}/independent-predictions/freeze",
            json={**fake_model_payload(output="place"), "model_version": "test-2"},
        )
        mixed_payload = fake_model_payload(output="win")
        mixed_payload["runners"][0]["place_probability"] = 0.78  # type: ignore[index]
        mixed = client.post(
            f"/api/odds-snapshots/{snapshot['id']}/independent-predictions/freeze",
            json=mixed_payload,
        )

    assert win_only.status_code == 201, win_only.text
    assert win_only.json()["output_capabilities"] == ["win"]
    assert all(runner["place_probability"] is None for runner in win_only.json()["runners"])
    assert place_only.status_code == 201, place_only.text
    assert place_only.json()["output_capabilities"] == ["place"]
    assert all(runner["win_probability"] is None for runner in place_only.json()["runners"])
    assert mixed.status_code == 422


def test_independent_probabilities_cannot_be_submitted_as_market_values(tmp_path: Path) -> None:
    app = create_app(tmp_path / "separation.sqlite3")

    with TestClient(app) as client:
        _, snapshot = prepare_snapshot(client)
        payload = fake_model_payload()
        payload["runners"][0]["win_market_share"] = 0.42  # type: ignore[index]
        rejected_market_field = client.post(
            f"/api/odds-snapshots/{snapshot['id']}/independent-predictions/freeze",
            json=payload,
        )
        invalid_sum = fake_model_payload(output="win")
        invalid_sum["runners"][0]["win_probability"] = 0.5  # type: ignore[index]
        rejected_sum = client.post(
            f"/api/odds-snapshots/{snapshot['id']}/independent-predictions/freeze",
            json=invalid_sum,
        )

    assert rejected_market_field.status_code == 422
    assert rejected_sum.status_code == 422
