import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "./App";

const healthResponse = (databaseStatus: "ok" | "error", status = 200) =>
  new Response(
    JSON.stringify({
      service: "Horse Racing Analytics",
      status: databaseStatus === "ok" ? "ok" : "degraded",
      api: { status: "ok" },
      database: { status: databaseStatus, engine: "sqlite" },
    }),
    {
      status,
      headers: { "Content-Type": "application/json" },
    },
  );

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("system status", () => {
  it("shows the API and SQLite as running when the health endpoint is ready", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(healthResponse("ok")));

    render(<App />);

    const apiCard = await screen.findByRole("article", { name: "APIの状態" });
    const databaseCard = screen.getByRole("article", { name: "データベースの状態" });
    expect(within(apiCard).getByText("稼働中")).toBeTruthy();
    expect(within(databaseCard).getByText("稼働中")).toBeTruthy();
    expect(within(databaseCard).getByText("SQLiteへ接続できています。")).toBeTruthy();
  });

  it("shows that only SQLite is stopped when the API returns degraded health", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(healthResponse("error", 503)));

    render(<App />);

    const apiCard = await screen.findByRole("article", { name: "APIの状態" });
    const databaseCard = screen.getByRole("article", { name: "データベースの状態" });
    expect(within(apiCard).getByText("稼働中")).toBeTruthy();
    expect(within(databaseCard).getByText("停止中")).toBeTruthy();
    expect(within(databaseCard).getByText("SQLiteへ接続できません。")).toBeTruthy();
  });
});

describe("race analysis", () => {
  it("imports a CSV and clearly separates market shares from place break-even rates", async () => {
    const analysis = {
      race_id: 1,
      race: {
        organizer: "JRA", country: "JP", racecourse: "東京", race_date: "2026-08-30", race_number: 11,
        start_time: "15:40", surface: "芝", distance_m: 2000, going: "良", field_size: 5,
        timezone: "Asia/Tokyo", start_utc: "2026-08-30T06:40:00Z",
      },
      runners: [{
        horse_number: 1, horse_name: "アカツキ", win_odds: 2,
        raw_inverse_win_odds: 0.5, normalized_win_market_share: 0.454545,
        place_odds_min: 1.2, place_odds_max: 1.5,
        place_break_even_hit_rate: { minimum: 0.666667, midpoint: 0.740741, maximum: 0.833333 },
      }],
      candidate_status: "期待値候補なし",
      candidate_reason: "市場基準は独立した予測確率ではないため、候補を生成しません。",
    };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(healthResponse("ok"))
      .mockResolvedValueOnce(new Response(JSON.stringify(analysis), {
        status: 201, headers: { "Content-Type": "application/json" },
      }));
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);
    await screen.findByRole("article", { name: "APIの状態" });

    const file = new File(["racecourse\n東京"], "race.csv", { type: "text/csv" });
    fireEvent.change(screen.getByLabelText("CSVファイル"), { target: { files: [file] } });
    fireEvent.click(screen.getByRole("button", { name: "取り込んで分析" }));

    expect(await screen.findByRole("heading", { name: "東京 11R" })).toBeTruthy();
    expect(screen.getByText("単勝市場投票シェア")).toBeTruthy();
    expect(screen.getByText("45.45%")).toBeTruthy();
    expect(screen.getByText("複勝損益分岐的中率（下限 / 代表 / 上限）")).toBeTruthy();
    expect(screen.getByText("66.67% / 74.07% / 83.33%")).toBeTruthy();
    expect(screen.getByText("期待値候補なし")).toBeTruthy();
  });

  it("shows every CSV error with its row, column, code, and description", async () => {
    const errorResponse = new Response(JSON.stringify({ detail: {
      code: "csv_validation_failed",
      message: "CSVに修正が必要な箇所があります。",
      errors: [
        { row: 2, column: "win_odds", code: "must_be_positive", description: "0より大きい値を指定してください。" },
        { row: 3, column: "age", code: "invalid_integer", description: "整数で指定してください。" },
      ],
    }}), { status: 422, headers: { "Content-Type": "application/json" } });
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(healthResponse("ok"))
      .mockResolvedValueOnce(errorResponse));
    render(<App />);
    await screen.findByRole("article", { name: "APIの状態" });

    fireEvent.change(screen.getByLabelText("CSVファイル"), {
      target: { files: [new File(["invalid"], "invalid.csv", { type: "text/csv" })] },
    });
    fireEvent.click(screen.getByRole("button", { name: "取り込んで分析" }));

    const alert = await screen.findByRole("alert");
    expect(within(alert).getByText("2行目 / win_odds / must_be_positive")).toBeTruthy();
    expect(within(alert).getByText("3行目 / age / invalid_integer")).toBeTruthy();
    expect(within(alert).getByText("0より大きい値を指定してください。")).toBeTruthy();
    expect(within(alert).getByText("整数で指定してください。")).toBeTruthy();
  });
});

