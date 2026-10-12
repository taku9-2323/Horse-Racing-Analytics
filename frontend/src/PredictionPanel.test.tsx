import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
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
    if (url.endsWith("/rule-judgements")) return Promise.resolve(jsonResponse([]));
    return Promise.reject(new Error(`Unexpected request: ${url}`));
  }));

  render(<PredictionPanel raceId={1} runners={[{
    horse_number: 1, win_odds: 2.4, place_odds_min: 1.3, place_odds_max: 1.6,
  }]} />);

  const card = await screen.findByText("固定済み / 独立予測 fake-independent-model test-1");
  const article = card.closest("article");
  expect(article).toBeTruthy();
  expect(article!.textContent).toContain("モデル時点 2026/08/30 13:54 JST / 根拠 固定fixtureによる境界検証");
  expect(within(article!).getByTitle("2026-08-30T04:54:00Z").textContent).toBe("2026/08/30 13:54 JST");
  expect(within(article!).getByText("1番 単勝予測確率 42.00% / 複勝予測確率 78.00%")).toBeTruthy();
  expect(article!.textContent).not.toContain("市場投票シェア");
  expect(article!.textContent).not.toContain("損益分岐");
});
it("compares the selected saved snapshot with its immediate predecessor and labels frozen references", async () => {
  const firstSnapshot = {
    id: 1, race_id: 1, observed_at: "2026-08-30T04:00:00Z",
    received_at: "2026-08-30T04:02:00Z", source_updated_at: "2026-08-30T03:58:00Z",
    source: "jra_web",
    runners: [{ horse_number: 1, win_odds: 2, place_odds_min: 1.2, place_odds_max: 1.5 }],
  };
  const secondSnapshot = {
    id: 2, race_id: 1, observed_at: "2026-08-30T05:00:00Z",
    received_at: "2026-08-30T05:02:00Z", source_updated_at: null,
    source: "user_csv",
    runners: [{ horse_number: 1, win_odds: 2.5, place_odds_min: 1.3, place_odds_max: 1.8 }],
  };
  const newLatest = {
    ...secondSnapshot, id: 3, observed_at: "2026-08-30T05:30:00Z",
    received_at: "2026-08-30T05:31:00Z",
    runners: [{ horse_number: 1, win_odds: 2.8, place_odds_min: 1.4, place_odds_max: 1.9 }],
  };
  const prediction = {
    id: 51, race_id: 1, input_snapshot_id: 1, prediction_kind: "market_baseline",
    model_identifier: "market-baseline", model_version: "1.0",
    prediction_as_of: firstSnapshot.observed_at, rationale: "fixture",
    output_capabilities: ["win"], frozen_at: firstSnapshot.received_at,
    status: "active", invalidation_reason: null, replaces_prediction_id: null,
    official_evaluation_eligible: true, evaluation_exclusion_reason: null,
    analysis_tags: [], runners: [{ horse_number: 1, raw_inverse_win_odds: 0.5, win_market_share: 1 }],
  };
  const oldPrediction = {
    ...prediction, id: 52, status: "invalidated", invalidation_reason: "fixture correction",
  };
  const judgement = {
    id: 71, race_id: 1, input_snapshot_id: 1, rule_version_id: 4,
    judgement_as_of: firstSnapshot.observed_at, frozen_at: firstSnapshot.received_at,
    status: "active", invalidation_reason: null, replaces_judgement_id: null,
  };
  const oldJudgement = {
    ...judgement, id: 72, status: "invalidated", invalidation_reason: "fixture correction",
  };
  let snapshotRead = 0;
  const methods: string[] = [];
  vi.stubGlobal("fetch", vi.fn((input: string | URL | Request, init?: RequestInit) => {
    const url = String(input);
    methods.push(init?.method ?? "GET");
    if (url.endsWith("/odds-snapshots")) {
      snapshotRead += 1;
      return Promise.resolve(jsonResponse(snapshotRead === 1
        ? [firstSnapshot, secondSnapshot]
        : [firstSnapshot, secondSnapshot, newLatest]));
    }
    if (url.endsWith("/predictions")) return Promise.resolve(jsonResponse([prediction, oldPrediction]));
    if (url.endsWith("/rule-judgements")) return Promise.resolve(jsonResponse([judgement, oldJudgement]));
    return Promise.reject(new Error("Unexpected request: " + url));
  }));

  render(<PredictionPanel raceId={1} runners={[{
    horse_number: 1, win_odds: 2.5, place_odds_min: 1.3, place_odds_max: 1.8,
  }]} />);

  const select = await screen.findByRole("combobox", { name: "比較する保存時点" });
  expect((select as HTMLSelectElement).value).toBe("2");
  expect(screen.getByText("最新保存済み")).toBeTruthy();
  expect(screen.getAllByText(/取得元更新時刻不明/).length).toBeGreaterThan(0);
  expect(screen.getByText("この表示を開いた時点での経過")).toBeTruthy();
  expect(screen.getByText(/観測間隔: 60分/)).toBeTruthy();
  expect(screen.getByText("差 +0.50")).toBeTruthy();
  expect(screen.getByText("前値比 +25.00%")).toBeTruthy();
  expect(screen.getByText("固定予測参照: #51 固定済み, #52 無効化済み")).toBeTruthy();
  expect(screen.getByText("固定判定参照: #71 ルール版ID 4 固定済み, #72 ルール版ID 4 無効化済み")).toBeTruthy();

  fireEvent.change(select, { target: { value: "1" } });
  expect((select as HTMLSelectElement).value).toBe("1");
  expect(methods).toEqual(["GET", "GET", "GET"]);
  fireEvent.click(screen.getByRole("button", { name: "保存履歴を再読込" }));
  await screen.findAllByText("時点 #3");
  expect((screen.getByRole("combobox", { name: "比較する保存時点" }) as HTMLSelectElement).value).toBe("1");
  expect(screen.getByText("最新保存済み")).toBeTruthy();
  expect(methods).toEqual(["GET", "GET", "GET", "GET", "GET", "GET"]);
});

