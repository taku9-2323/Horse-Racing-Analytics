import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import PredictionPanel from "./PredictionPanel";


afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const jsonResponse = (value: object) => new Response(JSON.stringify(value), {
  status: 200, headers: { "Content-Type": "application/json" },
});

it("shows independent probabilities separately from market shares and break-even values", async () => {
  const prediction = {
    id: 31, input_snapshot_id: 10, prediction_kind: "independent",
    model_identifier: "fake-independent-model", model_version: "test-1",
    prediction_as_of: "2026-08-30T04:54:00Z", rationale: "固定fixtureによる境界検証",
    output_capabilities: ["win", "place"], frozen_at: "2026-08-30T05:00:00Z",
    status: "active", invalidation_reason: null, official_evaluation_eligible: true,
    evaluation_exclusion_reason: null, analysis_tags: [],
    runners: [{ horse_number: 1, win_probability: 0.42, place_probability: 0.78 }],
  };
  vi.stubGlobal("fetch", vi.fn((input: string | URL | Request) => {
    const url = String(input);
    if (url.endsWith("/odds-snapshots")) return Promise.resolve(jsonResponse([]));
    if (url.endsWith("/predictions")) return Promise.resolve(jsonResponse([prediction]));
    return Promise.reject(new Error(`Unexpected request: ${url}`));
  }));

  render(<PredictionPanel raceId={1} runners={[{
    horse_number: 1, win_odds: 2.4, place_odds_min: 1.3, place_odds_max: 1.6,
  }]} />);

  const card = await screen.findByText("固定済み / 独立予測 fake-independent-model test-1");
  const article = card.closest("article");
  expect(article).toBeTruthy();
  expect(within(article!).getByText("モデル時点 2026-08-30T04:54:00Z / 根拠 固定fixtureによる境界検証")).toBeTruthy();
  expect(within(article!).getByText("1番 単勝予測確率 42.00% / 複勝予測確率 78.00%")).toBeTruthy();
  expect(article!.textContent).not.toContain("市場投票シェア");
  expect(article!.textContent).not.toContain("損益分岐");
});
