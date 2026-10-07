import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import App from "./App";
import EvaluationPanel from "./EvaluationPanel";
import { ViewStateProvider } from "./ViewState";
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });
const route = (value: object) => `/?view=${encodeURIComponent(JSON.stringify(value))}`;
const restore = (value: object) => { window.history.replaceState(null, "", route(value)); window.dispatchEvent(new PopStateEvent("popstate")); };
const race = { organizer: "JRA", country: "JP", racecourse: "東京", race_date: "2026-09-05", race_number: 1, start_time: "10:00", timezone: "Asia/Tokyo", start_utc: "2026-09-05T01:00:00Z", surface: "芝", distance_m: 1600, going: "良", field_size: 0 };
const analysis = (id: number) => ({ race_id: id, race: { ...race, race_number: id }, runners: [], candidate_status: "none", candidate_reason: "候補なし" });
const detail = { ...analysis(1), snapshot_id: 11, judgement_id: 21, rule_version_id: 1, observed_at: null, received_at: "2026-09-05T01:00:00Z", judgement_as_of: "2026-09-05T01:00:00Z", judgement_frozen_at: "2026-09-05T01:00:00Z", attention_horse_count: 0, judged_runner_count: 0, disclaimer: "検証用" };
const filters = { modelOption: "", tagOption: "", racecourse: "", betType: "", oddsMin: "10", oddsMax: "", popularityMin: "", popularityMax: "", frozenFrom: "", frozenTo: "" };
const report = { filters: {}, filter_options: { models: [], tags: [], racecourses: [], bet_types: [] }, calibration_status: "no_groups", calibration_reason: "一致するrunがありません。", calibration_groups: [], calibration: null, returns: { candidate: { stake_yen: 0, payout_yen: 0, refund_yen: 0, profit_yen: 0, return_rate: null }, discretionary: { stake_yen: 0, payout_yen: 0, refund_yen: 0, profit_yen: 0, return_rate: null } } };
beforeEach(() => { window.history.replaceState(null, "", "/"); vi.stubGlobal("scrollTo", vi.fn()); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
function mockApi(extra?: (url: string) => Promise<Response> | undefined) {
  const fetchMock = vi.fn((input: RequestInfo | URL, _init?: RequestInit) => {
    const url = String(input);
    const override = extra?.(url);
    if (override) return override;
    if (url === "/api/health") return Promise.resolve(json({ service: "test", status: "ok", api: { status: "ok" }, database: { status: "ok", engine: "sqlite" } }));
    if (url === "/api/races") return Promise.resolve(json([analysis(1), analysis(2)]));
    if (url === "/api/acquisition/jra/meeting-weeks/current") return Promise.resolve(json({}, 404));
    if (url.includes("weekly-decision-view")) return Promise.resolve(json(detail));
    if (url === "/api/races/1") return Promise.resolve(json(analysis(1)));
    if (url === "/api/races/2") return Promise.resolve(json(analysis(2)));
    if (url.startsWith("/api/evaluation")) return Promise.resolve(json(report));
    if (url.startsWith("/api/rule-performance")) return Promise.resolve(json({ filters: {}, groups: [], disclaimer: "保存済み判定の記述集計です。" }));
    return Promise.resolve(json([]));
  });
  vi.stubGlobal("fetch", fetchMock); return fetchMock;
}
it("restores a fixed detail, carries its race to import, and reloads that URL without POST", async () => {
  window.history.replaceState(null, "", route({ area: "weekly", weeklyDetail: "1,11,21" }));
  const fetchMock = mockApi();
  const view = render(<App />);
  await screen.findByRole("heading", { name: "東京 1R" });
  fireEvent.click(screen.getByRole("button", { name: "データ取込・訂正へ" }));
  await screen.findByRole("heading", { name: "1レース市場分析" });
  await waitFor(() => expect(new URLSearchParams(window.location.search).get("view")).toContain('"importRace":1'));
  view.unmount();
  render(<App />);
  await screen.findByRole("heading", { name: "東京 1R" });
  expect(fetchMock.mock.calls.every(([, init]) => init?.method !== "POST")).toBe(true);
});
it("keeps applied evaluation conditions across navigation, browser back, forward and reload", async () => {
  window.history.replaceState(null, "", route({ area: "evaluation", evaluation: JSON.stringify(filters) }));
  mockApi();
  const view = render(<App />);
  await screen.findByLabelText("適用済み条件");
  fireEvent.click(screen.getByRole("button", { name: "設定・バックアップ" }));
  await act(async () => {});
  window.history.back();
  await waitFor(() => expect(screen.getByLabelText("適用済み条件").textContent).toContain("オッズ下限: 10"));
  window.history.forward();
  await screen.findByRole("heading", { name: "システム状態" });
  fireEvent.click(screen.getByRole("button", { name: "成績・検証" }));
  await screen.findByLabelText("適用済み条件");
  view.unmount(); render(<App />);
  expect((await screen.findByLabelText("適用済み条件")).textContent).toContain("オッズ下限: 10");
});
it("returns to an enabled initial evaluation after history leaves an in-flight GET", async () => {
  let finish!: (value: Response) => void;
  window.history.replaceState(null, "", route({ evaluation: JSON.stringify(filters) }));
  vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>((resolve) => { finish = resolve; })));
  render(<ViewStateProvider><EvaluationPanel /></ViewStateProvider>);
  await act(async () => { restore({}); });
  expect(screen.getByRole("button", { name: "成績を表示" }).hasAttribute("disabled")).toBe(false);
  await act(async () => { finish(json(report)); });
  expect(screen.queryByLabelText("適用済み条件")).toBeNull();
});
it("ignores an older same-race reload after selecting another race", async () => {
  window.history.replaceState(null, "", route({ area: "import", importRace: 1 }));
  let calls = 0;
  let finish!: (value: Response) => void;
  mockApi((url) => url === "/api/races/1" && ++calls === 2 ? new Promise<Response>((resolve) => { finish = resolve; }) : undefined);
  render(<App />);
  await screen.findByRole("heading", { name: "東京 1R" });
  fireEvent.click(screen.getByRole("button", { name: /2026-09-05 東京 1R/ }));
  fireEvent.click(screen.getByRole("button", { name: /2026-09-05 東京 2R/ }));
  await screen.findByRole("heading", { name: "東京 2R" });
  await act(async () => { finish(json(analysis(1))); });
  expect(screen.getByRole("heading", { name: "東京 2R" })).toBeTruthy();
  expect(new URLSearchParams(window.location.search).get("view")).toContain('"importRace":2');
});
it("explains a missing fixed ID and returns to the list", async () => {
  window.history.replaceState(null, "", route({ area: "weekly", weeklyDetail: "404,11,21" }));
  mockApi((url) => url.includes("weekly-decision-view") ? Promise.resolve(json({}, 404)) : undefined);
  render(<App />);
  await screen.findByText(/指定されたレース・固定時点が見つからない/);
  fireEvent.click(screen.getByRole("button", { name: "一覧へ戻る" }));
  await waitFor(() => expect(screen.queryByText(/指定されたレース・固定時点が見つからない/)).toBeNull());
});

