import csv
from collections.abc import Sequence
from datetime import date, datetime, time
from io import StringIO
from math import isfinite
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


class RaceListItem(BaseModel):
    race_id: int
    race: RaceSummary


class CsvValidationIssue(BaseModel):
    row: int
    column: str
    code: str
    description: str


class CsvValidationError(Exception):
    def __init__(self, issues: list[CsvValidationIssue]) -> None:
        super().__init__("入力内容を確認してください。")
        self.issues = issues

    def detail(self) -> dict[str, object]:
        return {
            "code": "csv_validation_failed",
            "message": "CSVに修正が必要な箇所があります。",
            "errors": [issue.model_dump() for issue in self.issues],
        }


def win_market_baseline(win_odds: Sequence[float]) -> list[tuple[float, float]]:
    raw_values = [1 / odds for odds in win_odds]
    total = sum(raw_values)
    return [(raw, raw / total) for raw in raw_values]


def parse_race_csv(content: bytes) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise CsvValidationError([CsvValidationIssue(
            row=1, column="file", code="invalid_encoding",
            description="CSVはUTF-8で保存してください。",
        )]) from error

    reader = csv.DictReader(StringIO(text))
    if reader.fieldnames is None or set(reader.fieldnames) != CSV_COLUMNS:
        raise CsvValidationError([CsvValidationIssue(
            row=1, column="header", code="invalid_header",
            description="CSVの見出しが所定の形式と一致しません。",
        )])

    parsed: list[dict[str, Any]] = []
    issues: list[CsvValidationIssue] = []
    normalized_race_rows: list[tuple[int, dict[str, object | None]]] = []
    horse_number_rows: list[tuple[int, int]] = []
    race_keys = (
        "organizer", "country", "racecourse", "race_date", "race_number",
        "start_time", "timezone", "start_utc", "surface", "distance_m", "going",
    )
    for line_number, row in enumerate(reader, start=2):
        row_issues: list[CsvValidationIssue] = []
        strings = {column: csv_string(row, line_number, column, row_issues) for column in (
            "organizer", "country", "racecourse", "timezone", "surface", "going",
            "horse_name", "sex", "status",
        )}
        integers = {column: csv_integer(row, line_number, column, row_issues) for column in (
            "race_number", "distance_m", "gate", "horse_number", "age",
        )}
        if integers["horse_number"] is not None:
            horse_number_rows.append((line_number, integers["horse_number"]))
        numbers = {column: csv_number(row, line_number, column, row_issues) for column in (
            "assigned_weight", "win_odds", "place_odds_min", "place_odds_max",
        )}
        race_date = csv_date(row, line_number, "race_date", row_issues)
        start_time = csv_time(row, line_number, "start_time", row_issues)
        start_utc = csv_datetime(row, line_number, "start_utc", row_issues)
        normalized_race_rows.append((line_number, {
            "organizer": strings["organizer"], "country": strings["country"],
            "racecourse": strings["racecourse"], "race_date": race_date,
            "race_number": integers["race_number"], "start_time": start_time,
            "timezone": strings["timezone"], "start_utc": start_utc,
            "surface": strings["surface"], "distance_m": integers["distance_m"],
            "going": strings["going"],
        }))
        validate_choice(strings["surface"], {"芝", "ダート"}, line_number, "surface", row_issues)
        validate_choice(strings["sex"], {"牡", "牝", "セン"}, line_number, "sex", row_issues)
        validate_choice(strings["status"], {"出走", "取消", "除外"}, line_number, "status", row_issues)
        timezone = strings["timezone"]
        if timezone is not None and "/" not in timezone:
            row_issues.append(issue(line_number, "timezone", "invalid_timezone", "IANAタイムゾーン名を指定してください。"))
        for column in ("assigned_weight", "win_odds", "place_odds_min", "place_odds_max"):
            value = numbers[column]
            if value is not None and value <= 0:
                row_issues.append(issue(line_number, column, "must_be_positive", "0より大きい値を指定してください。"))
        minimum, maximum = numbers["place_odds_min"], numbers["place_odds_max"]
        if minimum is not None and maximum is not None and minimum > maximum:
            row_issues.append(issue(line_number, "place_odds_min", "invalid_range", "複勝下限オッズは上限以下にしてください。"))
        if row_issues:
            issues.extend(row_issues)
            continue
        parsed.append({
            **strings, **integers, **numbers, "_row": line_number,
            "race_date": race_date, "start_time": start_time,
            "start_utc": start_utc,
        })

    if not normalized_race_rows:
        raise CsvValidationError([issue(2, "file", "no_runners", "CSVに出走馬がありません。")])
    for key in race_keys:
        first_value = next(
            (normalized_race[key] for _, normalized_race in normalized_race_rows if normalized_race[key] is not None),
            None,
        )
        for row_number, normalized_race in normalized_race_rows:
            normalized_value = normalized_race[key]
            if first_value is not None and normalized_value is not None and normalized_value != first_value:
                issues.append(issue(row_number, key, "mixed_race", "1つのCSVには1レースだけを記載してください。"))
    seen_horse_numbers: set[int] = set()
    for row_number, horse_number in horse_number_rows:
        if horse_number in seen_horse_numbers:
            issues.append(issue(row_number, "horse_number", "duplicate_horse_number", "同じ馬番を1レース内に複数記載できません。"))
        seen_horse_numbers.add(horse_number)
    if not parsed:
        if not issues:
            issues.append(issue(2, "file", "no_runners", "CSVに出走馬がありません。"))
        raise CsvValidationError(issues)
    first = parsed[0]
    if issues:
        raise CsvValidationError(issues)

    market_values = win_market_baseline([float(row["win_odds"]) for row in parsed])
    race = {key: first[key] for key in race_keys}
    runners = [
        {
            **{key: row[key] for key in (
                "gate", "horse_number", "horse_name", "age", "sex",
                "assigned_weight", "status", "win_odds", "place_odds_min",
                "place_odds_max",
            )},
            "raw_inverse_win_odds": raw,
            "normalized_win_market_share": share,
        }
        for row, (raw, share) in zip(parsed, market_values, strict=True)
    ]
    return race, runners


