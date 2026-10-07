from collections.abc import Sequence
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.market_attention import MarketAttentionRanking
from app.rule_judgements import (
    RuleCondition,
    RuleJudgementRun,
    RuleVersion,
    build_rule_conditions,
    rule_condition_reason,
)


class ComparisonRow(BaseModel):
    horse_number: int
    horse_name: str
    market_rank: int | None
    normalized_win_market_share: float | None
    market_reason: str
    rule_judgement: Literal["注目", "見送り", "判定不能"] | None
    rule_reason: str
    rule_conditions: list[RuleCondition] = Field(default_factory=list)
    missing_reasons: list[str]


class MarketRuleComparison(BaseModel):
    race_id: int
    snapshot_id: int | None
    rule_version_id: int
    state: Literal["available", "odds_not_registered", "judgement_not_generated", "rule_version_mismatch"]
    next_action: str | None
    observed_at: str | None
    received_at: str | None
    fixed_state: Literal["not_generated", "fixed", "invalidated", "post_start"]
    official_pre_race_eligible: bool
    judgement_run_id: int | None
    rows: list[ComparisonRow]
    disclaimer: str = "市場基準とルール判定は別々の観察根拠です。どちらも独立予測、期待値、購入候補、正解または推奨を示しません。"


def build_comparison(
    race_id: int, rule_version_id: int, market: MarketAttentionRanking | None,
    judgement: RuleJudgementRun | None, rule_version: RuleVersion | None,
    snapshot_runners: Sequence[Any] = (),
) -> MarketRuleComparison:
    if market is None:
        return MarketRuleComparison(
            race_id=race_id, snapshot_id=None, rule_version_id=rule_version_id,
            state="odds_not_registered", next_action="先にオッズ時点を保存してください。",
            observed_at=None, received_at=None, fixed_state="not_generated",
            official_pre_race_eligible=False, judgement_run_id=None, rows=[],
        )
    if rule_version is None:
        return MarketRuleComparison(
            race_id=race_id, snapshot_id=market.snapshot_id, rule_version_id=rule_version_id,
            state="rule_version_mismatch", next_action="保存されているルール版を選択してください。",
            observed_at=market.observed_at, received_at=market.received_at,
            fixed_state="not_generated", official_pre_race_eligible=False,
            judgement_run_id=None, rows=[],
        )
    market_by_number = {runner.horse_number: runner for runner in market.runners}
    odds_by_number = {
        int(runner["horse_number"]): float(runner["win_odds"]) for runner in snapshot_runners
    }
    if judgement is None:
        rows = [ComparisonRow(
            horse_number=runner.horse_number, horse_name=runner.horse_name,
            market_rank=runner.rank, normalized_win_market_share=runner.normalized_win_market_share,
            market_reason=f"正規化市場シェア {(runner.normalized_win_market_share * 100):.2f}% の順位",
            rule_judgement=None, rule_reason="固定判定がありません。", missing_reasons=[],
        ) for runner in market.runners]
        return MarketRuleComparison(
            race_id=race_id, snapshot_id=market.snapshot_id, rule_version_id=rule_version_id,
            state="judgement_not_generated", next_action="このオッズ時点とルール版で判定を固定してください。",
            observed_at=market.observed_at, received_at=market.received_at,
            fixed_state="not_generated", official_pre_race_eligible=False,
            judgement_run_id=None, rows=rows,
        )
    rows = []
    for result in judgement.runners:
        market_runner = market_by_number.get(result.horse_number)
        conditions = build_rule_conditions(
            rule_version, result.satisfied_conditions, result.failed_conditions,
            {
                "market_rank": None if market_runner is None else market_runner.rank,
                "win_odds": odds_by_number.get(result.horse_number),
            },
        )
        rows.append(ComparisonRow(
            horse_number=result.horse_number, horse_name=result.horse_name,
            market_rank=None if market_runner is None else market_runner.rank,
            normalized_win_market_share=None if market_runner is None else market_runner.normalized_win_market_share,
            market_reason=(
                (market.unavailable_reason or "有効な市場順位がありません。")
                if market_runner is None else
                f"正規化市場シェア {(market_runner.normalized_win_market_share * 100):.2f}% の順位"
            ),
            rule_judgement=result.judgement,
            rule_reason=rule_condition_reason(conditions), rule_conditions=conditions,
            missing_reasons=result.missing_reasons,
        ))
    fixed_state: Literal["fixed", "invalidated", "post_start"] = (
        "invalidated" if judgement.status == "invalidated"
        else "fixed" if judgement.official_pre_race_eligible else "post_start"
    )
    return MarketRuleComparison(
        race_id=race_id, snapshot_id=market.snapshot_id, rule_version_id=rule_version_id,
        state="available", next_action=None, observed_at=market.observed_at,
        received_at=market.received_at, fixed_state=fixed_state,
        official_pre_race_eligible=judgement.official_pre_race_eligible,
        judgement_run_id=judgement.id, rows=rows,
    )
