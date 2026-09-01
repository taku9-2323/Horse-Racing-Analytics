import json
from typing import Any, Literal, cast

from pydantic import BaseModel, StringConstraints
from typing_extensions import Annotated

from app.race_analysis import win_market_baseline


ALLOWED_RULE_FIELDS = [
    "win_odds", "market_rank", "odds_change_rate", "relative_horse_number",
    "racecourse", "surface", "distance_m", "going", "field_size", "age",
    "sex", "assigned_weight",
]
INITIAL_RULE = {
    "rule_key": "market_observation_filter", "version": 1,
    "title": "市場順位・単勝オッズ観察ルール",
    "conditions": {"attention": [
        {"field": "market_rank", "operator": "lte", "value": 2},
        {"field": "win_odds", "operator": "lte", "value": 10.0},
    ]},
    "priority": ["判定不能", "注目", "見送り"], "missing_policy": "判定不能",
    "vocabulary": ["注目", "見送り", "判定不能"],
    "allowed_fields": ALLOWED_RULE_FIELDS,
}


class RuleVersion(BaseModel):
    id: int
    rule_key: str
    version: int
    title: str
    conditions: dict[str, Any]
    priority: list[str]
    missing_policy: str
    vocabulary: list[str]
    allowed_fields: list[str]
    created_at: str


class RunnerJudgement(BaseModel):
    horse_number: int
    horse_name: str
    judgement: Literal["注目", "見送り", "判定不能"]
    satisfied_conditions: list[str]
    failed_conditions: list[str]
    missing_reasons: list[str]


class RuleJudgementRun(BaseModel):
    id: int
    race_id: int
    input_snapshot_id: int
    rule_version_id: int
    judgement_as_of: str
    frozen_at: str
    status: Literal["active", "invalidated"]
    invalidation_reason: str | None
    replaces_judgement_id: int | None
    official_pre_race_eligible: bool
    exclusion_reason: str | None
    label: str = "ルールベース判定"
    disclaimer: str = "観察対象を絞る決定的ルールです。独自勝率、期待値、購入推奨、利益保証ではありません。"
    runners: list[RunnerJudgement]


NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class FreezeRuleJudgementRequest(BaseModel):
    snapshot_id: int
    rule_version_id: int
    judgement_as_of: str


class CorrectRuleJudgementRequest(FreezeRuleJudgementRequest):
    reason: NonBlank


def rule_version_response(row: Any) -> RuleVersion:
    return RuleVersion(
        id=int(row["id"]), rule_key=str(row["rule_key"]), version=int(row["version"]),
        title=str(row["title"]), conditions=json.loads(str(row["conditions_json"])),
        priority=json.loads(str(row["priority_json"])), missing_policy=str(row["missing_policy"]),
        vocabulary=json.loads(str(row["vocabulary_json"])),
        allowed_fields=json.loads(str(row["allowed_fields_json"])), created_at=str(row["created_at"]),
    )


def build_runner_judgements(race_runners: list[Any], snapshot_runners: list[Any]) -> list[dict[str, Any]]:
    active = [runner for runner in race_runners if str(runner["status"]) == "出走"]
    odds_by_number = {int(runner["horse_number"]): runner for runner in snapshot_runners}
    market = win_market_baseline([float(odds_by_number[int(runner["horse_number"])]["win_odds"]) for runner in active])
    ranked = sorted(
        [(int(runner["horse_number"]), share) for runner, (_, share) in zip(active, market, strict=True)],
        key=lambda item: (-item[1], item[0]),
    )
    ranks: dict[int, int] = {}
    previous_share: float | None = None
    previous_rank = 0
    for position, (horse_number, share) in enumerate(ranked, start=1):
        rank = previous_rank if previous_share == share else position
        ranks[horse_number] = rank
        previous_share, previous_rank = share, rank
    outputs = []
    for runner in race_runners:
        number = int(runner["horse_number"])
        if str(runner["status"]) != "出走" or number not in odds_by_number:
            outputs.append({"horse_number": number, "horse_name": str(runner["horse_name"]),
                            "judgement": "判定不能", "satisfied_conditions": [],
                            "failed_conditions": [], "missing_reasons": ["有効な出走馬の単勝オッズがありません。"]})
            continue
        odds = float(odds_by_number[number]["win_odds"])
        satisfied = [name for name, passed in (("市場順位が2位以内", ranks[number] <= 2), ("単勝オッズが10.0以下", odds <= 10)) if passed]
        failed = [name for name in ("市場順位が2位以内", "単勝オッズが10.0以下") if name not in satisfied]
        outputs.append({"horse_number": number, "horse_name": str(runner["horse_name"]),
                        "judgement": "注目" if not failed else "見送り",
                        "satisfied_conditions": satisfied, "failed_conditions": failed, "missing_reasons": []})
    return outputs


def judgement_response(run: Any, runners: list[Any]) -> RuleJudgementRun:
    return RuleJudgementRun(
        id=int(run["id"]), race_id=int(run["race_id"]), input_snapshot_id=int(run["input_snapshot_id"]),
        rule_version_id=int(run["rule_version_id"]), judgement_as_of=str(run["judgement_as_of"]),
        frozen_at=str(run["frozen_at"]), status=cast(Literal["active", "invalidated"], str(run["status"])),
        invalidation_reason=run["invalidation_reason"], replaces_judgement_id=run["replaces_judgement_id"],
        official_pre_race_eligible=bool(run["official_pre_race_eligible"]), exclusion_reason=run["exclusion_reason"],
        runners=[RunnerJudgement(
            horse_number=int(row["horse_number"]), horse_name=str(row["horse_name"]),
            judgement=cast(Literal["注目", "見送り", "判定不能"], str(row["judgement"])),
            satisfied_conditions=json.loads(str(row["satisfied_conditions_json"])),
            failed_conditions=json.loads(str(row["failed_conditions_json"])),
            missing_reasons=json.loads(str(row["missing_reasons_json"])),
        ) for row in runners],
    )
