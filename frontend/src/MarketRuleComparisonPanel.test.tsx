import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import MarketRuleComparisonPanel from "./MarketRuleComparisonPanel";

afterEach(() => vi.restoreAllMocks());

it("compares market evidence and fixed rule evidence in the same row", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    const url = String(input);
    if (url.endsWith("/odds-snapshots")) return Promise.resolve(new Response(JSON.stringify([{ id: 9, observed_at: "2026-08-30T05:00:00Z" }]), { status: 200 }));
    if (url === "/api/rule-versions") return Promise.resolve(new Response(JSON.stringify([{ id: 1, title: "市場観察", version: 1 }]), { status: 200 }));
    return Promise.resolve(new Response(JSON.stringify({
      state: "available", next_action: null, observed_at: "2026-08-30T05:00:00Z", received_at: "2026-08-30T05:01:00Z",
      fixed_state: "fixed", official_pre_race_eligible: true,
      disclaimer: "どちらも独立予測、期待値、購入候補、正解または推奨を示しません。",
      rows: [{ horse_number: 1, horse_name: "アカツキ", market_rank: 1, normalized_win_market_share: 0.45,
        market_reason: "正規化市場シェアの順位", rule_judgement: "注目",
        rule_reason: "市場順位: 達成（実測値1位 / 条件2位以内） / 単勝オッズ: 達成（実測値2.4 / 条件10.0以下）",
        rule_conditions: [
          { field: "market_rank", operator: "lte", threshold: 2, state: "satisfied", observed_value: 1 },
          { field: "win_odds", operator: "lte", threshold: 10, state: "satisfied", observed_value: 2.4 },
        ], missing_reasons: [] },
      { horse_number: 3, horse_name: "ミナモ", market_rank: 3, normalized_win_market_share: 0.06,
        market_reason: "正規化市場シェアの順位", rule_judgement: "見送り",
        rule_reason: "市場順位: 未達（実測値3位 / 条件2位以内） / 単勝オッズ: 未達（実測値20.0 / 条件10.0以下）",
        rule_conditions: [
          { field: "market_rank", operator: "lte", threshold: 2, state: "failed", observed_value: 3 },
          { field: "win_odds", operator: "lte", threshold: 10, state: "failed", observed_value: 20 },
        ], missing_reasons: [] },
      { horse_number: 6, horse_name: "カゲロウ", market_rank: null, normalized_win_market_share: null,
        market_reason: "有効な市場順位がありません。", rule_judgement: "判定不能",
        rule_reason: "市場順位: 判定状態不明（実測値不明 / 条件2位以内） / 単勝オッズ: 判定状態不明（実測値不明 / 条件10.0以下）",
        rule_conditions: [
          { field: "market_rank", operator: "lte", threshold: 2, state: "unknown", observed_value: null },
          { field: "win_odds", operator: "lte", threshold: 10, state: "unknown", observed_value: null },
        ], missing_reasons: ["有効な出走馬の単勝オッズがありません。"] }],
    }), { status: 200 }));
  });
  render(<MarketRuleComparisonPanel raceId={2} />);
  fireEvent.click(screen.getByRole("button", { name: "比較を開く" }));
  expect(await screen.findByText("市場観察 v1")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "同じ時点で比較" }));
  expect(await screen.findByText("1位 / 45.00%")).toBeTruthy();
  expect(screen.getByText("注目")).toBeTruthy();
  const conditionLists = screen.getAllByRole("list", { name: "判定条件" });
  expect(within(conditionLists[1]).getByText("市場順位: 未達（実測値3位 / 条件2位以内）")).toBeTruthy();
  expect(within(conditionLists[1]).getByText("単勝オッズ: 未達（実測値20.0 / 条件10.0以下）")).toBeTruthy();
  expect(within(conditionLists[2]).getByText("市場順位: 判定状態不明（実測値不明 / 条件2位以内）")).toBeTruthy();
  expect(screen.getByText(/どちらも独立予測/)).toBeTruthy();
  expect(screen.getByText(/固定済み/)).toBeTruthy();
});