def issue(row: int, column: str, code: str, description: str) -> CsvValidationIssue:
    return CsvValidationIssue(row=row, column=column, code=code, description=description)


def csv_string(row: dict[str, str | None], row_number: int, column: str, issues: list[CsvValidationIssue]) -> str | None:
    value = row.get(column)
    if value is None or not value.strip():
        issues.append(issue(row_number, column, "required", "必須項目です。"))
        return None
    return value.strip()


def csv_integer(row: dict[str, str | None], row_number: int, column: str, issues: list[CsvValidationIssue]) -> int | None:
    value = csv_string(row, row_number, column, issues)
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        issues.append(issue(row_number, column, "invalid_integer", "整数で指定してください。"))
        return None


def csv_number(row: dict[str, str | None], row_number: int, column: str, issues: list[CsvValidationIssue]) -> float | None:
    value = csv_string(row, row_number, column, issues)
    if value is None:
        return None
    try:
        parsed = float(value)
        if not isfinite(parsed):
            raise ValueError
        return parsed
    except ValueError:
        issues.append(issue(row_number, column, "invalid_number", "数値で指定してください。"))
        return None


def csv_date(row: dict[str, str | None], row_number: int, column: str, issues: list[CsvValidationIssue]) -> str | None:
    value = csv_string(row, row_number, column, issues)
    try:
        return date.fromisoformat(value).isoformat() if value is not None else None
    except ValueError:
        issues.append(issue(row_number, column, "invalid_date", "ISO 8601日付で指定してください。"))
        return None


def csv_time(row: dict[str, str | None], row_number: int, column: str, issues: list[CsvValidationIssue]) -> str | None:
    value = csv_string(row, row_number, column, issues)
    try:
        return time.fromisoformat(value).strftime("%H:%M") if value is not None else None
    except ValueError:
        issues.append(issue(row_number, column, "invalid_time", "ISO 8601時刻で指定してください。"))
        return None


def csv_datetime(row: dict[str, str | None], row_number: int, column: str, issues: list[CsvValidationIssue]) -> str | None:
    value = csv_string(row, row_number, column, issues)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00")) if value is not None else None
        if parsed is not None and parsed.utcoffset() is None:
            raise ValueError
        return parsed.isoformat().replace("+00:00", "Z") if parsed is not None else None
    except ValueError:
        issues.append(issue(row_number, column, "invalid_datetime", "UTCオフセット付きISO 8601日時で指定してください。"))
        return None


def validate_choice(value: str | None, allowed: set[str], row_number: int, column: str, issues: list[CsvValidationIssue]) -> None:
    if value is not None and value not in allowed:
        issues.append(issue(row_number, column, "invalid_choice", f"有効な値: {', '.join(sorted(allowed))}"))


def build_analysis(race_id: int, race: Any, runners: list[Any]) -> RaceAnalysis:
    supports_place = len(runners) >= 5
    market_values = win_market_baseline([float(runner["win_odds"]) for runner in runners])
    runner_analyses = [
        RunnerAnalysis(
            horse_number=int(runner["horse_number"]),
            horse_name=str(runner["horse_name"]),
            win_odds=float(runner["win_odds"]),
            raw_inverse_win_odds=raw,
            normalized_win_market_share=share,
            place_odds_min=float(runner["place_odds_min"]),
            place_odds_max=float(runner["place_odds_max"]),
            place_break_even_hit_rate=(PlaceBreakEvenHitRate(
                minimum=1 / float(runner["place_odds_max"]),
                midpoint=1 / ((float(runner["place_odds_min"]) + float(runner["place_odds_max"])) / 2),
                maximum=1 / float(runner["place_odds_min"]),
            ) if supports_place else None),
        )
        for runner, (raw, share) in zip(runners, market_values, strict=True)
    ]
    return RaceAnalysis(
        race_id=race_id,
        race=build_race_summary(race),
        runners=runner_analyses,
        candidate_status="期待値候補なし",
        candidate_reason="市場基準は独立した予測確率ではないため、候補を生成しません。",
    )


def build_race_summary(race: Any) -> RaceSummary:
    return RaceSummary(
        organizer=str(race["organizer"]), country=str(race["country"]),
        racecourse=str(race["racecourse"]), race_date=str(race["race_date"]),
        race_number=int(race["race_number"]), start_time=str(race["start_time"]),
        timezone=str(race["timezone"]), start_utc=str(race["start_utc"]),
        surface=str(race["surface"]), distance_m=int(race["distance_m"]),
        going=str(race["going"]), field_size=int(race["field_size"]),
    )
