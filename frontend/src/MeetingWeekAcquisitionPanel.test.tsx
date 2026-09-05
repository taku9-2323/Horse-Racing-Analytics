import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import MeetingWeekAcquisitionPanel from "./MeetingWeekAcquisitionPanel";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const running = {
  run_id: 1, week_start: "2026-08-31", week_end: "2026-09-06",
  started_at: "2026-09-02T03:00:00Z", completed_at: null, status: "running",
  target_count: 2, processed_count: 1, ready_count: 1, waiting_count: 1,
  failed_count: 0, stop_reason: null, last_target: "https://www.jra.go.jp/",
  last_updated_at: "2026-09-02T03:01:00Z", races: [],
};

it("starts the current meeting week and shows persisted completion progress", async () => {
  const completed = {
    ...running, status: "completed", completed_at: "2026-09-02T03:02:00Z",
    processed_count: 2, ready_count: 1,
    races: [{
      race_date: "2026-09-05", racecourse: "中山", meeting_number: 4,
      meeting_day: 1, race_number: 1, race_name: "2歳未勝利", start_time: "09:50",
      surface: "ダート", distance_m: 1200, condition_text: "2歳未勝利",
      state: "ready", race_id: 7, card_id: 5, snapshot_id: 8, judgement_id: 9,
      error_code: null, updated_at: "2026-09-02T03:02:00Z",
    }],
  };
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({ detail: { code: "meeting_week_not_acquired" } }), {
      status: 404, headers: { "Content-Type": "application/json" },
    }))
    .mockResolvedValueOnce(new Response(JSON.stringify(running), {
      status: 202, headers: { "Content-Type": "application/json" },
    }))
    .mockResolvedValueOnce(new Response(JSON.stringify(completed), {
      status: 200, headers: { "Content-Type": "application/json" },
    }));
  vi.stubGlobal("fetch", fetchMock);

  render(<MeetingWeekAcquisitionPanel />);
  fireEvent.click(await screen.findByRole("button", { name: "開催週のレースを取得" }));

  expect(await screen.findByText("2 / 2 レース処理済み")).toBeTruthy();
  expect(screen.getByText("中山 1R")).toBeTruthy();
  expect(screen.getByText("判定まで準備済み")).toBeTruthy();
  expect(fetchMock).toHaveBeenNthCalledWith(2, "/api/acquisition/jra/meeting-weeks/current/runs", {
    method: "POST",
  });
});

it("prevents a duplicate operation while acquisition is running", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(running), {
    status: 200, headers: { "Content-Type": "application/json" },
  })));

  render(<MeetingWeekAcquisitionPanel />);

  const button = await screen.findByRole("button", { name: "開催週を取得中…" });
  expect(button.hasAttribute("disabled")).toBe(true);
  expect(screen.getByText("1 / 2 レース処理済み")).toBeTruthy();
});

it("keeps partial progress visible and points to CSV when JRA acquisition stops", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
    ...running, status: "stopped", completed_at: "2026-09-02T03:02:00Z",
    target_count: 1, processed_count: 0, ready_count: 0, waiting_count: 1,
    failed_count: 1, stop_reason: "JRAからの取得を停止しました。CSV取込を使用してください。",
    races: [{
      race_date: "2026-09-05", racecourse: "中山", meeting_number: 4,
      meeting_day: 1, race_number: 1, race_name: "2歳未勝利", start_time: "09:50",
      surface: "ダート", distance_m: 1200, condition_text: "2歳未勝利",
      state: "entries_waiting", race_id: null, card_id: null, snapshot_id: null,
      judgement_id: null, error_code: null, updated_at: "2026-09-02T03:01:00Z",
    }],
  }), { status: 200, headers: { "Content-Type": "application/json" } })));

  render(<MeetingWeekAcquisitionPanel />);

  const alert = await screen.findByRole("alert");
  expect(within(alert).getByText(/CSV取込を使用してください/)).toBeTruthy();
  expect(screen.getByText("出馬表待ち")).toBeTruthy();
  expect(screen.getByRole("button", { name: "開催週のレースを更新" })).toBeTruthy();
});
