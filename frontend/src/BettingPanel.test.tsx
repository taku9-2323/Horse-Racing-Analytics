import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import BettingPanel from "./BettingPanel";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const totals = (stake_yen = 0, payout_yen = 0, refund_yen = 0) => ({
  stake_yen, payout_yen, refund_yen,
  profit_yen: payout_yen + refund_yen - stake_yen,
  return_rate: stake_yen ? (payout_yen + refund_yen) / stake_yen : null,
});
const pendingTotals = (stake_yen: number) => ({
  stake_yen, payout_yen: 0, refund_yen: 0, profit_yen: null, return_rate: null,
});

const jsonResponse = (value: object, status = 200) => new Response(JSON.stringify(value), {
  status, headers: { "Content-Type": "application/json" },
});

describe("bet and result workflow", () => {
  it("registers a classified bet, imports official payouts, and shows the settlement", async () => {
    const prediction = { id: 8, model_identifier: "market-baseline", model_version: "1.0",
      frozen_at: "2026-08-30T05:00:00Z", status: "active", official_evaluation_eligible: true };
    const bet = { id: 1, horse_number: 1, bet_type: "win", decision_type: "discretionary", amount_yen: 200, prediction_run_id: 8 };
    const emptyLedger = {
      bets: [], result_version: null, settlements: [], totals: totals(),
      by_decision_type: { candidate: totals(), discretionary: totals() },
    };
    const betLedger = {
      ...emptyLedger, bets: [bet], totals: pendingTotals(200),
      by_decision_type: { candidate: totals(), discretionary: pendingTotals(200) },
    };
    const settledLedger = {
      ...betLedger, result_version: { id: 1, version: 1 },
      totals: totals(200, 840, 0),
      by_decision_type: { candidate: totals(), discretionary: totals(200, 840, 0) },
    };
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(jsonResponse(emptyLedger))
      .mockResolvedValueOnce(jsonResponse([prediction]))
      .mockResolvedValueOnce(jsonResponse(bet, 201))
      .mockResolvedValueOnce(jsonResponse(betLedger))
      .mockResolvedValueOnce(jsonResponse({ id: 1, version: 1 }, 201))
      .mockResolvedValueOnce(jsonResponse(settledLedger)));

    render(<BettingPanel raceId={1} runners={[{ horse_number: 1, horse_name: "アカツキ" }]} />);
    fireEvent.click(screen.getByRole("button", { name: "購入・収支を表示" }));
    expect(await screen.findByText("実購入はまだありません。")).toBeTruthy();

    fireEvent.change(screen.getByLabelText("購入判断に使った固定予測"), { target: { value: "8" } });
    fireEvent.change(screen.getByLabelText("購入額（円）"), { target: { value: "200" } });
    fireEvent.click(screen.getByRole("button", { name: "実購入を登録" }));
    expect(await screen.findByText("1番 / 単勝 / 候補外裁量 / ¥200 / 固定予測 #8")).toBeTruthy();
    expect(screen.getByText("損益 未確定")).toBeTruthy();

    const resultCsv = new File(["horse_number,finish_position,status,win_payout_per_100,place_payout_per_100\n1,1,確定,420,150"], "results.csv", { type: "text/csv" });
    fireEvent.change(screen.getByLabelText("結果CSVファイル"), { target: { files: [resultCsv] } });
    fireEvent.click(screen.getByRole("button", { name: "結果を取り込んで精算" }));

    const summary = await screen.findByRole("region", { name: "収支集計" });
    expect(within(summary).getByText("結果 v1 精算済み")).toBeTruthy();
    expect(within(summary).getByText("購入額 ¥200")).toBeTruthy();
    expect(within(summary).getByText("払戻額 ¥840")).toBeTruthy();
    expect(within(summary).getByText("返還額 ¥0")).toBeTruthy();
    expect(within(summary).getByText("損益 ¥640")).toBeTruthy();
    expect(within(summary).getByText("回収率 420.00%")).toBeTruthy();
    expect(screen.getByText("候補外裁量 購入額 ¥200 / 払戻額 ¥840 / 返還額 ¥0 / 損益 ¥640 / 回収率 420.00%")).toBeTruthy();
  });

  it("acquires the selected race result from JRA and refreshes settlement", async () => {
    const emptyLedger = {
      bets: [], result_version: null, settlements: [], totals: totals(),
      by_decision_type: { candidate: totals(), discretionary: totals() },
    };
    const settledLedger = {
      ...emptyLedger, result_version: { id: 3, version: 1 },
    };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse(emptyLedger))
      .mockResolvedValueOnce(jsonResponse([]))
      .mockResolvedValueOnce(jsonResponse({
        result: { id: 3, version: 1 }, changes: [],
        source: { parser_version: "jra-result/1" },
      }, 201))
      .mockResolvedValueOnce(jsonResponse(settledLedger));
    vi.stubGlobal("fetch", fetchMock);

    render(<BettingPanel raceId={7} runners={[{ horse_number: 1, horse_name: "アカツキ" }]} />);
    fireEvent.click(screen.getByRole("button", { name: "購入・収支を表示" }));
    await screen.findByText("結果未取込");
    fireEvent.change(screen.getByLabelText("JRAレース結果URL"), {
      target: { value: "https://www.jra.go.jp/JRADB/accessS.html?CNAME=result" },
    });
    fireEvent.click(screen.getByRole("button", { name: "JRA結果を取得して精算" }));

    expect(await screen.findByText("JRA結果を取得し、結果 v1 で精算しました（jra-result/1）。")).toBeTruthy();
    expect(screen.getByText("結果 v1 精算済み")).toBeTruthy();
    expect(fetchMock).toHaveBeenNthCalledWith(3, "/api/races/7/results/acquire", expect.objectContaining({
      method: "POST", body: JSON.stringify({ url: "https://www.jra.go.jp/JRADB/accessS.html?CNAME=result" }),
    }));
  });

  it("continues through CSV settlement when JRA acquisition stops", async () => {
    const emptyLedger = {
      bets: [], result_version: null, settlements: [], totals: totals(),
      by_decision_type: { candidate: totals(), discretionary: totals() },
    };
    const settledLedger = {
      ...emptyLedger, result_version: { id: 4, version: 1 },
    };
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(jsonResponse(emptyLedger))
      .mockResolvedValueOnce(jsonResponse([]))
      .mockResolvedValueOnce(jsonResponse({
        detail: { code: "acquisition_stopped", message: "JRAからの取得を停止しました。CSV取込を使用してください。" },
      }, 503))
      .mockResolvedValueOnce(jsonResponse({ id: 4, version: 1 }, 201))
      .mockResolvedValueOnce(jsonResponse(settledLedger)));

    render(<BettingPanel raceId={7} runners={[{ horse_number: 1, horse_name: "アカツキ" }]} />);
    fireEvent.click(screen.getByRole("button", { name: "購入・収支を表示" }));
    await screen.findByText("結果未取込");
    fireEvent.change(screen.getByLabelText("JRAレース結果URL"), { target: { value: "https://www.jra.go.jp/JRADB/accessS.html?CNAME=result" } });
    fireEvent.click(screen.getByRole("button", { name: "JRA結果を取得して精算" }));

    expect(await screen.findByText("JRAからの取得を停止しました。CSV取込を使用してください。")).toBeTruthy();
    const resultCsv = new File([
      "horse_number,finish_position,status,win_payout_per_100,place_payout_per_100\n1,1,確定,420,150",
    ], "results.csv", { type: "text/csv" });
    fireEvent.change(screen.getByLabelText("結果CSVファイル"), { target: { files: [resultCsv] } });
    fireEvent.click(screen.getByRole("button", { name: "結果を取り込んで精算" }));

    expect(await screen.findByText("公式結果を取り込み、購入を精算しました。")).toBeTruthy();
    expect(screen.getByText("結果 v1 精算済み")).toBeTruthy();
  });
});


