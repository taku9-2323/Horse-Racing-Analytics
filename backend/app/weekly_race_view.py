from typing import Literal

from pydantic import BaseModel

from app.database import SqliteDatabase
from app.market_attention import build_market_attention_ranking
from app.race_analysis import RaceSummary, build_race_summary
from app.rule_judgements import judgement_response


class WeeklyDecisionRunner(BaseModel):
    horse_number: int
    horse_name: str
    win_odds: float
    place_odds_min: float
    place_odds_max: float
    market_rank: int | None
    normalized_win_market_share: float | None
    rule_judgement: Literal["注目", "見送り", "判定不能"]
    rule_reason: str
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
    runners: list[WeeklyDecisionRunner]
    disclaimer: str = "注目段階はルール該当率です。期待値、回収率、購入推奨、利益優位性を示しません。"


def weekly_race_decision_view(
    database: SqliteDatabase, race_id: int, snapshot_id: int, judgement_id: int,
) -> WeeklyRaceDecisionView:
    race_stored = database.get_race(race_id)
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
    odds_by_number = {int(row["horse_number"]): row for row in snapshot_runners}
    judgement = judgement_response(judgement_run, judgement_runners)
    runners = []
    for item in judgement.runners:
        odds = odds_by_number[item.horse_number]
        market_item = market_by_number.get(item.horse_number)
        reasons = item.missing_reasons or [*item.satisfied_conditions, *item.failed_conditions]
        runners.append(WeeklyDecisionRunner(
            horse_number=item.horse_number, horse_name=item.horse_name,
            win_odds=float(odds["win_odds"]), place_odds_min=float(odds["place_odds_min"]),
            place_odds_max=float(odds["place_odds_max"]),
            market_rank=None if market_item is None else market_item.rank,
            normalized_win_market_share=(
                None if market_item is None else market_item.normalized_win_market_share
            ),
            rule_judgement=item.judgement, rule_reason=" / ".join(reasons),
            missing_reasons=item.missing_reasons,
        ))
    return WeeklyRaceDecisionView(
        race_id=race_id, race=build_race_summary(race), snapshot_id=snapshot_id,
        judgement_id=judgement_id, rule_version_id=judgement.rule_version_id,
        observed_at=snapshot["observed_at"], received_at=str(snapshot["received_at"]),
        judgement_as_of=judgement.judgement_as_of,
        judgement_frozen_at=judgement.frozen_at,
        attention_horse_count=sum(item.rule_judgement == "注目" for item in runners),
        judged_runner_count=sum(item.rule_judgement in {"注目", "見送り"} for item in runners),
        runners=runners,
    )
