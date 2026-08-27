from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app


SAMPLE_CSV = (Path(__file__).parents[2] / "examples" / "sample-race.csv").read_bytes()


def test_initial_analysis_tags_have_required_evidence_and_are_disabled(tmp_path: Path) -> None:
    app = create_app(tmp_path / "tags.sqlite3")

    with TestClient(app) as client:
        response = client.get("/api/analysis-tags")

    assert response.status_code == 200
    tags = response.json()
    assert len(tags) == 7
    assert {tag["rule_key"] for tag in tags} == {
        "market_odds_level", "odds_movement_snapshot", "relative_draw_context",
        "surface_going_course_distance", "age_sex_context",
        "assigned_weight_context", "field_size_place_rule",
    }
    for tag in tags:
        assert tag["version"] == 1
        assert tag["enabled"] is False
        assert tag["probability_multiplier"] is None
        assert tag["source_url"]
        assert tag["evidence_summary"]
        assert tag["study_period"]
        assert tag["population"]
        assert tag["evidence_quality"] in {"A", "B"}
        assert tag["conditions"]


def test_enabling_and_disabling_a_tag_creates_audit_events(tmp_path: Path) -> None:
    app = create_app(tmp_path / "tag-audit.sqlite3")

    with TestClient(app) as client:
        tag = client.get("/api/analysis-tags").json()[0]
        enabled = client.post(
            f"/api/analysis-tags/{tag['id']}/state",
            json={"enabled": True, "reason": "前向き検証を開始"},
        )
        disabled = client.post(
            f"/api/analysis-tags/{tag['id']}/state",
            json={"enabled": False, "reason": "検証期間を終了"},
        )
        audit = client.get(f"/api/analysis-tags/{tag['rule_key']}/audit")

    assert enabled.status_code == 200
    assert enabled.json()["enabled"] is True
    assert disabled.status_code == 200
    assert disabled.json()["enabled"] is False
    assert [(event["action"], event["reason"]) for event in audit.json()] == [
        ("enabled", "前向き検証を開始"),
        ("disabled", "検証期間を終了"),
    ]


def test_condition_change_creates_a_disabled_new_version_without_overwriting_old(tmp_path: Path) -> None:
    app = create_app(tmp_path / "tag-version.sqlite3")

    with TestClient(app) as client:
        original = client.get("/api/analysis-tags").json()[0]
        client.post(
            f"/api/analysis-tags/{original['id']}/state",
            json={"enabled": True, "reason": "検証開始"},
        )
        replacement = client.post(
            f"/api/analysis-tags/{original['id']}/versions",
            json={
                "conditions": {"filters": [{
                    "field": "win_odds", "operator": "range", "value": [1.0, 3.0],
                }]},
                "reason": "検証帯を事前固定",
            },
        )
        versions = client.get(f"/api/analysis-tags/{original['rule_key']}/versions")
        audit = client.get(f"/api/analysis-tags/{original['rule_key']}/audit")

    assert replacement.status_code == 201
    new_version = replacement.json()
    assert new_version["id"] != original["id"]
    assert new_version["version"] == 2
    assert new_version["enabled"] is False
    assert new_version["probability_multiplier"] is None
    assert versions.status_code == 200
    old, new = versions.json()
    assert old["enabled"] is False
    assert old["conditions"] == original["conditions"]
    assert new["conditions"] == {"filters": [{
        "field": "win_odds", "operator": "range", "value": [1.0, 3.0],
    }]}
    assert [event["action"] for event in audit.json()] == ["enabled", "disabled", "version_created"]
    assert audit.json()[-1]["action"] == "version_created"
    assert audit.json()[-1]["reason"] == "検証帯を事前固定"


def test_enabling_tag_only_rule_does_not_change_prediction_or_candidates(tmp_path: Path) -> None:
    app = create_app(tmp_path / "tag-no-probability.sqlite3")

    with TestClient(app) as client:
        race = client.post(
            "/api/races/import", content=SAMPLE_CSV,
            headers={"Content-Type": "text/csv"},
        ).json()
        snapshot = client.post(
            f"/api/races/{race['race_id']}/odds-snapshots",
            json={
                "observed_at": "2026-08-30T05:00:00Z",
                "source": "test",
                "runners": [{
                    "horse_number": runner["horse_number"],
                    "win_odds": runner["win_odds"],
                    "place_odds_min": runner["place_odds_min"],
                    "place_odds_max": runner["place_odds_max"],
                } for runner in race["runners"]],
            },
        ).json()
        before = client.post(
            f"/api/odds-snapshots/{snapshot['id']}/freeze",
            json={"model_identifier": "market-baseline", "model_version": "1.0"},
        ).json()
        tag = client.get("/api/analysis-tags").json()[0]
        client.post(
            f"/api/analysis-tags/{tag['id']}/state",
            json={"enabled": True, "reason": "層別記録のみ"},
        )
        after = client.post(
            f"/api/odds-snapshots/{snapshot['id']}/freeze",
            json={"model_identifier": "market-baseline", "model_version": "1.0"},
        ).json()
        analysis = client.get(f"/api/races/{race['race_id']}").json()

    assert before["runners"] == after["runners"]
    assert before["analysis_tags"] == []
    assert len(after["analysis_tags"]) == 1
    assert after["analysis_tags"][0]["rule_key"] == "market_odds_level"
    assert after["analysis_tags"][0]["version"] == 1
    assert after["analysis_tags"][0]["context"]["runners"][0]["win_odds"] > 0
    assert analysis["candidate_status"] == "期待値候補なし"


