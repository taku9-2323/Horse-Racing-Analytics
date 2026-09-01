import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import RuleJudgementPanel from "./RuleJudgementPanel";

afterEach(() => vi.restoreAllMocks());

it("selects a pre-race snapshot, freezes explainable judgements, and keeps the disclaimer", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    const url = String(input);
    if (url === "/api/rule-versions") return Promise.resolve(new Response(JSON.stringify([{ id: 1, title: "市場観察", version: 1 }]), { status: 200 }));
    if (url.endsWith("/odds-snapshots")) return Promise.resolve(new Response(JSON.stringify([{ id: 9, observed_at: "2026-08-30T05:00:00Z" }]), { status: 200 }));
    if (url.endsWith("/rule-judgements") && !init) return Promise.resolve(new Response(JSON.stringify([]), { status: 200 }));
    return Promise.resolve(new Response(JSON.stringify({
      id: 4, input_snapshot_id: 9, judgement_as_of: "2026-08-30T05:00:00Z", status: "active",
      official_pre_race_eligible: true, exclusion_reason: null,
      disclaimer: "独自勝率、期待値、購入推奨、利益保証ではありません。",
      runners: [{ horse_number: 1, horse_name: "アカツキ", judgement: "注目", satisfied_conditions: ["市場順位が2位以内"], failed_conditions: [], missing_reasons: [] }],
    }), { status: 201 }));
  });
  render(<RuleJudgementPanel raceId={2} />);
  fireEvent.click(screen.getByRole("button", { name: "ルール判定を開く" }));
  expect(await screen.findByText("市場観察 v1")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "この時点で判定を固定" }));
  expect(await screen.findByText(/1 アカツキ: 注目/)).toBeTruthy();
  expect(screen.getByText("独自勝率、期待値、購入推奨、利益保証ではありません。")).toBeTruthy();
});
