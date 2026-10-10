from typing import Any, Literal

from pydantic import BaseModel

from app.attention import AttentionLevel, _attention_level
from app.database import SqliteDatabase
from app.market_attention import build_market_attention_ranking
from app.race_analysis import RaceSummary, build_race_summary
from app.rule_judgements import (
    RuleCondition,
    build_rule_conditions,
    judgement_response,
    rule_condition_reason,
    rule_version_response,
)


class WeeklyDecisionRunner(BaseModel):
    horse_number: int
    horse_name: str
    gate: int | None
    age: int | None
    sex: str | None
    assigned_weight: float | None
    status: str | None
    win_odds: float | None
    place_odds_min: float | None
    place_odds_max: float | None
    market_rank: int | None
    normalized_win_market_share: float | None
    rule_judgement: Literal["注目", "見送り", "判定不能"]
    rule_reason: str
    rule_conditions: list[RuleCondition]
    missing_reasons: list[str]


class WeeklyRaceDecisionView(BaseModel):
    race_id: int
    race: RaceSummary
    snapshot_id: int
    judgement_id: int
    rule_version_id: int
    observed_at: str | None
    received_at: str
    judgement_as_of: str
    judgement_frozen_at: str
    attention_horse_count: int
    judged_runner_count: int
    attention_level: AttentionLevel | None
    runners: list[WeeklyDecisionRunner]
    disclaimer: str = "注目度は固定ルール判定の対象頭数に占める『注目』頭数の割合を段階表示したものです。予測確率・予測自信度・市場優位性・購入推奨を示しません。"


def _optional_int(row: Any, key: str) -> int | None:
    value = row[key]
    return None if value is None else int(value)


def _optional_float(row: Any, key: str) -> float | None:
    value = row[key]
    return None if value is None else float(value)


def _optional_text(row: Any, key: str) -> str | None:
    value = row[key]
    return None if value is None else str(value)


def weekly_race_decision_view(
    database: SqliteDatabase, race_id: int, snapshot_id: int, judgement_id: int,
) -> WeeklyRaceDecisionView:
    race_stored = database.get_race_roster(race_id)
    snapshot_stored = database.get_odds_snapshot(snapshot_id)
    judgement_stored = database.get_rule_judgement(judgement_id)
    if race_stored is None:
        raise LookupError("race_not_found")
    if snapshot_stored is None:
        raise LookupError("snapshot_not_found")
    if judgement_stored is None:
        raise LookupError("judgement_not_found")
    race, race_runners = race_stored
    snapshot, snapshot_runners = snapshot_stored
    judgement_run, judgement_runners = judgement_stored
    if int(snapshot["race_id"]) != race_id:
        raise ValueError("snapshot_race_mismatch")
    if (
        int(judgement_run["race_id"]) != race_id
        or int(judgement_run["input_snapshot_id"]) != snapshot_id
    ):
        raise ValueError("judgement_input_mismatch")

    market = build_market_attention_ranking(race_id, snapshot, snapshot_runners, race_runners)
    market_by_number = {runner.horse_number: runner for runner in market.runners}
    roster_by_number = {int(row["horse_number"]): row for row in race_runners}
    odds_by_number = {int(row["horse_number"]): row for row in snapshot_runners}
    judgement = judgement_response(judgement_run, judgement_runners)
    rule_version_row = next((
        row for row in database.list_rule_versions()
        if int(row["id"]) == judgement.rule_version_id
    ), None)
    rule_version = None if rule_version_row is None else rule_version_response(rule_version_row)
    runners = []
    for item in judgement.runners:
        roster = roster_by_number.get(item.horse_number)
        odds = odds_by_number.get(item.horse_number)
        market_item = market_by_number.get(item.horse_number)
        conditions = build_rule_conditions(
            rule_version, item.satisfied_conditions, item.failed_conditions,
            {
                "market_rank": None if market_item is None else market_item.rank,
                "win_odds": None if odds is None else float(odds["win_odds"]),
            },
        )
        runners.append(WeeklyDecisionRunner(
            horse_number=item.horse_number, horse_name=item.horse_name,
            gate=None if roster is None else _optional_int(roster, "gate"),
            age=None if roster is None else _optional_int(roster, "age"),
            sex=None if roster is None else _optional_text(roster, "sex"),
            assigned_weight=(
                None if roster is None else _optional_float(roster, "assigned_weight")
            ),
            status=None if roster is None else _optional_text(roster, "status"),
            win_odds=None if odds is None else float(odds["win_odds"]),
            place_odds_min=None if odds is None else float(odds["place_odds_min"]),
            place_odds_max=None if odds is None else float(odds["place_odds_max"]),
            market_rank=None if market_item is None else market_item.rank,
            normalized_win_market_share=(
                None if market_item is None else market_item.normalized_win_market_share
            ),
            rule_judgement=item.judgement, rule_reason=rule_condition_reason(conditions),
            rule_conditions=conditions,
            missing_reasons=item.missing_reasons,
        ))
    attention_horse_count = sum(item.rule_judgement == "注目" for item in runners)
    judged_runner_count = sum(item.rule_judgement in {"注目", "見送り"} for item in runners)
    attention_ratio = (
        attention_horse_count / judged_runner_count if judged_runner_count > 0 else None
    )
    return WeeklyRaceDecisionView(
        race_id=race_id, race=build_race_summary(race), snapshot_id=snapshot_id,
        judgement_id=judgement_id, rule_version_id=judgement.rule_version_id,
        observed_at=snapshot["observed_at"], received_at=str(snapshot["received_at"]),
        judgement_as_of=judgement.judgement_as_of,
        judgement_frozen_at=judgement.frozen_at,
        attention_horse_count=attention_horse_count,
        judged_runner_count=judged_runner_count,
        attention_level=(
            None if attention_ratio is None else _attention_level(attention_ratio)
        ),
        runners=runners,
    )
