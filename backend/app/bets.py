import csv
from io import StringIO
import json
from typing import Any, Literal

from pydantic import BaseModel, Field


BetType = Literal["win", "place"]
DecisionType = Literal["candidate", "discretionary"]
RunnerResultStatus = Literal["確定", "取消", "除外"]


class BetCreate(BaseModel):
    horse_number: int = Field(gt=0)
    bet_type: BetType
    decision_type: DecisionType
    amount_yen: int = Field(gt=0, multiple_of=100)
    prediction_run_id: int | None = Field(default=None, gt=0)


class Bet(BaseModel):
    id: int
    race_id: int
    horse_number: int
    bet_type: BetType
    decision_type: DecisionType
    amount_yen: int
    placed_at: str
    status: str
    prediction_run_id: int | None


class RunnerResult(BaseModel):
    horse_number: int
    finish_position: int | None
    status: RunnerResultStatus
    win_payout_per_100: int
    place_payout_per_100: int


class ResultVersion(BaseModel):
    id: int
    race_id: int
    version: int
    received_at: str
    status: str
    correction_reason: str | None
    changes: list[str]
    runners: list[RunnerResult]


class Settlement(BaseModel):
    id: int
    bet_id: int
    result_version_id: int
    stake_yen: int
    payout_yen: int
    refund_yen: int
    profit_yen: int


class SettlementTotals(BaseModel):
    stake_yen: int
    payout_yen: int
    refund_yen: int
    profit_yen: int | None
    return_rate: float | None


class RaceLedger(BaseModel):
    bets: list[Bet]
    result_version: ResultVersion | None
    settlements: list[Settlement]
    totals: SettlementTotals
    by_decision_type: dict[str, SettlementTotals]


class ResultCsvIssue(BaseModel):
    row: int
    column: str
    code: str
    description: str


class ResultCsvValidationError(Exception):
    def __init__(self, issues: list[ResultCsvIssue]) -> None:
        super().__init__("結果CSVを確認してください。")
        self.issues = issues

    def detail(self) -> dict[str, object]:
        return {
            "code": "result_csv_validation_failed",
            "message": "結果CSVに修正が必要な箇所があります。",
            "errors": [issue.model_dump() for issue in self.issues],
        }


RESULT_COLUMNS = {
    "horse_number", "finish_position", "status",
    "win_payout_per_100", "place_payout_per_100",
}


def parse_results_csv(content: bytes, *, allow_dead_heat: bool = False) -> list[dict[str, Any]]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ResultCsvValidationError([_issue(1, "file", "invalid_encoding", "CSVはUTF-8で保存してください。")]) from error
    reader = csv.DictReader(StringIO(text))
    if reader.fieldnames is None or set(reader.fieldnames) != RESULT_COLUMNS:
        raise ResultCsvValidationError([_issue(1, "header", "invalid_header", "結果CSVの見出しが所定の形式と一致しません。")])

    parsed: list[dict[str, Any]] = []
    issues: list[ResultCsvIssue] = []
    seen_horses: set[int] = set()
    seen_finishes: set[int] = set()
    for row_number, row in enumerate(reader, start=2):
        horse_number = _integer(row, row_number, "horse_number", issues)
        status = (row.get("status") or "").strip()
        if status not in {"確定", "取消", "除外"}:
            issues.append(_issue(row_number, "status", "invalid_choice", "確定、取消、除外のいずれかを指定してください。"))
        finish_text = (row.get("finish_position") or "").strip()
        finish_position: int | None = None
        if status == "確定":
            finish_position = _integer(row, row_number, "finish_position", issues)
            if finish_position is not None and finish_position in seen_finishes and not allow_dead_heat:
                issues.append(_issue(row_number, "finish_position", "unsupported_dead_heat", "同着は理由付き訂正で処理してください。"))
            elif finish_position is not None:
                seen_finishes.add(finish_position)
        elif finish_text:
            issues.append(_issue(row_number, "finish_position", "must_be_blank", "取消・除外では着順を空欄にしてください。"))
        win_payout = _integer(row, row_number, "win_payout_per_100", issues, allow_zero=True)
        place_payout = _integer(row, row_number, "place_payout_per_100", issues, allow_zero=True)
        if horse_number is not None:
            if horse_number in seen_horses:
                issues.append(_issue(row_number, "horse_number", "duplicate_horse_number", "同じ馬番を複数記載できません。"))
            seen_horses.add(horse_number)
        if status in {"取消", "除外"} and any(value not in {None, 0} for value in (win_payout, place_payout)):
            issues.append(_issue(row_number, "status", "refund_cannot_have_payout", "取消・除外の払戻額は0にしてください。"))
        if horse_number is not None and status in {"確定", "取消", "除外"} and win_payout is not None and place_payout is not None:
            parsed.append({
                "horse_number": horse_number, "finish_position": finish_position,
                "status": status, "win_payout_per_100": win_payout,
                "place_payout_per_100": place_payout,
            })
    if not parsed and not issues:
        issues.append(_issue(2, "file", "no_results", "結果がありません。"))
    if issues:
        raise ResultCsvValidationError(issues)
    return parsed