it("preserves weekly URL selections and uses the matching detail status on history restoration", async () => {
  const weeklyRaces = [1, 2].map((id) => ({ race_date: "2026-09-05", racecourse: "東京", race_number: id, race_name: `race${id}`, start_time: "10:00", surface: "芝", distance_m: 1600, condition_text: "", state: "ready", display_state: id === 1 ? "no_attention" : "post_start", race_id: id, snapshot_id: id * 11, judgement_id: id * 21, has_started: id === 2, attention_horse_count: 0, judged_runner_count: 0, attention_level: "none" }));
  const weekly = { run_id: 1, week_start: "2026-08-31", week_end: "2026-09-06", status: "completed", last_updated_at: "2026-09-05T01:00:00Z", processed_count: 2, target_count: 2, ready_count: 2, waiting_count: 0, failed_count: 0, races: weeklyRaces };
  window.history.replaceState(null, "", route({ area: "weekly", weeklyDate: "2026-09-05", weeklyCompleted: true }));
  mockApi((url) => {
    if (url === "/api/acquisition/jra/meeting-weeks/current") return Promise.resolve(json(weekly));
    if (url.includes("/races/2/weekly-decision-view")) return Promise.resolve(json({ ...detail, ...analysis(2), snapshot_id: 22, judgement_id: 42 }));
  });
  render(<App />);
  fireEvent.click(await screen.findByRole("button", { name: /東京 1R race1/ }));
  await screen.findByText("データ状態: 判定済み");
  fireEvent.click(screen.getByRole("button", { name: "レース一覧へ戻る" }));
  fireEvent.click(await screen.findByRole("button", { name: /東京 2R race2/ }));
  await screen.findByText("データ状態: 判定済み / 発走後");
  await act(async () => { restore({ area: "weekly", weeklyDate: "2026-09-05", weeklyCompleted: true, weeklyDetail: "1,11,21" }); });
  await screen.findByRole("heading", { name: "東京 1R" });
  expect(screen.getByText("データ状態: 判定済み")).toBeTruthy();
  expect(screen.queryByText("データ状態: 判定済み / 発走後")).toBeNull();
});
