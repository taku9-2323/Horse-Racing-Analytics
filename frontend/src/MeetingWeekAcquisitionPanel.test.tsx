import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import MeetingWeekAcquisitionPanel from "./MeetingWeekAcquisitionPanel";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers(); });

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
  await waitFor(() => expect(screen.getByRole("button", { name: "開催週のレースを取得" }).hasAttribute("disabled")).toBe(false));
  fireEvent.click(screen.getByRole("button", { name: "開催週のレースを取得" }));

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
  expect(screen.getByRole("button", { name: "未取得・失敗分を再試行" })).toBeTruthy();
  expect(screen.getByText(/2026\/09\/02 12:01 JST/)).toBeTruthy();
});

const json = (body: object) => new Response(JSON.stringify(body), { status: 200 });

it.each(["running", "completed", "stopped"])("reconnects using GET only to %s after a progress failure", async (status) => {
  vi.useFakeTimers();
  const fetchMock = vi.fn().mockResolvedValueOnce(json(running))
    .mockRejectedValueOnce(new Error("offline"))
    .mockResolvedValueOnce(json({ ...running, status }))
    .mockResolvedValueOnce(json({ ...running, status: "completed" }));
  vi.stubGlobal("fetch", fetchMock);
  await act(async () => { render(<MeetingWeekAcquisitionPanel />); });
  await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
  expect(screen.getByText(/表示中の進捗は最後に確認できた内容/)).toBeTruthy();
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "進捗を再確認" })); });
  expect(screen.queryByRole("button", { name: "進捗を再確認" })).toBeNull();
  if (status === "running") {
    expect(screen.getByRole("button", { name: "開催週を取得中…" }).hasAttribute("disabled")).toBe(true);
  } else {
    expect(screen.getByRole("button", { name: status === "stopped" ? "未取得・失敗分を再試行" : "開催週のレースを更新" }).hasAttribute("disabled")).toBe(false);
  }
  await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
  expect(fetchMock).toHaveBeenCalledTimes(status === "running" ? 4 : 3);
  expect(fetchMock.mock.calls.every(([, options]) => options?.method !== "POST")).toBe(true);
});

it("aborts a polling GET and ignores its late response after unmount", async () => {
  vi.useFakeTimers();
  let finish!: (value: Response) => void;
  const pending = new Promise<Response>((resolve) => { finish = resolve; });
  const fetchMock = vi.fn().mockResolvedValueOnce(json(running)).mockReturnValueOnce(pending);
  const onSummaryChange = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
  const view = render(<MeetingWeekAcquisitionPanel onSummaryChange={onSummaryChange} />);
  await act(async () => {});
  await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
  expect(fetchMock).toHaveBeenCalledTimes(2);
  const signal = fetchMock.mock.calls[1][1].signal as AbortSignal;
  view.unmount();
  expect(signal.aborted).toBe(true);
  await act(async () => { finish(json(running)); });
  await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
  expect(fetchMock).toHaveBeenCalledTimes(2);
  expect(onSummaryChange).toHaveBeenCalledTimes(1);
});
