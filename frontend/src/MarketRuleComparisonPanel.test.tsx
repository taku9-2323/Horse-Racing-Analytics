import { fireEvent, render, screen } from "@testing-library/react";
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
        market_reason: "正規化市場シェアの順位", rule_judgement: "注目", rule_reason: "市場順位が2位以内", missing_reasons: [] }],
    }), { status: 200 }));
  });
  render(<MarketRuleComparisonPanel raceId={2} />);
  fireEvent.click(screen.getByRole("button", { name: "比較を開く" }));
  expect(await screen.findByText("市場観察 v1")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "同じ時点で比較" }));
  expect(await screen.findByText("1位 / 45.00%")).toBeTruthy();
  expect(screen.getByText("注目")).toBeTruthy();
  expect(screen.getByText(/どちらも独立予測/)).toBeTruthy();
  expect(screen.getByText(/固定済み/)).toBeTruthy();
});
