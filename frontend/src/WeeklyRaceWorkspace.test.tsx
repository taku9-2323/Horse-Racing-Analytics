import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import WeeklyRaceWorkspace, { selectMeetingDate } from "./WeeklyRaceWorkspace";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers(); });
beforeEach(() => { vi.stubGlobal("scrollTo", vi.fn()); vi.useFakeTimers({ toFake: ["Date"] }); vi.setSystemTime(new Date("2026-09-05T00:00:00Z")); });

const race = (overrides: Record<string, unknown>) => ({
  race_date: "2026-09-05", racecourse: "東京", meeting_number: 4, meeting_day: 1,
  race_number: 1, race_name: "2歳未勝利", start_time: "10:00", surface: "芝",
  distance_m: 1600, condition_text: "2歳", state: "ready", race_id: 1,
  card_id: 1, snapshot_id: 11, judgement_id: 21, rule_version_id: 1,
  judgement_as_of: "2026-09-05T00:50:00Z", judgement_frozen_at: "2026-09-05T00:55:00Z",
  odds_observed_at: "2026-09-05T00:50:00Z", attention_horse_count: 0,
  judged_runner_count: 10, attention_ratio: 0, attention_level: "none",
  display_state: "no_attention", has_started: false, error_code: null,
  updated_at: "2026-09-05T01:00:00Z", ...overrides,
});

const summary = {
  run_id: 1, week_start: "2026-08-31", week_end: "2026-09-06",
  started_at: "2026-09-05T01:00:00Z", completed_at: "2026-09-05T01:01:00Z",
  status: "completed", target_count: 5, processed_count: 5, ready_count: 3,
  waiting_count: 1, failed_count: 1, stop_reason: null, last_target: null,
  last_updated_at: "2026-09-05T01:01:00Z",
  races: [
    race({ race_number: 3, start_time: "11:00", race_id: 3, snapshot_id: 13, judgement_id: 23,
      attention_horse_count: 2, judged_runner_count: 4, attention_ratio: .5, attention_level: "high", display_state: "attention" }),
    race({ race_number: 1, start_time: "10:00" }),
    race({ race_number: 2, start_time: "10:30", race_id: 2, snapshot_id: 12, judgement_id: 22,
      attention_horse_count: 1, attention_ratio: .1, attention_level: "low", display_state: "attention" }),
    race({ race_date: "2026-09-06", racecourse: "中山", race_number: 4, start_time: "11:30",
      race_id: null, snapshot_id: null, judgement_id: null, rule_version_id: null,
      judgement_as_of: null, judgement_frozen_at: null, odds_observed_at: null,
      attention_horse_count: null, judged_runner_count: null, attention_ratio: null,
      attention_level: null, state: "odds_waiting", display_state: "odds_waiting" }),
    race({ race_number: 5, start_time: "12:00", race_id: 5, snapshot_id: null,
      judgement_id: null, rule_version_id: null, judgement_as_of: null,
      judgement_frozen_at: null, odds_observed_at: null, attention_horse_count: null,
      judged_runner_count: null, attention_ratio: null, attention_level: null,
      state: "stopped", display_state: "acquisition_failed", error_code: "network_error" }),
  ],
};

const json = (value: object) => new Response(JSON.stringify(value), {
  status: 200, headers: { "Content-Type": "application/json" },
});

it("filters the chronological row list and communicates attention without color alone", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json(summary)));
  render(<WeeklyRaceWorkspace />);

  const list = await screen.findByRole("list", { name: "レース一覧" });
  const rows = within(list).getAllByRole("button");
  expect(rows.map((row) => row.textContent?.match(/\d+R/)?.[0])).toEqual(["1R", "2R", "3R", "5R"]);
  expect(within(rows[1]).getByText("注目 1頭 / 10頭")).toBeTruthy();
  expect(within(rows[2]).getByText("注目 2頭 / 4頭")).toBeTruthy();
  expect(within(rows[2]).getByText("芝1600m / 2歳")).toBeTruthy();
  expect(within(rows[2]).getByText("判定済み")).toBeTruthy();

  fireEvent.click(screen.getByRole("button", { name: "注目あり" }));
  expect(within(list).getAllByRole("button")).toHaveLength(2);
  fireEvent.click(screen.getByRole("tab", { name: /9月6日/ }));
  expect(screen.getByText("絞り込み条件に一致するレースはありません。")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "データ待ち・不足" }));
  expect(screen.getByText("オッズ待ち")).toBeTruthy();
});

