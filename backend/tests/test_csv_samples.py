from pathlib import Path
from fastapi.testclient import TestClient
from app.main import create_app
from app.race_analysis import CSV_COLUMNS
from app.bets import RESULT_COLUMNS


def test_canonical_samples_match_headers_and_import_through_http(tmp_path: Path) -> None:
    examples = Path(__file__).parents[2] / "examples"
    race = (examples / "sample-race.csv").read_bytes()
    results = (examples / "sample-results.csv").read_bytes()
    assert set(race.decode("utf-8").splitlines()[0].split(",")) == CSV_COLUMNS
    assert set(results.decode("utf-8").splitlines()[0].split(",")) == RESULT_COLUMNS
    with TestClient(create_app(tmp_path / "samples.sqlite3")) as client:
        imported = client.post("/api/races/import", content=race, headers={"Content-Type": "text/csv; charset=utf-8"})
        assert imported.status_code == 201, imported.text
        race_id = imported.json()["race_id"]
        settled = client.post(f"/api/races/{race_id}/results/import", content=results, headers={"Content-Type": "text/csv; charset=utf-8"})
        assert settled.status_code == 201, settled.text
        assert len(settled.json()["runners"]) == 5
