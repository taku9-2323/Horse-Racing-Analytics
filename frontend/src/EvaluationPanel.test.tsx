import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import EvaluationPanel from "./EvaluationPanel";


const report = {
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
  returns: {
    candidate: { stake_yen: 0, payout_yen: 0, refund_yen: 0, profit_yen: 0, return_rate: null },
    discretionary: { stake_yen: 200, payout_yen: 840, refund_yen: 0, profit_yen: 640, return_rate: 4.2 },
  },
};


afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });


describe("evaluation panel", () => {
  it("shows calibration, small-sample bands, separated returns, and applies every filter", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(report), {
      status: 200, headers: { "Content-Type": "application/json" },
    }));
    vi.stubGlobal("fetch", fetchMock);
    render(<EvaluationPanel />);

    fireEvent.click(screen.getByRole("button", { name: "成績を表示" }));

    expect(await screen.findByText("0.0785")).toBeTruthy();
    expect(screen.getByText("公式評価対象 1予測 / 5頭")).toBeTruthy();
    expect(screen.getByText("公式評価対象外 2予測")).toBeTruthy();
    const calibration = screen.getByRole("table", { name: "確率帯別の校正" });
    expect(within(calibration).getByText("40–50%未満")).toBeTruthy();
    expect(within(calibration).getAllByText("少数標本")).toHaveLength(2);
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
});

const response = (body: object, status = 200) => new Response(JSON.stringify(body), { status });

it("keeps results paired with applied filters through edits, failures and reset", async () => {
  const filtered = { ...report, calibration: { ...report.calibration, brier_score: 0.25 } };
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