const emptyLedger = {
  bets: [], result_version: null, totals: totals(),
  by_decision_type: { candidate: totals(), discretionary: totals() },
};

async function openBettingPanel(fetchMock: ReturnType<typeof vi.fn>) {
  vi.stubGlobal("fetch", fetchMock);
  render(<BettingPanel raceId={1} runners={[{ horse_number: 1, horse_name: "アカツキ" }]} />);
  fireEvent.click(screen.getByRole("button", { name: "購入・収支を表示" }));
  await screen.findByText("実購入はまだありません。");
}

it.each(["bet", "import", "correct"])("guards repeated %s requests while saving and permits a later operation", async (operation) => {
  let finish!: (response: Response) => void;
  const pending = new Promise<Response>((resolve) => { finish = resolve; });
  const initial = operation === "correct" ? { ...emptyLedger, result_version: { id: 1, version: 1 } } : emptyLedger;
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(jsonResponse(initial))
    .mockResolvedValueOnce(jsonResponse([]))
    .mockReturnValueOnce(pending)
    .mockResolvedValueOnce(jsonResponse(initial))
    .mockResolvedValueOnce(jsonResponse({}, 201))
    .mockResolvedValueOnce(jsonResponse(initial));
  await openBettingPanel(fetchMock);
  const label = operation === "bet" ? "実購入を登録" : operation === "import" ? "結果を取り込んで精算" : "訂正版で再精算";
  if (operation !== "bet") fireEvent.change(screen.getByLabelText("結果CSVファイル"), { target: { files: [new File(["csv"], "results.csv")] } });
  if (operation === "correct") fireEvent.change(screen.getByLabelText("訂正理由"), { target: { value: "公式訂正" } });
  const button = screen.getByRole("button", { name: label });
  fireEvent.click(button);
  fireEvent.click(button);
  expect(fetchMock.mock.calls.filter(([, options]) => options?.method === "POST")).toHaveLength(1);
  expect((button as HTMLButtonElement).disabled).toBe(true);
  await act(async () => { finish(jsonResponse({}, 201)); });
  if (operation === "correct") fireEvent.change(screen.getByLabelText("訂正理由"), { target: { value: "追加訂正" } });
  expect((screen.getByRole("button", { name: label }) as HTMLButtonElement).disabled).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: label }));
  await act(async () => {});
  expect(fetchMock.mock.calls.filter(([, options]) => options?.method === "POST")).toHaveLength(2);
});