it("shows malformed observation timestamps as visible raw text and suppresses every time-based comparison", async () => {
  const previousSnapshot = {
    id: 10, race_id: 1, observed_at: "2026-08-30T10:00:00Z",
    received_at: "2026-08-30T10:02:00Z", source_updated_at: null, source: "user_csv",
    runners: [{ horse_number: 1, win_odds: 2, place_odds_min: 1.2, place_odds_max: 1.5 }],
  };
  const malformedObservedAt = "2026-08-30T11:00:00-with-invalid-tail";
  const selectedSnapshot = {
    id: 11, race_id: 1, observed_at: malformedObservedAt,
    received_at: "2026-08-30T11:02:00Z", source_updated_at: null, source: "user_csv",
    runners: [{ horse_number: 1, win_odds: 3, place_odds_min: 1.4, place_odds_max: 1.8 }],
  };
  vi.stubGlobal("fetch", vi.fn((input: string | URL | Request) => {
    const url = String(input);
    if (url.endsWith("/odds-snapshots")) return Promise.resolve(jsonResponse([previousSnapshot, selectedSnapshot]));
    if (url.endsWith("/predictions") || url.endsWith("/rule-judgements")) return Promise.resolve(jsonResponse([]));
    return Promise.reject(new Error(`Unexpected request: ${url}`));
  }));

  render(<PredictionPanel raceId={1} runners={[{
    horse_number: 1, win_odds: 3, place_odds_min: 1.4, place_odds_max: 1.8,
  }]} />);

  await screen.findByText(/観測時刻を解釈できないため時系列比較できません。/);
  const visibleRawTimes = document.querySelectorAll(".snapshot-invalid-time code");
  expect(Array.from(visibleRawTimes).some((node) => node.textContent === malformedObservedAt)).toBe(true);
  expect(screen.queryByText(/^観測間隔:/)).toBeNull();
  expect(screen.queryByText(/^差 /)).toBeNull();
  expect(screen.queryByText(/^前値比 /)).toBeNull();
});

it.each([
  {
    invalidSide: "selected",
    previousObservedAt: "2026-02-28T10:00:00Z",
    selectedObservedAt: "2026-02-30T10:00:00Z",
  },
  {
    invalidSide: "previous",
    previousObservedAt: "2026-02-30T10:00:00Z",
    selectedObservedAt: "2026-03-03T10:00:00Z",
  },
])("shows an invalid calendar date on the $invalidSide snapshot and suppresses all comparison metrics", async ({
  previousObservedAt,
  selectedObservedAt,
}) => {
  const previousSnapshot = {
    id: 20, race_id: 1, observed_at: previousObservedAt,
    received_at: "2026-03-01T10:02:00Z", source_updated_at: null, source: "user_csv",
    runners: [{ horse_number: 1, win_odds: 2, place_odds_min: 1.2, place_odds_max: 1.5 }],
  };
  const selectedSnapshot = {
    id: 21, race_id: 1, observed_at: selectedObservedAt,
    received_at: "2026-03-03T10:02:00Z", source_updated_at: null, source: "user_csv",
    runners: [{ horse_number: 1, win_odds: 3, place_odds_min: 1.4, place_odds_max: 1.8 }],
  };
  vi.stubGlobal("fetch", vi.fn((input: string | URL | Request) => {
    const url = String(input);
    if (url.endsWith("/odds-snapshots")) return Promise.resolve(jsonResponse([previousSnapshot, selectedSnapshot]));
    if (url.endsWith("/predictions") || url.endsWith("/rule-judgements")) return Promise.resolve(jsonResponse([]));
    return Promise.reject(new Error(`Unexpected request: ${url}`));
  }));

  render(<PredictionPanel raceId={1} runners={[{
    horse_number: 1, win_odds: 3, place_odds_min: 1.4, place_odds_max: 1.8,
  }]} />);

  await screen.findByText(/観測時刻を解釈できないため時系列比較できません。/);
  const visibleRawTimes = document.querySelectorAll(".snapshot-invalid-time code");
  expect(Array.from(visibleRawTimes).filter((node) => node.textContent === "2026-02-30T10:00:00Z")).toHaveLength(1 + Number(selectedObservedAt === "2026-02-30T10:00:00Z"));
  expect(screen.queryByText("2026/03/02 19:00 JST")).toBeNull();
  expect(screen.queryByText(/^観測間隔:/)).toBeNull();
  expect(screen.queryByText(/^差 /)).toBeNull();
  expect(screen.queryByText(/^前値比 /)).toBeNull();
});
