import csv
from datetime import date, datetime, time
from io import StringIO
from typing import Any

from pydantic import BaseModel


CSV_COLUMNS = {
    "organizer", "country", "racecourse", "race_date", "race_number",
    "start_time", "timezone", "start_utc", "surface", "distance_m", "going",
    "gate", "horse_number", "horse_name", "age", "sex", "assigned_weight",
    "status", "win_odds", "place_odds_min", "place_odds_max",
}


class RaceSummary(BaseModel):
    organizer: str
    country: str
    racecourse: str
    race_date: str
    race_number: int
    start_time: str
    timezone: str
    start_utc: str
    surface: str
    distance_m: int
    going: str
    field_size: int


class PlaceBreakEvenHitRate(BaseModel):
    minimum: float
    midpoint: float
    maximum: float


class RunnerAnalysis(BaseModel):
    horse_number: int
    horse_name: str
    win_odds: float
    raw_inverse_win_odds: float
    normalized_win_market_share: float
    place_odds_min: float
    place_odds_max: float
    place_break_even_hit_rate: PlaceBreakEvenHitRate | None


class RaceAnalysis(BaseModel):
    race_id: int
    race: RaceSummary
    runners: list[RunnerAnalysis]
    candidate_status: str
    candidate_reason: str


def parse_race_csv(content: bytes) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("CSVはUTF-8で保存してください。") from error

    reader = csv.DictReader(StringIO(text))
    if reader.fieldnames is None or set(reader.fieldnames) != CSV_COLUMNS:
        raise ValueError("CSVの見出しが所定の形式と一致しません。")

    parsed: list[dict[str, Any]] = []
    for line_number, row in enumerate(reader, start=2):
        try:
            timezone = required(row, "timezone")
            if "/" not in timezone:
                raise ValueError("timezoneはIANAタイムゾーン名です。")
            start_utc = datetime.fromisoformat(
                required(row, "start_utc").replace("Z", "+00:00")
            )
            if start_utc.utcoffset() is None:
                raise ValueError("start_utcにはUTCオフセットが必要です。")
            surface = required(row, "surface")
            sex = required(row, "sex")
            status = required(row, "status")
            if surface not in {"芝", "ダート"}:
                raise ValueError("surfaceは芝またはダートです。")
            if sex not in {"牡", "牝", "セン"}:
                raise ValueError("sexは牡、牝、センのいずれかです。")
            if status not in {"出走", "取消", "除外"}:
                raise ValueError("statusは出走、取消、除外のいずれかです。")
            parsed.append({
                "organizer": required(row, "organizer"),
                "country": required(row, "country"),
                "racecourse": required(row, "racecourse"),
                "race_date": date.fromisoformat(required(row, "race_date")).isoformat(),
                "race_number": int(required(row, "race_number")),
                "start_time": time.fromisoformat(required(row, "start_time")).strftime("%H:%M"),
                "timezone": timezone,
                "start_utc": start_utc.isoformat().replace("+00:00", "Z"),
                "surface": surface,
                "distance_m": int(required(row, "distance_m")),
                "going": required(row, "going"),
                "gate": int(required(row, "gate")),
                "horse_number": int(required(row, "horse_number")),
                "horse_name": required(row, "horse_name"),
                "age": int(required(row, "age")),
                "sex": sex,
                "assigned_weight": float(required(row, "assigned_weight")),
                "status": status,
                "win_odds": float(required(row, "win_odds")),
                "place_odds_min": float(required(row, "place_odds_min")),
                "place_odds_max": float(required(row, "place_odds_max")),
            })
        except (TypeError, ValueError) as error:
            raise ValueError(f"{line_number}行目の値を確認してください。") from error

    if not parsed:
        raise ValueError("CSVに出走馬がありません。")
    race_keys = (
        "organizer", "country", "racecourse", "race_date", "race_number",
        "start_time", "timezone", "start_utc", "surface", "distance_m", "going",
    )
    first = parsed[0]
    if any(any(row[key] != first[key] for key in race_keys) for row in parsed[1:]):
        raise ValueError("1つのCSVには1レースだけを記載してください。")
    if any(
        row["win_odds"] <= 0
        or row["place_odds_min"] <= 0
        or row["place_odds_max"] < row["place_odds_min"]
        for row in parsed
    ):
        raise ValueError("オッズは正の値で、複勝下限は上限以下にしてください。")

    raw_inverse_win_odds = [1 / float(row["win_odds"]) for row in parsed]
    inverse_total = sum(raw_inverse_win_odds)
    race = {key: first[key] for key in race_keys}
    runners = [
        {
            **{key: row[key] for key in (
                "gate", "horse_number", "horse_name", "age", "sex",
                "assigned_weight", "status", "win_odds", "place_odds_min",
                "place_odds_max",
            )},
            "raw_inverse_win_odds": raw,
            "normalized_win_market_share": raw / inverse_total,
        }
        for row, raw in zip(parsed, raw_inverse_win_odds, strict=True)
    ]
    return race, runners


def required(row: dict[str, str | None], key: str) -> str:
    value = row.get(key)
    if value is None or not value.strip():
        raise ValueError(f"{key}は必須です。")
    return value.strip()


def build_analysis(race_id: int, race: Any, runners: list[Any]) -> RaceAnalysis:
    supports_place = len(runners) >= 5
    runner_analyses = [
        RunnerAnalysis(
            horse_number=int(runner["horse_number"]),
            horse_name=str(runner["horse_name"]),
            win_odds=float(runner["win_odds"]),
            raw_inverse_win_odds=float(runner["raw_inverse_win_odds"]),
            normalized_win_market_share=float(runner["normalized_win_market_share"]),
            place_odds_min=float(runner["place_odds_min"]),
            place_odds_max=float(runner["place_odds_max"]),
            place_break_even_hit_rate=(PlaceBreakEvenHitRate(
                minimum=1 / float(runner["place_odds_max"]),
                midpoint=1 / ((float(runner["place_odds_min"]) + float(runner["place_odds_max"])) / 2),
                maximum=1 / float(runner["place_odds_min"]),
            ) if supports_place else None),
        )
        for runner in runners
    ]
    return RaceAnalysis(
        race_id=race_id,
        race=RaceSummary(
            organizer=str(race["organizer"]), country=str(race["country"]),
            racecourse=str(race["racecourse"]), race_date=str(race["race_date"]),
            race_number=int(race["race_number"]), start_time=str(race["start_time"]),
            timezone=str(race["timezone"]), start_utc=str(race["start_utc"]),
            surface=str(race["surface"]), distance_m=int(race["distance_m"]),
            going=str(race["going"]), field_size=len(runners),
        ),
        runners=runner_analyses,
        candidate_status="期待値候補なし",
        candidate_reason="市場基準は独立した予測確率ではないため、候補を生成しません。",
    )
