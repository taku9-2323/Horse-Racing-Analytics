import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import EvaluationPanel from "./EvaluationPanel";


const report = {
  calibration_status: "single_group" as const,
  calibration_reason: null,
  filters: {
    model_identifier: null, model_version: null, tag_rule_key: null, tag_version: null,
    racecourse: null, bet_type: null, odds_min: null, odds_max: null,
    popularity_min: null, popularity_max: null,
    prediction_frozen_from: null, prediction_frozen_to: null,
  },
  filter_options: {
    models: [{ identifier: "market-baseline", version: "1.0" }],
    tags: [{ rule_key: "market_odds_level", version: 1 }],
    racecourses: ["東京"], bet_types: ["place", "win"],
  },
  calibration: {
    eligible_prediction_runs: 1, excluded_prediction_runs: 2,
    runner_count: 5, brier_score: 19 / 242,
    bands: [
      { lower_bound: 0, upper_bound: 0.1, count: 2,
        average_predicted_probability: 0.06818, actual_win_rate: 0, small_sample: true },
      { lower_bound: 0.4, upper_bound: 0.5, count: 1,
        average_predicted_probability: 5 / 11, actual_win_rate: 1, small_sample: true },
    ],
  },
  calibration_groups: [{
    model_identifier: "market-baseline", model_version: "1.0", bet_type: "win" as const,
    eligible_prediction_runs: 1, excluded_prediction_runs: 2,
    raw_unique_run_count: 3, eligible_candidate_run_count: 1,
    capability_qualified_run_count: 1, selected_prediction_run_count: 1,
    ineligible_excluded_run_count: 2, unsupported_bet_excluded_run_count: 0,
    duplicate_excluded_run_count: 0, race_date_from: "2026-08-30", race_date_to: "2026-08-30",
    prediction_frozen_from: "2026-08-30T04:55:00Z", prediction_frozen_to: "2026-08-30T04:55:00Z",
    distinct_race_count: 1, distinct_observation_count: 1, runner_observation_count: 5,
    brier_score: 19 / 242,
    bands: [
      { lower_bound: 0, upper_bound: 0.1, count: 2,
        average_predicted_probability: 0.06818, actual_win_rate: 0 },
      { lower_bound: 0.4, upper_bound: 0.5, count: 1,
        average_predicted_probability: 5 / 11, actual_win_rate: 1 },
    ],
    market_comparison_status: "not_applicable" as const,
    market_comparison_reason: "市場基準groupは独立モデルとの比較対象として扱いません。",
    market_comparisons: [],
    uncertainty_status: "not_estimated" as const, uncertainty_interval: null,
    uncertainty_reason: "レース内相関を考慮した不確実性推定法は未実装・未検証です。",
  }],
  returns: {
    candidate: { stake_yen: 0, payout_yen: 0, refund_yen: 0, profit_yen: 0, return_rate: null },
    discretionary: { stake_yen: 200, payout_yen: 840, refund_yen: 0, profit_yen: 640, return_rate: 4.2 },
  },
};


afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });


describe("evaluation panel", () => {
  it("shows calibration groups and separated returns, and applies every filter", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(report), {
      status: 200, headers: { "Content-Type": "application/json" },
    }));
    vi.stubGlobal("fetch", fetchMock);
    render(<EvaluationPanel />);

    fireEvent.click(screen.getByRole("button", { name: "成績を表示" }));

    expect(await screen.findByText("0.0785")).toBeTruthy();
    expect(screen.getByText("market-baseline / 1.0 / 単勝")).toBeTruthy();
    expect(screen.getByText("run総数 3 / 公式対象候補 1 / 券種対応候補 1 / 採用 1 / 除外（公式対象外 2・券種非対応 0・重複 0）")).toBeTruthy();
    const calibration = screen.getByRole("table", { name: /確率帯別の校正/ });
    expect(within(calibration).getByText("40–50%未満")).toBeTruthy();
    expect(within(calibration).queryByText("十分")).toBeNull();
    expect(screen.getByText(/レース内相関を考慮した不確実性推定法/)).toBeTruthy();
    const returns = screen.getByRole("region", { name: "購入区分別収支" });
    expect(within(returns).getByText("候補内実購入")).toBeTruthy();
    expect(within(returns).getByText("候補外裁量")).toBeTruthy();
    expect(within(returns).getByText(/回収率 420\.00%/)).toBeTruthy();

    fireEvent.change(screen.getByLabelText("モデル版"), { target: { value: JSON.stringify(["market-baseline", "1.0"]) } });
    fireEvent.change(screen.getByLabelText("タグ版"), { target: { value: JSON.stringify(["market_odds_level", "1"]) } });
    fireEvent.change(screen.getByLabelText("競馬場"), { target: { value: "東京" } });
    fireEvent.change(screen.getByLabelText("券種"), { target: { value: "win" } });
    fireEvent.change(screen.getByLabelText("オッズ下限"), { target: { value: "1" } });
    fireEvent.change(screen.getByLabelText("オッズ上限"), { target: { value: "3" } });
    fireEvent.change(screen.getByLabelText("人気下限"), { target: { value: "1" } });
    fireEvent.change(screen.getByLabelText("人気上限"), { target: { value: "1" } });
    fireEvent.change(screen.getByLabelText("予測固定時刻（開始）"), { target: { value: "2026-08-30T13:59" } });
    fireEvent.change(screen.getByLabelText("予測固定時刻（終了）"), { target: { value: "2026-08-30T14:01" } });
    fireEvent.click(screen.getByRole("button", { name: "条件を適用" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    const requested = new URL(String(fetchMock.mock.calls[1][0]), "http://localhost");
    expect(Object.fromEntries(requested.searchParams)).toEqual({
      model_identifier: "market-baseline", model_version: "1.0",
      tag_rule_key: "market_odds_level", tag_version: "1",
      racecourse: "東京", bet_type: "win", odds_min: "1", odds_max: "3",
      popularity_min: "1", popularity_max: "1",
      prediction_frozen_from: new Date("2026-08-30T13:59").toISOString(),
      prediction_frozen_to: new Date("2026-08-30T14:01").toISOString(),
    });
  });

  it("shows each market baseline version separately without pooling their scores", async () => {
    const baseGroup = report.calibration_groups[0];
    const comparison = (baseline_version: string, market_brier_score: number) => ({
      baseline_version, status: "available" as const, reason: null,
      model_brier_score: 0.04, market_brier_score, brier_difference: 0.04 - market_brier_score,
      matched_runner_observation_count: 1, matched_race_count: 1, matched_observation_count: 1,
      raw_unique_run_count: 1, eligible_candidate_run_count: 1, capability_qualified_run_count: 1,
      selected_run_count: 1, ineligible_excluded_run_count: 0,
      unsupported_bet_excluded_run_count: 0, duplicate_excluded_run_count: 0,
    });
    const independentReport = {
      ...report,
      calibration_groups: [{
        ...baseGroup, model_identifier: "model-a", model_version: "independent-v7",
        market_comparison_status: "available" as const, market_comparison_reason: null,
        market_comparisons: [comparison("baseline-1", 0.25), comparison("baseline-2", 0.64)],
      }],
    };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(independentReport)));
    render(<EvaluationPanel />);
    fireEvent.click(screen.getByRole("button", { name: "成績を表示" }));
    expect(await screen.findByText("model-a / independent-v7 / 単勝")).toBeTruthy();
    expect(screen.getByRole("article", { name: "市場基準版 baseline-1" })).toBeTruthy();
    expect(screen.getByRole("article", { name: "市場基準版 baseline-2" })).toBeTruthy();
    expect(screen.getByText(/市場 Brier 0\.2500/)).toBeTruthy();
    expect(screen.getByText(/市場 Brier 0\.6400/)).toBeTruthy();
  });

  it("explains when a baseline is absent and when place has no market comparison", async () => {
    const baseGroup = report.calibration_groups[0];
    const noBaseline = {
      ...baseGroup, model_identifier: "model-a", model_version: "model-v1",
      market_comparison_status: "no_baseline" as const,
      market_comparison_reason: "有効な市場基準runがありません。", market_comparisons: [],
    };
    const place = {
      ...noBaseline, bet_type: "place" as const,
      market_comparison_status: "not_applicable" as const,
      market_comparison_reason: "複勝には比較対象の市場確率がありません。",
    };
    const noBaselineReport = {
      ...report, calibration_status: "multiple_groups" as const,
      calibration_reason: "モデル版・券種ごとに分けて集計しています。",
      calibration_groups: [noBaseline, place],
    };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(noBaselineReport)));
    render(<EvaluationPanel />);
    fireEvent.click(screen.getByRole("button", { name: "成績を表示" }));
    expect(await screen.findByText("有効な市場基準runがありません。")).toBeTruthy();
    expect(screen.getByText("複勝には比較対象の市場確率がありません。")).toBeTruthy();
    expect(screen.queryByRole("article", { name: /市場基準版/ })).toBeNull();
  });

  it("explains an empty result without rendering a calibration score", async () => {
    const emptyReport = {
      ...report,
      calibration: null,
      calibration_status: "no_groups" as const,
      calibration_reason: "フィルターに一致する予測runがありません。",
      calibration_groups: [],
    };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(emptyReport)));
    render(<EvaluationPanel />);
    fireEvent.click(screen.getByRole("button", { name: "成績を表示" }));

    expect(await screen.findByText("フィルターに一致する予測runがありません。")).toBeTruthy();
    expect(screen.queryByText("Brierスコア")).toBeNull();
    expect(screen.queryByRole("table", { name: /確率帯別の校正/ })).toBeNull();
  });
});