def _integer(
    row: dict[str, str | None], row_number: int, column: str,
    issues: list[ResultCsvIssue], allow_zero: bool = False,
) -> int | None:
    value = (row.get(column) or "").strip()
    if not value:
        issues.append(_issue(row_number, column, "required", "必須項目です。"))
        return None
    try:
        parsed = int(value)
    except ValueError:
        issues.append(_issue(row_number, column, "invalid_integer", "整数で指定してください。"))
        return None
    if parsed < 0 or (parsed == 0 and not allow_zero):
        issues.append(_issue(row_number, column, "must_be_positive", "正の整数で指定してください。"))
        return None
    return parsed


def _issue(row: int, column: str, code: str, description: str) -> ResultCsvIssue:
    return ResultCsvIssue(row=row, column=column, code=code, description=description)


def bet_response(row: Any) -> Bet:
    return Bet(
        id=int(row["id"]), race_id=int(row["race_id"]), horse_number=int(row["horse_number"]),
        bet_type=row["bet_type"], decision_type=row["decision_type"],
        amount_yen=int(row["amount_yen"]), placed_at=str(row["placed_at"]), status=str(row["status"]),
        prediction_run_id=(
            None if row["prediction_run_id"] is None else int(row["prediction_run_id"])
        ),
    )


def result_response(version: Any, runners: list[Any]) -> ResultVersion:
    return ResultVersion(
        id=int(version["id"]), race_id=int(version["race_id"]), version=int(version["version"]),
        received_at=str(version["received_at"]), status=str(version["status"]),
        correction_reason=version["correction_reason"],
        changes=json.loads(str(version["change_summary_json"])),
        runners=[RunnerResult(
            horse_number=int(row["horse_number"]), finish_position=row["finish_position"],
            status=row["status"], win_payout_per_100=int(row["win_payout_per_100"]),
            place_payout_per_100=int(row["place_payout_per_100"]),
        ) for row in runners],
    )


def settlement_response(row: Any) -> Settlement:
    return Settlement(
        id=int(row["id"]), bet_id=int(row["bet_id"]), result_version_id=int(row["result_version_id"]),
        stake_yen=int(row["stake_yen"]), payout_yen=int(row["payout_yen"]),
        refund_yen=int(row["refund_yen"]), profit_yen=int(row["profit_yen"]),
    )


def totals_for(rows: list[Any]) -> SettlementTotals:
    stake = sum(int(row["stake_yen"]) for row in rows)
    payout = sum(int(row["payout_yen"]) for row in rows)
    refund = sum(int(row["refund_yen"]) for row in rows)
    return SettlementTotals(
        stake_yen=stake, payout_yen=payout, refund_yen=refund,
        profit_yen=payout + refund - stake,
        return_rate=(payout + refund) / stake if stake else None,
    )


def pending_totals_for(bets: list[Any]) -> SettlementTotals:
    return SettlementTotals(
        stake_yen=sum(int(bet["amount_yen"]) for bet in bets),
        payout_yen=0, refund_yen=0, profit_yen=None, return_rate=None,
    )