def test_saved_conditions_control_matches_and_invalid_conditions_are_rejected(tmp_path: Path) -> None:
    app = create_app(tmp_path / "tag-conditions.sqlite3")

    with TestClient(app) as client:
        tags = client.get("/api/analysis-tags").json()
        original = next(tag for tag in tags if tag["rule_key"] == "market_odds_level")
        invalid = client.post(
            f"/api/analysis-tags/{original['id']}/versions",
            json={"conditions": {"win_odds_bands": [1, 3]}, "reason": "未対応形式"},
        )
        invalid_type = client.post(
            f"/api/analysis-tags/{original['id']}/versions",
            json={"conditions": {"filters": [{
                "field": "win_odds", "operator": "gt", "value": "abc",
            }]}, "reason": "型不正"},
        )
        replacement = client.post(
            f"/api/analysis-tags/{original['id']}/versions",
            json={"conditions": {"filters": [{
                "field": "win_odds", "operator": "range", "value": [1000, 2000],
            }]}, "reason": "該当なし条件"},
        ).json()
        client.post(
            f"/api/analysis-tags/{replacement['id']}/state",
            json={"enabled": True, "reason": "条件一致テスト"},
        )
        race = client.post(
            "/api/races/import", content=SAMPLE_CSV, headers={"Content-Type": "text/csv"},
        ).json()
        snapshot = client.post(
            f"/api/races/{race['race_id']}/odds-snapshots",
            json={"observed_at": "2026-08-30T05:00:00Z", "source": "test",
                  "runners": [{key: runner[key] for key in (
                      "horse_number", "win_odds", "place_odds_min", "place_odds_max",
                  )} for runner in race["runners"]]},
        ).json()
        prediction = client.post(
            f"/api/odds-snapshots/{snapshot['id']}/freeze",
            json={"model_identifier": "market-baseline", "model_version": "1.0"},
        ).json()

    assert invalid.status_code == 422
    assert invalid_type.status_code == 422
    assert invalid_type.json()["detail"]["code"] == "invalid_tag_conditions"
    assert prediction["analysis_tags"] == []


def test_odds_movement_tag_requires_two_pre_start_snapshots_and_records_remaining_time(tmp_path: Path) -> None:
    app = create_app(tmp_path / "movement-tag.sqlite3")

    with TestClient(app) as client:
        race = client.post(
            "/api/races/import", content=SAMPLE_CSV, headers={"Content-Type": "text/csv"},
        ).json()
        movement = next(
            tag for tag in client.get("/api/analysis-tags").json()
            if tag["rule_key"] == "odds_movement_snapshot"
        )
        client.post(
            f"/api/analysis-tags/{movement['id']}/state",
            json={"enabled": True, "reason": "時点間検証"},
        )
        runner_odds = [{key: runner[key] for key in (
            "horse_number", "win_odds", "place_odds_min", "place_odds_max",
        )} for runner in race["runners"]]
        first = client.post(
            f"/api/races/{race['race_id']}/odds-snapshots",
            json={"observed_at": "2026-08-30T04:00:00Z", "source": "test", "runners": runner_odds},
        ).json()
        first_prediction = client.post(
            f"/api/odds-snapshots/{first['id']}/freeze",
            json={"model_identifier": "market-baseline", "model_version": "1.0"},
        ).json()
        second = client.post(
            f"/api/races/{race['race_id']}/odds-snapshots",
            json={"observed_at": "2026-08-30T05:00:00Z", "source": "test", "runners": runner_odds},
        ).json()
        second_prediction = client.post(
            f"/api/odds-snapshots/{second['id']}/freeze",
            json={"model_identifier": "market-baseline", "model_version": "1.0"},
        ).json()
        post_start = client.post(
            f"/api/races/{race['race_id']}/odds-snapshots",
            json={"observed_at": "2026-08-30T07:00:00Z", "source": "test", "runners": runner_odds},
        ).json()
        post_start_prediction = client.post(
            f"/api/odds-snapshots/{post_start['id']}/freeze",
            json={"model_identifier": "market-baseline", "model_version": "1.0"},
        ).json()

    assert first_prediction["analysis_tags"] == []
    assert second_prediction["analysis_tags"][0]["rule_key"] == "odds_movement_snapshot"
    assert second_prediction["analysis_tags"][0]["context"]["seconds_until_start"] == 6000
    assert post_start_prediction["analysis_tags"] == []
