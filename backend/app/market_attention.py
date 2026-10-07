from collections.abc import Sequence
from typing import Any, Literal

from pydantic import BaseModel

from app.race_analysis import win_market_baseline


class MarketAttentionRunner(BaseModel):
    rank: int
    horse_number: int
    horse_name: str
    win_odds: float
    raw_inverse_win_odds: float
    normalized_win_market_share: float


class MarketAttentionRanking(BaseModel):
    race_id: int
    snapshot_id: int
    observed_at: str | None
    received_at: str
    status: Literal["available", "unavailable"]
    unavailable_reason: str | None
    runners: list[MarketAttentionRunner]
    label: str = "市場評価による順位"
    disclaimer: str = "オッズから算出した市場内の相対順位です。客観的確率、独立予測、期待値、購入推奨ではありません。"


def build_market_attention_ranking(
    race_id: int, snapshot: Any, snapshot_runners: Sequence[Any], race_runners: Sequence[Any],
) -> MarketAttentionRanking:
    active_names = {
        int(runner["horse_number"]): str(runner["horse_name"])
        for runner in race_runners
        if str(runner["status"]) == "出走"
    }
    snapshot_numbers = {int(runner["horse_number"]) for runner in snapshot_runners}
    if active_names.keys() - snapshot_numbers:
        return MarketAttentionRanking(
            race_id=race_id, snapshot_id=int(snapshot["id"]),
            observed_at=snapshot["observed_at"], received_at=str(snapshot["received_at"]),
            status="unavailable",
            unavailable_reason="選択したオッズ時点の出走馬データが不足しているため順位を算出できません。",
            runners=[],
        )
    eligible = [runner for runner in snapshot_runners if int(runner["horse_number"]) in active_names]
    if not eligible:
        return MarketAttentionRanking(
            race_id=race_id, snapshot_id=int(snapshot["id"]),
            observed_at=snapshot["observed_at"], received_at=str(snapshot["received_at"]),
            status="unavailable", unavailable_reason="有効な出走馬の単勝オッズがないため順位を算出できません。",
            runners=[],
        )

    market_values = win_market_baseline([float(runner["win_odds"]) for runner in eligible])
    values = [
        (runner, raw, share)
        for runner, (raw, share) in zip(eligible, market_values, strict=True)
    ]
    values.sort(key=lambda item: (-item[2], int(item[0]["horse_number"])))
    ranked: list[MarketAttentionRunner] = []
    previous_share: float | None = None
    previous_rank = 0
    for position, (runner, raw, share) in enumerate(values, start=1):
        rank = previous_rank if previous_share == share else position
        ranked.append(MarketAttentionRunner(
            rank=rank, horse_number=int(runner["horse_number"]),
            horse_name=active_names[int(runner["horse_number"])],
            win_odds=float(runner["win_odds"]), raw_inverse_win_odds=raw,
            normalized_win_market_share=share,
        ))
        previous_share, previous_rank = share, rank
    return MarketAttentionRanking(
        race_id=race_id, snapshot_id=int(snapshot["id"]), observed_at=snapshot["observed_at"],
        received_at=str(snapshot["received_at"]), status="available",
        unavailable_reason=None, runners=ranked,
    )
