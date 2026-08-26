import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
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