const response = (body: object, status = 200) => new Response(JSON.stringify(body), { status });

it("keeps results paired with applied filters through edits, failures and reset", async () => {
  const filtered = {
    ...report,
    calibration_groups: [{ ...report.calibration_groups[0], brier_score: 0.25 }],
  };
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(response(report))
    .mockResolvedValueOnce(response(filtered))
    .mockResolvedValueOnce(response({ detail: "通信失敗" }, 503))
    .mockResolvedValueOnce(response({ detail: "通信失敗" }, 503))
    .mockResolvedValueOnce(response(report));
  vi.stubGlobal("fetch", fetchMock);
  render(<EvaluationPanel />);
  fireEvent.click(screen.getByRole("button", { name: "成績を表示" }));
  await screen.findByText("0.0785");
  fireEvent.change(screen.getByLabelText("オッズ下限"), { target: { value: "10" } });
  expect(screen.getByText("未適用の変更があります。")).toBeTruthy();
  expect(screen.getByLabelText("適用済み条件").textContent).toBe("適用済み条件：すべて");
  fireEvent.click(screen.getByRole("button", { name: "条件を適用" }));
  await screen.findByText("0.2500");
  expect(screen.getByLabelText("適用済み条件").textContent).toContain("オッズ下限: 10");
  expect(screen.queryByText("未適用の変更があります。")).toBeNull();
  fireEvent.change(screen.getByLabelText("オッズ下限"), { target: { value: "20" } });
  fireEvent.click(screen.getByRole("button", { name: "条件を適用" }));
  expect((await screen.findByRole("alert")).textContent).toContain("更新失敗");
  expect(screen.getByText("0.2500")).toBeTruthy();
  expect(screen.getByLabelText("適用済み条件").textContent).toContain("オッズ下限: 10");
  fireEvent.click(screen.getByRole("button", { name: "条件をリセット" }));
  await screen.findByRole("alert");
  expect((screen.getByLabelText("オッズ下限") as HTMLInputElement).value).toBe("");
  expect(screen.getByLabelText("適用済み条件").textContent).toContain("オッズ下限: 10");
  fireEvent.click(screen.getByRole("button", { name: "条件をリセット" }));
  await screen.findByText("0.0785");
  expect(screen.getByLabelText("適用済み条件").textContent).toBe("適用済み条件：すべて");
  expect(fetchMock.mock.calls[4][0]).toBe("/api/evaluation");
});

it("preserves model and tag identities after option order changes", async () => {
  const extraModel = { identifier: "another-model", version: "2.0" };
  const extraTag = { rule_key: "another-tag", version: 2 };
  const reordered = { ...report, filter_options: { ...report.filter_options,
    models: [extraModel, ...report.filter_options.models], tags: [extraTag, ...report.filter_options.tags] } };
  const fetchMock = vi.fn().mockResolvedValueOnce(response(report))
    .mockResolvedValueOnce(response(reordered)).mockResolvedValueOnce(response(reordered));
  vi.stubGlobal("fetch", fetchMock);
  render(<EvaluationPanel />);
  fireEvent.click(screen.getByRole("button", { name: "成績を表示" }));
  await screen.findByText("0.0785");
  fireEvent.change(screen.getByLabelText("モデル版"), { target: { value: JSON.stringify(["market-baseline", "1.0"]) } });
  fireEvent.change(screen.getByLabelText("タグ版"), { target: { value: JSON.stringify(["market_odds_level", "1"]) } });
  fireEvent.click(screen.getByRole("button", { name: "条件を適用" }));
  await screen.findByRole("option", { name: "another-model / 2.0" });
  fireEvent.click(screen.getByRole("button", { name: "条件を適用" }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
  const params = new URL(fetchMock.mock.calls[2][0], "http://localhost").searchParams;
  expect(params.get("model_identifier")).toBe("market-baseline");
  expect(params.get("tag_rule_key")).toBe("market_odds_level");
});