describe("prediction freezing", () => {
  it("loads history, saves edited odds, freezes, and creates a reasoned correction", async () => {
    const analysis = {
      race_id: 1,
      race: { organizer: "JRA", country: "JP", racecourse: "東京", race_date: "2026-08-30", race_number: 11,
        start_time: "15:40", timezone: "Asia/Tokyo", start_utc: "2026-08-30T06:40:00Z",
        surface: "芝", distance_m: 2000, going: "良", field_size: 1 },
      runners: [{ horse_number: 1, horse_name: "アカツキ", win_odds: 2,
        raw_inverse_win_odds: 0.5, normalized_win_market_share: 1,
        place_odds_min: 1.2, place_odds_max: 1.5, place_break_even_hit_rate: null }],
      candidate_status: "期待値候補なし", candidate_reason: "市場基準から候補を生成しません。",
    };
    const snapshot = { id: 10, race_id: 1, observed_at: "2026-08-30T04:55:00Z", received_at: "2026-08-30T05:00:00Z",
      source: "ui", runners: [{ horse_number: 1, win_odds: 2.4, place_odds_min: 1.2, place_odds_max: 1.5 }] };
    const historicalSnapshot = { ...snapshot, id: 9, observed_at: "2026-08-30T04:45:00Z",
      runners: [{ horse_number: 1, win_odds: 2, place_odds_min: 1.2, place_odds_max: 1.5 }] };
    const prediction = { id: 20, race_id: 1, input_snapshot_id: 10, model_identifier: "market-baseline", model_version: "1.0",
      frozen_at: "2026-08-30T05:01:00Z", status: "active", invalidation_reason: null, replaces_prediction_id: null,
      official_evaluation_eligible: true, evaluation_exclusion_reason: null,
      runners: [{ horse_number: 1, raw_inverse_win_odds: 0.5, win_market_share: 1 }] };
    const invalidated = { ...prediction, status: "invalidated", invalidation_reason: "入力ミス",
      official_evaluation_eligible: false, evaluation_exclusion_reason: "理由付きで無効化された旧版" };
    const replacement = { ...prediction, id: 21, replaces_prediction_id: 20,
      official_evaluation_eligible: false, evaluation_exclusion_reason: "発走後に固定された事後訂正" };
    const jsonResponse = (value: object, status = 200) => new Response(JSON.stringify(value), {
      status, headers: { "Content-Type": "application/json" },
    });
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(healthResponse("ok"))
      .mockResolvedValueOnce(jsonResponse(analysis, 201))
      .mockResolvedValueOnce(jsonResponse([historicalSnapshot]))
      .mockResolvedValueOnce(jsonResponse([]))
      .mockResolvedValueOnce(jsonResponse(snapshot, 201))
      .mockResolvedValueOnce(jsonResponse(prediction, 201))
      .mockResolvedValueOnce(jsonResponse(replacement, 201))
      .mockResolvedValueOnce(jsonResponse([historicalSnapshot, snapshot]))
      .mockResolvedValueOnce(jsonResponse([invalidated, replacement]));
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);
    await screen.findByRole("article", { name: "APIの状態" });
    fireEvent.change(screen.getByLabelText("CSVファイル"), { target: { files: [new File(["csv"], "race.csv")] } });
    fireEvent.click(screen.getByRole("button", { name: "取り込んで分析" }));
    await screen.findByRole("heading", { name: "東京 11R" });
    expect(await screen.findByText("時点 #9 / 観測 2026-08-30T04:45:00Z")).toBeTruthy();

    fireEvent.change(screen.getByLabelText("1番 単勝オッズ"), { target: { value: "2.4" } });
    fireEvent.change(screen.getByLabelText("オッズ観測時刻"), { target: { value: "2026-08-30T13:55" } });
    fireEvent.click(screen.getByRole("button", { name: "オッズ時点を保存" }));
    expect(await screen.findByText("時点 #10 / 観測 2026-08-30T04:55:00Z")).toBeTruthy();
    expect(screen.getByText("1番 単勝2.4 / 複勝1.2–1.5")).toBeTruthy();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(5));
    const snapshotRequest = fetchMock.mock.calls[4][1] as RequestInit;
    const snapshotBody = JSON.parse(String(snapshotRequest.body)) as { runners: Array<{ win_odds: number }> };
    expect(snapshotBody.runners[0].win_odds).toBe(2.4);
    fireEvent.click(screen.getAllByRole("button", { name: "この時点の予測を固定" }).at(-1)!);

    expect(await screen.findByText("固定済み / 市場基準 1.0")).toBeTruthy();
    expect(screen.getByText("公式評価対象")).toBeTruthy();
    expect(screen.getByText(/入力時点 #10/)).toBeTruthy();
    expect(screen.getByText("1番 100.00%")).toBeTruthy();

    fireEvent.change(screen.getByLabelText("訂正理由"), { target: { value: "入力ミス" } });
    fireEvent.click(screen.getByRole("button", { name: "最新時点で訂正版を作成" }));

    expect(await screen.findByText("無効化済み / 市場基準 1.0")).toBeTruthy();
    expect(screen.getByText("理由: 入力ミス")).toBeTruthy();
    expect(screen.getAllByText("固定済み / 市場基準 1.0")).toHaveLength(1);
    expect(screen.getByText("発走後に固定された事後訂正")).toBeTruthy();
  });
});
