import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import PastAttentionWorkspace from "./PastAttentionWorkspace";


const jsonResponse = (value: object) => new Response(JSON.stringify(value), {
  status: 200, headers: { "Content-Type": "application/json" },
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("shows past attention horses, filters the current page, and pages by four meeting weeks", async () => {
  const firstPage = {
    page: 1, weeks_per_page: 4, total_week_count: 5, has_newer: false, has_older: true,
    weeks: [{ week_start: "2026-08-31", week_end: "2026-09-06", horses: [
      { race_id: 11, race_date: "2026-09-06", racecourse: "東京", race_number: 11,
        start_time: "15:45", horse_number: 1, horse_name: "アカツキ",
        pre_race_attention: true, post_start_attention: true,
        pre_race_snapshot_id: 101, pre_race_judgement_id: 201,
        post_start_snapshot_id: 102, post_start_judgement_id: 202,
        result_status: "確定", finish_position: 1, has_result_correction: true },
      { race_id: 12, race_date: "2026-09-05", racecourse: "中山", race_number: 8,
        start_time: "14:00", horse_number: 4, horse_name: "ヨイノツキ",
        pre_race_attention: false, post_start_attention: true,
        pre_race_snapshot_id: null, pre_race_judgement_id: null,
        post_start_snapshot_id: 103, post_start_judgement_id: 203,
        result_status: null, finish_position: null, has_result_correction: false },
    ] }],
  };
  const secondPage = {
    page: 2, weeks_per_page: 4, total_week_count: 5, has_newer: true, has_older: false,
    weeks: [{ week_start: "2026-08-03", week_end: "2026-08-09", horses: [
      { ...firstPage.weeks[0].horses[0], race_id: 3, race_date: "2026-08-09",
        horse_number: 7, horse_name: "ナツノホシ", has_result_correction: false },
    ] }],
  };
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    const url = String(input);
    if (url === "/api/past-attention?page=1") return Promise.resolve(jsonResponse(firstPage));
    if (url === "/api/past-attention?page=2") return Promise.resolve(jsonResponse(secondPage));
    return Promise.reject(new Error(`unexpected request: ${url}`));
  });
  vi.stubGlobal("fetch", fetchMock);

  render(<PastAttentionWorkspace />);

  const week = await screen.findByRole("region", { name: "2026年8月31日〜9月6日" });
  const winner = within(week).getByRole("button", { name: /アカツキ/ });
  expect(winner.textContent).toContain("1着");
  expect(winner.textContent).toContain("事前判定");
  expect(winner.textContent).toContain("事後判定");
  expect(winner.textContent).toContain("訂正あり");

  fireEvent.click(screen.getByRole("button", { name: "結果未取得" }));
  expect(screen.queryByRole("button", { name: /アカツキ/ })).toBeNull();
  expect(screen.getByRole("button", { name: /ヨイノツキ/ }).textContent).toContain("結果未取得");

  fireEvent.click(screen.getByRole("button", { name: "すべて" }));
  fireEvent.change(screen.getByLabelText("開催日"), { target: { value: "2026-09-06" } });
  fireEvent.click(screen.getByRole("button", { name: "古い4開催週" }));
  expect(await screen.findByRole("button", { name: /ナツノホシ/ })).toBeTruthy();
  expect((screen.getByLabelText("開催日") as HTMLSelectElement).value).toBe("all");
  expect(screen.getByRole("button", { name: "新しい4開催週" })).toBeTruthy();
});

it("filters the displayed four weeks by date, racecourse, result, and judgement timing", async () => {
  const horse = {
    race_id: 11, race_date: "2026-09-06", racecourse: "東京", race_number: 11,
    start_time: "15:45", horse_number: 1, horse_name: "アカツキ",
    pre_race_attention: true, post_start_attention: false,
    pre_race_snapshot_id: 101, pre_race_judgement_id: 201,
    post_start_snapshot_id: null, post_start_judgement_id: null,
    result_status: "確定", finish_position: 1, has_result_correction: false,
  };
  const page = {
    page: 1, weeks_per_page: 4, total_week_count: 1, has_newer: false, has_older: false,
    weeks: [{ week_start: "2026-08-31", week_end: "2026-09-06", horses: [
      horse,
      { ...horse, race_id: 12, race_date: "2026-09-05", racecourse: "中山",
        horse_number: 2, horse_name: "ヨイノツキ", finish_position: 4,
        pre_race_attention: false, post_start_attention: true },
    ] }],
  };
  vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(jsonResponse(page))));
  render(<PastAttentionWorkspace />);
  await screen.findByRole("button", { name: /アカツキ/ });

  fireEvent.change(screen.getByLabelText("開催日"), { target: { value: "2026-09-05" } });
  expect(screen.queryByRole("button", { name: /アカツキ/ })).toBeNull();
  expect(screen.getByRole("button", { name: /ヨイノツキ/ })).toBeTruthy();

  fireEvent.change(screen.getByLabelText("開催日"), { target: { value: "all" } });
  fireEvent.change(screen.getByLabelText("競馬場"), { target: { value: "東京" } });
  fireEvent.click(screen.getByRole("button", { name: "事前判定あり" }));
  fireEvent.click(screen.getByRole("button", { name: "1着" }));
  expect(screen.getByRole("button", { name: /アカツキ/ })).toBeTruthy();
  expect(screen.queryByRole("button", { name: /ヨイノツキ/ })).toBeNull();
});

