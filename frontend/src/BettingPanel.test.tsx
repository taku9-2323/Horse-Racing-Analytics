import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
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
    const bet = { id: 1, horse_number: 1, bet_type: "win", decision_type: "discretionary", amount_yen: 200 };
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
      .mockResolvedValueOnce(jsonResponse(bet, 201))
      .mockResolvedValueOnce(jsonResponse(betLedger))
      .mockResolvedValueOnce(jsonResponse({ id: 1, version: 1 }, 201))
      .mockResolvedValueOnce(jsonResponse(settledLedger)));

    render(<BettingPanel raceId={1} runners={[{ horse_number: 1, horse_name: "アカツキ" }]} />);
    fireEvent.click(screen.getByRole("button", { name: "購入・収支を表示" }));
    expect(await screen.findByText("実購入はまだありません。")).toBeTruthy();

    fireEvent.change(screen.getByLabelText("購入額（円）"), { target: { value: "200" } });
    fireEvent.click(screen.getByRole("button", { name: "実購入を登録" }));
    expect(await screen.findByText("1番 / 単勝 / 候補外裁量 / ¥200")).toBeTruthy();
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
    expect(fetchMock).toHaveBeenNthCalledWith(2, "/api/races/7/results/acquire", expect.objectContaining({
      method: "POST", body: JSON.stringify({ url: "https://www.jra.go.jp/JRADB/accessS.html?CNAME=result" }),
    }));
  });

  it("keeps the result CSV fallback visible when JRA acquisition stops", async () => {
    const emptyLedger = {
      bets: [], result_version: null, settlements: [], totals: totals(),
      by_decision_type: { candidate: totals(), discretionary: totals() },
    };
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(jsonResponse(emptyLedger))
      .mockResolvedValueOnce(jsonResponse({
        detail: { code: "acquisition_stopped", message: "JRAからの取得を停止しました。CSV取込を使用してください。" },
      }, 503)));

    render(<BettingPanel raceId={7} runners={[{ horse_number: 1, horse_name: "アカツキ" }]} />);
    fireEvent.click(screen.getByRole("button", { name: "購入・収支を表示" }));
    await screen.findByText("結果未取込");
    fireEvent.change(screen.getByLabelText("JRAレース結果URL"), { target: { value: "https://www.jra.go.jp/JRADB/accessS.html?CNAME=result" } });
    fireEvent.click(screen.getByRole("button", { name: "JRA結果を取得して精算" }));

    expect(await screen.findByText("JRAからの取得を停止しました。CSV取込を使用してください。")).toBeTruthy();
    expect(screen.getByLabelText("結果CSVファイル")).toBeTruthy();
  });
});