it("recovers a saved bet with GET only after ledger refresh fails", async () => {
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(jsonResponse(emptyLedger))
    .mockResolvedValueOnce(jsonResponse([]))
    .mockResolvedValueOnce(jsonResponse({}, 201))
    .mockRejectedValueOnce(new Error("offline"))
    .mockResolvedValueOnce(jsonResponse(emptyLedger));
  await openBettingPanel(fetchMock);
  fireEvent.click(screen.getByRole("button", { name: "実購入を登録" }));
  await screen.findByText(/登録済み・表示更新に失敗/);
  expect((screen.getByRole("button", { name: "実購入を登録" }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "台帳を再読み込み" }));
  await screen.findByText("保存済みの購入台帳を更新しました。");
  expect(fetchMock.mock.calls.filter(([, options]) => options?.method === "POST")).toHaveLength(1);
});

it("preserves input and announces a rejected purchase before retry", async () => {
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(jsonResponse(emptyLedger))
    .mockResolvedValueOnce(jsonResponse([]))
    .mockResolvedValueOnce(jsonResponse({ detail: "購入額を確認してください" }, 400))
    .mockResolvedValueOnce(jsonResponse({}, 201))
    .mockResolvedValueOnce(jsonResponse(emptyLedger));
  await openBettingPanel(fetchMock);
  fireEvent.change(screen.getByLabelText("購入額（円）"), { target: { value: "200" } });
  fireEvent.click(screen.getByRole("button", { name: "実購入を登録" }));
  await screen.findByText("購入額を確認してください");
  expect(screen.getByRole("status").textContent).toBe("購入額を確認してください");
  expect((screen.getByLabelText("購入額（円）") as HTMLInputElement).value).toBe("200");
  fireEvent.click(screen.getByRole("button", { name: "実購入を登録" }));
  await screen.findByText("実購入を登録しました。");
});