it("opens the fixed decision for the selected horse and restores the list on back", async () => {
  const horse = {
    race_id: 11, race_date: "2026-09-06", racecourse: "東京", race_number: 11,
    start_time: "15:45", horse_number: 3, horse_name: "アカツキ",
    pre_race_attention: true, post_start_attention: true,
    pre_race_snapshot_id: 101, pre_race_judgement_id: 201,
    post_start_snapshot_id: 102, post_start_judgement_id: 202,
    result_status: "確定", finish_position: 1, has_result_correction: false,
  };
  const page = {
    page: 1, weeks_per_page: 4, total_week_count: 1, has_newer: false, has_older: false,
    weeks: [{ week_start: "2026-08-31", week_end: "2026-09-06", horses: [horse] }],
  };
  const detail = {
    race_id: 11, race: { racecourse: "東京", race_date: "2026-09-06", race_number: 11,
      start_time: "15:45", surface: "芝", distance_m: 2000, going: "良", field_size: 10 },
    snapshot_id: 101, judgement_id: 201, rule_version_id: 1,
    observed_at: "2026-09-06T06:30:00Z", received_at: "2026-09-06T06:31:00Z",
    judgement_as_of: "2026-09-06T06:30:00Z", judgement_frozen_at: "2026-09-06T06:32:00Z",
    attention_horse_count: 1, judged_runner_count: 10,
    runners: [{ horse_number: 3, horse_name: "アカツキ", win_odds: 3.2,
      place_odds_min: 1.4, place_odds_max: 1.8, market_rank: 2,
      normalized_win_market_share: .2, rule_judgement: "注目",
      rule_reason: "注目ルールに該当", missing_reasons: [] }],
    disclaimer: "期待値、回収率、購入推奨、利益優位性を示しません。",
  };
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    const url = String(input);
    if (url === "/api/past-attention?page=1") return Promise.resolve(jsonResponse(page));
    if (url === "/api/races/11/weekly-decision-view?snapshot_id=101&judgement_id=201") {
      return Promise.resolve(jsonResponse(detail));
    }
    return Promise.reject(new Error(`unexpected request: ${url}`));
  });
  vi.stubGlobal("fetch", fetchMock);
  const onNavigate = vi.fn();
  render(<PastAttentionWorkspace onNavigate={onNavigate} />);

  fireEvent.click(await screen.findByRole("button", { name: /アカツキ/ }));
  expect(await screen.findByRole("heading", { name: "東京 11R" })).toBeTruthy();
  expect(screen.getByRole("article", { name: "アカツキ 選択中" })).toBeTruthy();
  expect(screen.getByText("一覧で選択")).toBeTruthy();
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/races/11/weekly-decision-view?snapshot_id=101&judgement_id=201",
  );
  fireEvent.click(screen.getByRole("button", { name: "設定・バックアップへ" }));
  expect(onNavigate).toHaveBeenCalledWith("settings");

  fireEvent.click(screen.getByRole("button", { name: "過去レースへ戻る" }));
  expect(screen.getByRole("button", { name: /アカツキ/ })).toBeTruthy();
});

it("bulk-acquires missing results for the displayed meeting weeks and refreshes the rows", async () => {
  const missingHorse = {
    race_id: 11, race_date: "2026-09-06", racecourse: "東京", race_number: 11,
    start_time: "15:45", horse_number: 3, horse_name: "アカツキ",
    pre_race_attention: true, post_start_attention: false,
    pre_race_snapshot_id: 101, pre_race_judgement_id: 201,
    post_start_snapshot_id: null, post_start_judgement_id: null,
    result_status: null, finish_position: null, has_result_correction: false,
  };
  const page = (horse: object) => ({
    page: 1, weeks_per_page: 4, total_week_count: 1, has_newer: false, has_older: false,
    weeks: [{ week_start: "2026-08-31", week_end: "2026-09-06", horses: [horse] }],
  });
  const running = {
    run_id: 7, page: 1, status: "running", started_at: "2026-09-07T03:00:00Z",
    completed_at: null, target_count: 1, processed_count: 0, succeeded_count: 0, missing_count: 0,
    failed_count: 0, stop_reason: null,
    targets: [{ race_id: 11, race_date: "2026-09-06", racecourse: "東京", race_number: 11,
      status: "running", error_code: null, error_message: null }],
  };
  const completed = { ...running, status: "completed", completed_at: "2026-09-07T03:01:00Z",
    processed_count: 1, succeeded_count: 1,
    targets: [{ ...running.targets[0], status: "succeeded" }] };
  let pageLoads = 0;
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url === "/api/past-attention?page=1") {
      pageLoads += 1;
      const horse = pageLoads === 1 ? missingHorse : {
        ...missingHorse, result_status: "確定", finish_position: 1,
      };
      return Promise.resolve(jsonResponse(page(horse)));
    }
    if (url === "/api/past-attention/result-runs?page=1" && init?.method === "POST") {
      return Promise.resolve(new Response(JSON.stringify(running), { status: 202 }));
    }
    if (url === "/api/past-attention/result-runs/7") return Promise.resolve(jsonResponse(completed));
    return Promise.reject(new Error(`unexpected request: ${url}`));
  }));
  render(<PastAttentionWorkspace />);
  await screen.findByRole("button", { name: /アカツキ.*結果未取得/ });

  fireEvent.click(screen.getByRole("button", { name: "表示中の未取得結果を一括取得" }));
  expect(await screen.findByText("0 / 1 レース処理済み")).toBeTruthy();
  await waitFor(() => expect(screen.getByRole("button", { name: /アカツキ.*1着/ })).toBeTruthy(), {
    timeout: 2000,
  });
  expect(screen.getByText("1件取得 / 0件未取得 / 0件失敗")).toBeTruthy();
});