it("opens the fixed decision detail and restores list selections on back", async () => {
  const detail = {
    race_id: 2, race: { organizer: "JRA", country: "JP", racecourse: "東京",
      race_date: "2026-09-05", race_number: 2, start_time: "10:30", timezone: "Asia/Tokyo",
      start_utc: "2026-09-05T01:30:00Z", surface: "芝", distance_m: 1600, going: "良", field_size: 10 },
    snapshot_id: 12, judgement_id: 22, rule_version_id: 1,
    observed_at: "2026-09-05T00:50:00Z", received_at: "2026-09-05T00:51:00Z",
    judgement_as_of: "2026-09-05T00:50:00Z", judgement_frozen_at: "2026-09-05T00:55:00Z",
    attention_horse_count: 1, judged_runner_count: 10,
    runners: [{ horse_number: 1, horse_name: "アカツキ", win_odds: 2,
      place_odds_min: 1.2, place_odds_max: 1.5, market_rank: 1,
      normalized_win_market_share: .31, rule_judgement: "注目",
      rule_reason: "市場順位が2位以内 / 単勝オッズが10.0以下", missing_reasons: [] }],
    disclaimer: "注目段階はルール該当率です。期待値、回収率、購入推奨、利益優位性を示しません。",
  };
  vi.stubGlobal("fetch", vi.fn((input: string | URL | Request) => {
    const url = String(input);
    if (url === "/api/acquisition/jra/meeting-weeks/current") return Promise.resolve(json(summary));
    if (url.includes("/weekly-decision-view")) return Promise.resolve(json(detail));
    return Promise.reject(new Error(`Unexpected request: ${url}`));
  }));
  render(<WeeklyRaceWorkspace />);

  fireEvent.click(await screen.findByRole("button", { name: "注目あり" }));
  fireEvent.click(screen.getByRole("button", { name: /東京 2R/ }));
  expect(await screen.findByRole("heading", { name: "東京 2R" })).toBeTruthy();
  expect(screen.getByText("市場順位が2位以内 / 単勝オッズが10.0以下")).toBeTruthy();
  expect(screen.getByText(/期待値、回収率、購入推奨/)).toBeTruthy();
  expect(screen.getByRole("heading", { name: "実購入・結果・収支" })).toBeTruthy();
  expect(screen.getByText("データ状態: 判定済み")).toBeTruthy();
  expect(screen.getByRole("button", { name: "設定・バックアップへ" })).toBeTruthy();

  fireEvent.click(screen.getByRole("button", { name: "レース一覧へ戻る" }));
  expect(screen.getByRole("button", { name: "注目あり", pressed: true })).toBeTruthy();
  expect(within(screen.getByRole("list", { name: "レース一覧" })).getAllByRole("button")).toHaveLength(2);
});

it("keeps a completed attention race in the attention filter when completed races are shown", async () => {
  const completedAttention = {
    ...summary,
    races: [race({ display_state: "post_start", has_started: true,
      attention_horse_count: 1, judged_runner_count: 10, attention_ratio: .1,
      attention_level: "low" })],
  };
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json(completedAttention)));
  render(<WeeklyRaceWorkspace />);

  await screen.findByRole("heading", { name: "レース一覧" });
  fireEvent.click(screen.getByLabelText("発走後も表示"));
  fireEvent.click(screen.getByRole("button", { name: "注目あり" }));

  const row = screen.getByRole("button", { name: /東京 1R/ });
  expect(within(row).getByText("注目馬あり")).toBeTruthy();
  expect(within(row).getByText("発走後")).toBeTruthy();
});

it("selects Sunday or a supplied holiday, then the final date, preserving valid choices", () => {
  const races = summary.races as Parameters<typeof selectMeetingDate>[0];
  expect(selectMeetingDate(races, null, new Date("2026-09-05T16:00:00Z"))).toBe("2026-09-06");
  const holiday = [...races, { ...races[0], race_date: "2026-09-07" }];
  expect(selectMeetingDate(holiday, null, new Date("2026-09-07T00:00:00Z"))).toBe("2026-09-07");
  expect(selectMeetingDate(holiday, null, new Date("2026-09-08T00:00:00Z"))).toBe("2026-09-07");
  expect(selectMeetingDate(races, "2026-09-05", new Date("2026-09-06T00:00:00Z"))).toBe("2026-09-05");
  expect(selectMeetingDate(holiday, null, new Date("2026-09-04T00:00:00Z"))).toBe("2026-09-05");
});

it("explains hidden completed races and restores them with a direct action", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json({ ...summary, races: [race({ has_started: true })] })));
  render(<WeeklyRaceWorkspace />);
  await screen.findByText("選択日のレースはすべて発走済みのため非表示です。");
  fireEvent.click(screen.getByRole("button", { name: "発走済みレースを表示" }));
  expect(screen.getByRole("list", { name: "レース一覧" })).toBeTruthy();
});

it("explains unacquired data and clears mismatched filters", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json({ ...summary, races: [] })));
  const view = render(<WeeklyRaceWorkspace />);
  await screen.findByText(/開催週のレースは未取得です/);
  view.unmount();
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json(summary)));
  render(<WeeklyRaceWorkspace />);
  fireEvent.click(await screen.findByRole("button", { name: "注目あり" }));
  fireEvent.click(screen.getByRole("tab", { name: /9月6日/ }));
  fireEvent.click(screen.getByRole("button", { name: "絞り込みを解除して全レースを表示" }));
  expect(screen.getByRole("list", { name: "レース一覧" })).toBeTruthy();
});
