import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import RaceDecisionDetail, { type DecisionRunner, type DecisionView } from "./RaceDecisionDetail";

type FakeOpinion = { id: number; race_id: number; horse_number: number; mark: string | null;
  memo: string | null; skip_reason: string | null; recorded_at: string };
type FakeRunnerRecord = { horse_number: number; current_revision: FakeOpinion | null;
  revisions: FakeOpinion[]; reflections: Array<{ id: number; race_id: number; horse_number: number;
    reflection: string; recorded_at: string }> };
const blankRecords = (horseNumbers: number[]): { race_id: number; result_saved: boolean; runners: FakeRunnerRecord[] } => ({
  race_id: 7, result_saved: false, runners: horseNumbers.map((horse_number) => ({
    horse_number, current_revision: null, revisions: [], reflections: [],
  })) });
const jsonResponse = (payload: unknown, ok = true, status = ok ? 200 : 400) => ({
  ok, status, json: async () => payload,
}) as Response;
let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  fetchMock = vi.fn().mockResolvedValue(jsonResponse(blankRecords([1, 2, 3, 4, 5])));
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const runner = (overrides: Partial<DecisionRunner> & Pick<DecisionRunner, "horse_number" | "horse_name">): DecisionRunner => ({
  gate: 1, age: 4, sex: "牡", assigned_weight: 57, status: "出走",
  win_odds: 10, place_odds_min: 2, place_odds_max: 3, market_rank: 3,
  normalized_win_market_share: 0.1, rule_judgement: "見送り", rule_reason: "保存済み判定理由",
  rule_conditions: [], missing_reasons: [],
  ...overrides,
});

const detail = (overrides: Partial<DecisionView> = {}): DecisionView => ({
  race_id: 7,
  race: { racecourse: "東京", race_date: "2026-09-05", race_number: 11, start_time: "15:40",
    surface: "芝", distance_m: 2000, going: "良", field_size: 5 },
  snapshot_id: 41, judgement_id: 51, rule_version_id: 3,
  observed_at: null, received_at: "2026-09-05T05:01:00Z",
  judgement_as_of: "2026-09-05T05:00:00Z", judgement_frozen_at: "2026-09-05T05:05:00Z",
  attention_horse_count: 1, judged_runner_count: 4, attention_level: "low",
  runners: [
    runner({ horse_number: 1, horse_name: "アカツキ", market_rank: 3 }),
    runner({ horse_number: 2, horse_name: "ハルカゼ", market_rank: 2 }),
    runner({ horse_number: 3, horse_name: "ミナモ", market_rank: 2, status: "取消", rule_judgement: "判定不能" }),
    runner({ horse_number: 4, horse_name: "観測不明", gate: null, age: null, sex: null,
      assigned_weight: null, status: "除外", win_odds: null, place_odds_min: 2, place_odds_max: null,
      market_rank: null, rule_judgement: "判定不能", rule_reason: "保存値不明" }),
    runner({ horse_number: 5, horse_name: "ホシカゲ", market_rank: 1, rule_judgement: "注目" }),
  ],
  disclaimer: "固定判定と市場情報を表示します。購入推奨や独立確率ではありません。",
  ...overrides,
});

it("compares selected runners in number or saved-snapshot market-rank order", () => {
  const view = render(<RaceDecisionDetail detail={detail()} dataState="判定済み"
    backLabel="一覧へ戻る" onBack={() => {}} selectedHorseNumber={1} />);

  const selectedFromPastList = screen.getByRole("checkbox", { name: "1番 アカツキを比較する" });
  expect((selectedFromPastList as HTMLInputElement).checked).toBe(false);
  expect(screen.getByText("一覧で選択")).toBeTruthy();
  expect(screen.getByText("比較する馬を選択してください")).toBeTruthy();
  expect(screen.getByText(/レースID 7.*snapshot 41.*判定 51/)).toBeTruthy();
  expect(screen.getByText("観測時刻不明")).toBeTruthy();
  expect(screen.getByText("受信時刻")).toBeTruthy();

  for (const horse of [5, 4, 3, 2, 1]) {
    fireEvent.click(screen.getByRole("checkbox", { name: new RegExp(`^${horse}番`) }));
  }

  const table = screen.getByRole("table", { name: "選択馬の比較" });
  const headers = () => within(table).getAllByRole("columnheader").map((header) => header.textContent?.trim());
  expect(headers()).toEqual(["項目", "1番 アカツキ", "2番 ハルカゼ", "3番 ミナモ", "4番 観測不明", "5番 ホシカゲ"]);
  const mobile = document.querySelector(".comparison-mobile-list");
  expect(mobile?.textContent).toContain("枠");
  expect(mobile?.textContent).toContain("4歳");
  expect(mobile?.textContent).toContain("牡");
  expect(mobile?.textContent).toContain("57.0kg");
  expect(mobile?.textContent).toContain("10.0");

  fireEvent.change(screen.getByLabelText("比較の順序"), { target: { value: "popularity" } });
  expect(headers()).toEqual(["項目", "5番 ホシカゲ", "2番 ハルカゼ", "3番 ミナモ", "1番 アカツキ", "4番 観測不明"]);
  expect(screen.getAllByRole("checkbox").map((checkbox) => checkbox.getAttribute("aria-label")))
    .toEqual(["5番 ホシカゲを比較する", "2番 ハルカゼを比較する", "3番 ミナモを比較する",
      "1番 アカツキを比較する", "4番 観測不明を比較する"]);
  expect(mobile?.textContent).toContain("5番 ホシカゲ");
  expect(mobile?.textContent).toContain("3番 ミナモ");
  expect(mobile?.textContent).toContain("不明");
  expect(mobile?.textContent).toContain("取消");
  expect(mobile?.textContent).toContain("除外");
});

it.each([
  ["race ID", detail({ race_id: 8 })],
  ["snapshot ID", detail({ snapshot_id: 42 })],
  ["judgement ID", detail({ judgement_id: 52 })],
])("clears comparison selection when the %s changes", (_scope, changedDetail) => {
  const view = render(<RaceDecisionDetail detail={detail()} dataState="判定済み"
    backLabel="一覧へ戻る" onBack={() => {}} />);
  fireEvent.click(screen.getByRole("checkbox", { name: "2番 ハルカゼを比較する" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "5番 ホシカゲを比較する" }));
  expect(screen.getByRole("table", { name: "選択馬の比較" })).toBeTruthy();

  const expectNoSelection = () => {
    expect(screen.getByText("比較する馬を選択してください")).toBeTruthy();
    expect(screen.queryByRole("table", { name: "選択馬の比較" })).toBeNull();
    for (const checkbox of screen.getAllByRole("checkbox")) {
      expect((checkbox as HTMLInputElement).checked).toBe(false);
    }
  };

  view.rerender(<RaceDecisionDetail detail={changedDetail}
    dataState="判定済み" backLabel="一覧へ戻る" onBack={() => {}} />);
  expectNoSelection();

  view.rerender(<RaceDecisionDetail detail={detail()}
    dataState="判定済み" backLabel="一覧へ戻る" onBack={() => {}} />);
  expectNoSelection();
});

it("stores private opinion history, counts Unicode code points and gates reflections on saved results", async () => {
  const view = render(<RaceDecisionDetail detail={detail()} dataState="判定済み"
    backLabel="一覧へ戻る" onBack={() => {}} />);
  const personalRegion = screen.getByRole("region", { name: "自分の記録" });
  const personal = within(personalRegion);
  const memo = await screen.findByRole("textbox", { name: "1番 自分のメモ" });
  expect(screen.getAllByText("振り返りは結果保存後に記録できます。")).toHaveLength(5);
  expect((screen.getByRole("textbox", { name: "1番 振り返りを追記" }) as HTMLTextAreaElement).disabled).toBe(true);
  expect(personal.queryByText(/JRA|公式/)).toBeNull();

  fireEvent.change(memo, { target: { value: "😀".repeat(2000) } });
  expect(screen.getByText("メモ・見送り理由 2000/2000文字")).toBeTruthy();
  expect((personal.getByRole("button", { name: "1番の意見を履歴に保存" }) as HTMLButtonElement).disabled).toBe(false);
  fireEvent.change(memo, { target: { value: "😀".repeat(2001) } });
  expect(screen.getByText("メモ・見送り理由 2001/2000文字")).toBeTruthy();
  expect((personal.getByRole("button", { name: "1番の意見を履歴に保存" }) as HTMLButtonElement).disabled).toBe(false);
  expect(personal.getByRole("button", { name: "1番の意見を履歴に保存" }).getAttribute("aria-disabled")).toBe("true");

  fireEvent.change(screen.getByRole("combobox", { name: "1番 自分の印" }), { target: { value: "◎" } });
  fireEvent.change(memo, { target: { value: "◎の考え" } });
  const savedRevision = { id: 1, race_id: 7, horse_number: 1, mark: "◎", memo: "◎の考え",
    skip_reason: null, recorded_at: "2026-09-05T05:01:00Z" };
  fetchMock.mockResolvedValueOnce(jsonResponse(savedRevision));
  fireEvent.click(personal.getByRole("button", { name: "1番の意見を履歴に保存" }));
  await screen.findByText("自分のメモ: ◎の考え");
  expect(personal.getByText("保存しました。記録の履歴に追加しました。")).toBeTruthy();
  expect(fetchMock.mock.calls.at(-1)?.[0]).toBe("/api/races/7/runners/1/personal-opinion-revisions");
  const firstOperation = JSON.parse(fetchMock.mock.calls.at(-1)?.[1]?.body as string) as Record<string, string | null>;
  expect(firstOperation).toMatchObject({ mark: "◎", memo: "◎の考え", skip_reason: null });
  expect(firstOperation.operation_id).toMatch(/^[0-9a-f-]{36}$/i);

  const clearedRevision = { ...savedRevision, id: 2, mark: null, memo: null };
  fetchMock.mockResolvedValueOnce(jsonResponse(clearedRevision));
  fireEvent.click(personal.getByRole("button", { name: "1番の記録をクリア" }));
  const clearOperation = JSON.parse(fetchMock.mock.calls.at(-1)?.[1]?.body as string) as { operation_id: string };
  expect(clearOperation.operation_id).not.toBe(firstOperation.operation_id);
  await waitFor(() => expect((screen.getByRole("textbox", { name: "1番 自分のメモ" }) as HTMLTextAreaElement).value).toBe(""));
  expect(personal.getByText("自分のメモ: ◎の考え")).toBeTruthy();
  expect(personal.getByText("自分のメモ: —")).toBeTruthy();
  expect((personal.getByRole("button", { name: "1番の記録をクリア" }) as HTMLButtonElement).disabled).toBe(false);
  expect(personal.getByRole("button", { name: "1番の記録をクリア" }).getAttribute("aria-disabled")).toBe("true");

  fireEvent.change(screen.getByRole("textbox", { name: "1番 自分のメモ" }), { target: { value: "再入力" } });
  fetchMock.mockResolvedValueOnce(jsonResponse({ detail: { message: "入力内容を確認してください。" } }, false));
  fireEvent.click(personal.getByRole("button", { name: "1番の意見を履歴に保存" }));
  const editedOperation = JSON.parse(fetchMock.mock.calls.at(-1)?.[1]?.body as string) as { operation_id: string };
  expect(editedOperation.operation_id).not.toBe(clearOperation.operation_id);
  await waitFor(() => expect(personal.getByRole("alert").textContent).toContain("入力内容を確認"));

  fetchMock.mockResolvedValueOnce(jsonResponse({ ...blankRecords([1, 2, 3, 4, 5]), result_saved: true }));
  view.rerender(<RaceDecisionDetail detail={detail({ snapshot_id: 42 })} dataState="判定済み"
    backLabel="一覧へ戻る" onBack={() => {}} />);
  await waitFor(() => expect(screen.getByText("結果保存後")).toBeTruthy());
  const reflection = screen.getByRole("textbox", { name: "1番 振り返りを追記" });
  expect((reflection as HTMLTextAreaElement).disabled).toBe(false);
  fireEvent.change(reflection, { target: { value: "CSV結果を見て振り返る" } });
  fetchMock.mockResolvedValueOnce(jsonResponse({ id: 1, race_id: 7, horse_number: 1,
    reflection: "CSV結果を見て振り返る", recorded_at: "2026-09-05T05:02:00Z" }));
  fireEvent.click(within(screen.getByRole("region", { name: "自分の記録" }))
    .getByRole("button", { name: "1番の振り返りを追記" }));
  const reflectionOperation = JSON.parse(fetchMock.mock.calls.at(-1)?.[1]?.body as string) as { operation_id: string };
  expect(reflectionOperation.operation_id).toMatch(/^[0-9a-f-]{36}$/i);
  await screen.findByText("CSV結果を見て振り返る");
  await waitFor(() => expect(screen.getByText("振り返りを追加しました。")).toBeTruthy());
});

it("reuses an operation ID after a lost response and creates a new one after editing", async () => {
  render(<RaceDecisionDetail detail={detail()} dataState="判定済み"
    backLabel="一覧へ戻る" onBack={() => {}} />);
  const memo = await screen.findByRole("textbox", { name: "1番 自分のメモ" });
  fireEvent.change(memo, { target: { value: "retry me" } });
  fetchMock.mockRejectedValueOnce(new TypeError("response lost after commit"));
  fireEvent.click(screen.getByRole("button", { name: "1番の意見を履歴に保存" }));
  await screen.findByRole("alert");
  const firstAttempt = JSON.parse(fetchMock.mock.calls.at(-1)?.[1]?.body as string) as { operation_id: string };

  fetchMock.mockResolvedValueOnce(jsonResponse({ id: 1, race_id: 7, horse_number: 1,
    mark: null, memo: "retry me", skip_reason: null, recorded_at: "2026-09-05T05:01:00Z" }));
  fireEvent.click(screen.getByRole("button", { name: "1番の意見を履歴に保存" }));
  await screen.findByText("保存しました。記録の履歴に追加しました。");
  const retry = JSON.parse(fetchMock.mock.calls.at(-1)?.[1]?.body as string) as { operation_id: string };
  expect(retry.operation_id).toBe(firstAttempt.operation_id);

  fireEvent.change(memo, { target: { value: "edited retry" } });
  fetchMock.mockResolvedValueOnce(jsonResponse({ id: 2, race_id: 7, horse_number: 1,
    mark: null, memo: "edited retry", skip_reason: null, recorded_at: "2026-09-05T05:02:00Z" }));
  fireEvent.click(screen.getByRole("button", { name: "1番の意見を履歴に保存" }));
  await screen.findByText("自分のメモ: edited retry");
  const edited = JSON.parse(fetchMock.mock.calls.at(-1)?.[1]?.body as string) as { operation_id: string };
  expect(edited.operation_id).not.toBe(firstAttempt.operation_id);
});

it("allows different horses to save concurrently and suppresses a duplicate save for one horse", async () => {
  const pending: Array<(response: Response) => void> = [];
  fetchMock.mockImplementation((input: RequestInfo | URL) => {
    if (String(input).endsWith("personal-runner-records")) {
      return Promise.resolve(jsonResponse(blankRecords([1, 2, 3, 4, 5])));
    }
    return new Promise<Response>((resolve) => pending.push(resolve));
  });
  render(<RaceDecisionDetail detail={detail()} dataState="判定済み"
    backLabel="一覧へ戻る" onBack={() => {}} />);
  await screen.findByRole("textbox", { name: "1番 自分のメモ" });
  fireEvent.change(screen.getByRole("textbox", { name: "1番 自分のメモ" }), { target: { value: "horse one" } });
  fireEvent.change(screen.getByRole("textbox", { name: "2番 自分のメモ" }), { target: { value: "horse two" } });
  const firstButton = screen.getByRole("button", { name: "1番の意見を履歴に保存" });
  firstButton.focus();
  fireEvent.click(firstButton);
  fireEvent.click(firstButton);
  fireEvent.click(screen.getByRole("button", { name: "2番の意見を履歴に保存" }));

  await waitFor(() => expect(fetchMock.mock.calls.filter(([url]) =>
    String(url).includes("personal-opinion-revisions"))).toHaveLength(2));
  expect((firstButton as HTMLButtonElement).disabled).toBe(false);
  expect(firstButton.getAttribute("aria-disabled")).toBe("true");
  expect(firstButton.getAttribute("aria-busy")).toBe("true");
  expect(screen.getByText("1番の記録を保存しています。")).toBeTruthy();
  expect(document.activeElement).toBe(firstButton);
  expect(fetchMock.mock.calls.filter(([url]) => String(url).includes("personal-opinion-revisions"))
    .map(([url]) => String(url))).toEqual([
    "/api/races/7/runners/1/personal-opinion-revisions",
    "/api/races/7/runners/2/personal-opinion-revisions",
  ]);
  pending[0](jsonResponse({ id: 1, race_id: 7, horse_number: 1, mark: null,
    memo: "horse one", skip_reason: null, recorded_at: "2026-09-05T05:01:00Z" }));
  await screen.findByText("自分のメモ: horse one");
  expect(document.activeElement).toBe(firstButton);
  const secondMemo = screen.getByRole("textbox", { name: "2番 自分のメモ" });
  secondMemo.focus();
  pending[1](jsonResponse({ id: 2, race_id: 7, horse_number: 2, mark: null,
    memo: "horse two", skip_reason: null, recorded_at: "2026-09-05T05:02:00Z" }));
  await screen.findByText("自分のメモ: horse two");
  expect(document.activeElement).toBe(secondMemo);
});

it("preserves save focus through a 409 refresh without stealing focus after the user moves", async () => {
  const pending: Array<(response: Response) => void> = [];
  fetchMock.mockImplementation((input: RequestInfo | URL) => {
    if (String(input).endsWith("personal-runner-records")) {
      return Promise.resolve(jsonResponse(blankRecords([1, 2, 3, 4, 5])));
    }
    if (String(input).endsWith("personal-opinion-revisions")) {
      return new Promise<Response>((resolve) => pending.push(resolve));
    }
    return Promise.resolve(jsonResponse({}));
  });
  render(<RaceDecisionDetail detail={detail()} dataState="判定済み"
    backLabel="一覧へ戻る" onBack={() => {}} />);
  const memo = await screen.findByRole("textbox", { name: "1番 自分のメモ" });
  fireEvent.change(memo, { target: { value: "409 focus" } });
  const saveButton = screen.getByRole("button", { name: "1番の意見を履歴に保存" });
  saveButton.focus();
  fireEvent.click(saveButton);
  await waitFor(() => expect(pending).toHaveLength(1));
  expect((saveButton as HTMLButtonElement).disabled).toBe(false);
  expect(document.activeElement).toBe(saveButton);
  pending[0](jsonResponse({ detail: { code: "personal_operation_conflict", message: "合成409" } }, false, 409));
  await screen.findByRole("alert");
  expect((memo as HTMLTextAreaElement).value).toBe("409 focus");
  expect(document.activeElement).toBe(saveButton);

  fireEvent.change(memo, { target: { value: "409 while editing focus" } });
  saveButton.focus();
  fireEvent.click(saveButton);
  await waitFor(() => expect(pending).toHaveLength(2));
  memo.focus();
  pending[1](jsonResponse({ detail: { code: "personal_operation_conflict", message: "合成409再試行" } }, false, 409));
  await screen.findByText("合成409再試行");
  expect(document.activeElement).toBe(memo);
  expect((memo as HTMLTextAreaElement).value).toBe("409 while editing focus");
});

it("keeps clear and reflection controls focused when their action conditions change", async () => {
  const currentOpinion = { id: 1, race_id: 7, horse_number: 1, mark: "◎", memo: "keep",
    skip_reason: null, recorded_at: "2026-09-05T05:01:00Z" };
  const initialRecords = blankRecords([1, 2, 3, 4, 5]);
  initialRecords.result_saved = true;
  initialRecords.runners[0].current_revision = currentOpinion;
  initialRecords.runners[0].revisions = [currentOpinion];
  const pending: Array<{ url: string; resolve: (response: Response) => void }> = [];
  let personalReads = 0;
  fetchMock.mockImplementation((input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith("personal-runner-records")) {
      personalReads += 1;
      return Promise.resolve(jsonResponse(personalReads === 1
        ? initialRecords : blankRecords([1, 2, 3, 4, 5])));
    }
    if (url.endsWith("personal-opinion-revisions") || url.endsWith("personal-reflections")) {
      return new Promise<Response>((resolve) => pending.push({ url, resolve }));
    }
    return Promise.resolve(jsonResponse({}));
  });
  render(<RaceDecisionDetail detail={detail()} dataState="判定済み"
    backLabel="一覧へ戻る" onBack={() => {}} />);
  const clearButton = await screen.findByRole("button", { name: "1番の記録をクリア" });
  clearButton.focus();
  fireEvent.click(clearButton);
  await waitFor(() => expect(pending).toHaveLength(1));
  expect((clearButton as HTMLButtonElement).disabled).toBe(false);
  expect(clearButton.getAttribute("aria-disabled")).toBe("true");
  pending[0].resolve(jsonResponse({ ...currentOpinion, id: 2, mark: null, memo: null }));
  await screen.findByText("保存しました。記録の履歴に追加しました。");
  await waitFor(() => expect(clearButton.getAttribute("aria-disabled")).toBe("true"));
  expect(document.activeElement).toBe(clearButton);
  fireEvent.click(clearButton);
  expect(pending).toHaveLength(1);

  const initialOpinionButton = screen.getByRole("button", { name: "2番の意見を履歴に保存" });
  expect((initialOpinionButton as HTMLButtonElement).disabled).toBe(false);
  expect(initialOpinionButton.getAttribute("aria-disabled")).toBe("true");
  fireEvent.click(initialOpinionButton);
  expect(pending).toHaveLength(1);
  const secondMemo = screen.getByRole("textbox", { name: "2番 自分のメモ" });
  fireEvent.change(secondMemo, { target: { value: "x".repeat(2001) } });
  expect(initialOpinionButton.getAttribute("aria-disabled")).toBe("true");
  fireEvent.click(initialOpinionButton);
  expect(pending).toHaveLength(1);

  const reflection = screen.getByRole("textbox", { name: "1番 振り返りを追記" });
  const reflectionButton = screen.getByRole("button", { name: "1番の振り返りを追記" });
  expect((reflectionButton as HTMLButtonElement).disabled).toBe(false);
  expect(reflectionButton.getAttribute("aria-disabled")).toBe("true");
  fireEvent.click(reflectionButton);
  expect(pending).toHaveLength(1);
  fireEvent.change(reflection, { target: { value: "x".repeat(2001) } });
  expect(reflectionButton.getAttribute("aria-disabled")).toBe("true");
  fireEvent.click(reflectionButton);
  expect(pending).toHaveLength(1);

  fireEvent.change(reflection, { target: { value: "saved reflection" } });
  reflectionButton.focus();
  fireEvent.click(reflectionButton);
  await waitFor(() => expect(pending).toHaveLength(2));
  expect((reflectionButton as HTMLButtonElement).disabled).toBe(false);
  expect(reflectionButton.getAttribute("aria-disabled")).toBe("true");
  pending[1].resolve(jsonResponse({ id: 1, race_id: 7, horse_number: 1,
    reflection: "saved reflection", recorded_at: "2026-09-05T05:02:00Z" }));
  await screen.findByText("振り返りを追加しました。");
  await waitFor(() => expect(reflectionButton.getAttribute("aria-disabled")).toBe("true"));
  expect(document.activeElement).toBe(reflectionButton);
  fireEvent.click(reflectionButton);
  expect(pending).toHaveLength(2);

  fireEvent.change(reflection, { target: { value: "409 result-gate update" } });
  reflectionButton.focus();
  fireEvent.click(reflectionButton);
  await waitFor(() => expect(pending).toHaveLength(3));
  pending[2].resolve(jsonResponse({ detail: { code: "result_not_saved", message: "合成409結果状態" } }, false, 409));
  await screen.findByText("合成409結果状態");
  expect((reflection as HTMLTextAreaElement).disabled).toBe(true);
  expect((reflectionButton as HTMLButtonElement).disabled).toBe(false);
  expect(reflectionButton.getAttribute("aria-disabled")).toBe("true");
  expect(document.activeElement).toBe(reflectionButton);
  fireEvent.click(reflectionButton);
  expect(pending).toHaveLength(3);
});

it("keeps explicit reload focus while loading and suppresses concurrent reloads", async () => {
  const pendingLoads: Array<(response: Response) => void> = [];
  fetchMock.mockImplementation((input: RequestInfo | URL) => {
    if (String(input).endsWith("personal-runner-records")) {
      return new Promise<Response>((resolve) => pendingLoads.push(resolve));
    }
    return Promise.resolve(jsonResponse({}));
  });
  render(<RaceDecisionDetail detail={detail()} dataState="判定済み"
    backLabel="一覧へ戻る" onBack={() => {}} />);
  const reloadButton = screen.getByRole("button", { name: "個人記録を再読込" });
  expect((reloadButton as HTMLButtonElement).disabled).toBe(false);
  expect(reloadButton.getAttribute("aria-disabled")).toBe("true");
  reloadButton.focus();
  fireEvent.click(reloadButton);
  await waitFor(() => expect(pendingLoads).toHaveLength(1));
  expect(document.activeElement).toBe(reloadButton);
  pendingLoads[0](jsonResponse(blankRecords([1, 2, 3, 4, 5])));
  await screen.findByRole("textbox", { name: "1番 自分のメモ" });
  expect(reloadButton.getAttribute("aria-disabled")).toBe("false");
  expect(document.activeElement).toBe(reloadButton);

  fireEvent.click(reloadButton);
  await waitFor(() => expect(pendingLoads).toHaveLength(2));
  expect((reloadButton as HTMLButtonElement).disabled).toBe(false);
  expect(reloadButton.getAttribute("aria-disabled")).toBe("true");
  expect(reloadButton.getAttribute("aria-busy")).toBe("true");
  fireEvent.click(reloadButton);
  expect(pendingLoads).toHaveLength(2);
  expect(document.activeElement).toBe(reloadButton);
  pendingLoads[1](jsonResponse(blankRecords([1, 2, 3, 4, 5])));
  await waitFor(() => expect(reloadButton.getAttribute("aria-busy")).toBe("false"));
  expect(document.activeElement).toBe(reloadButton);
});

it("reloads the same race's reflection gate after CSV import and on explicit request", async () => {
  let resultSaved = false;
  let personalGetCount = 0;
  const totals = { stake_yen: 0, payout_yen: 0, refund_yen: 0, profit_yen: null, return_rate: null };
  const ledger = {
    bets: [], result_version: null, totals,
    by_decision_type: { candidate: totals, discretionary: totals },
  };
  fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith("personal-runner-records")) {
      personalGetCount += 1;
      return jsonResponse({ ...blankRecords([1, 2, 3, 4, 5]), result_saved: resultSaved });
    }
    if (url.endsWith("/ledger")) return jsonResponse(ledger);
    if (url.endsWith("/predictions")) return jsonResponse([]);
    if (url.endsWith("/results/import")) {
      resultSaved = true;
      return jsonResponse({ result: { version: 1 } });
    }
    return jsonResponse({});
  });
  render(<RaceDecisionDetail detail={detail()} dataState="判定済み"
    backLabel="一覧へ戻る" onBack={() => {}} />);
  const reflection = await screen.findByRole("textbox", { name: "1番 振り返りを追記" });
  expect((reflection as HTMLTextAreaElement).disabled).toBe(true);
  const initialGets = personalGetCount;

  fireEvent.click(screen.getByRole("button", { name: "購入・収支を表示" }));
  const resultInput = await screen.findByLabelText("結果CSVファイル");
  fireEvent.change(resultInput, { target: { files: [new File(["synthetic csv"], "results.csv", { type: "text/csv" })] } });
  fireEvent.click(screen.getByRole("button", { name: "結果を取り込んで精算" }));
  await waitFor(() => expect(personalGetCount).toBeGreaterThan(initialGets));
  await waitFor(() => expect((screen.getByRole("textbox", { name: "1番 振り返りを追記" }) as HTMLTextAreaElement).disabled).toBe(false));
  expect(screen.getByText("結果保存後")).toBeTruthy();

  const afterCsvGets = personalGetCount;
  fireEvent.click(screen.getByRole("button", { name: "個人記録を再読込" }));
  await waitFor(() => expect(personalGetCount).toBeGreaterThan(afterCsvGets));
});

it("refreshes result state after a reflection 409 and preserves the draft", async () => {
  let personalReads = 0;
  fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
    if (String(input).endsWith("personal-runner-records")) {
      personalReads += 1;
      return jsonResponse({ ...blankRecords([1, 2, 3, 4, 5]), result_saved: personalReads === 1 });
    }
    if (String(input).includes("personal-reflections")) {
      return jsonResponse({ detail: { code: "result_not_saved", message: "振り返りは結果保存後に記録できます。" } }, false, 409);
    }
    return jsonResponse({});
  });
  render(<RaceDecisionDetail detail={detail()} dataState="判定済み"
    backLabel="一覧へ戻る" onBack={() => {}} />);
  const reflection = await screen.findByRole("textbox", { name: "1番 振り返りを追記" });
  fireEvent.change(reflection, { target: { value: "結果差替えを検知" } });
  fireEvent.click(screen.getByRole("button", { name: "1番の振り返りを追記" }));
  await waitFor(() => expect(personalReads).toBe(2));
  expect((screen.getByRole("textbox", { name: "1番 振り返りを追記" }) as HTMLTextAreaElement).disabled).toBe(true);
  expect((screen.getByRole("textbox", { name: "1番 振り返りを追記" }) as HTMLTextAreaElement).value)
    .toBe("結果差替えを検知");
  expect(screen.getByRole("alert").textContent).toContain("結果保存後");
});

it("keeps opinion history ordered and deduplicates a replay after a clock rollback", async () => {
  const laterAppend = { id: 11, race_id: 7, horse_number: 1, mark: "◎", memo: "先に追加",
    skip_reason: null, recorded_at: "2026-09-05T12:00:00Z" };
  const clockRollbackAppend = { ...laterAppend, id: 12, memo: "時計が戻った記録",
    recorded_at: "2026-09-05T09:00:00Z" };
  const secondClockRollbackAppend = { ...laterAppend, id: 13, memo: "さらに時計が戻った記録",
    recorded_at: "2026-09-05T08:00:00Z" };
  let personalReads = 0;
  let opinionPosts = 0;
  fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith("personal-runner-records")) {
      personalReads += 1;
      if (personalReads === 1) return jsonResponse(blankRecords([1, 2, 3, 4, 5]));
      const baseline = blankRecords([1, 2, 3, 4, 5]);
      return jsonResponse({ ...baseline, runners: baseline.runners.map((runner, index) => index === 0
        ? { ...runner, current_revision: clockRollbackAppend, revisions: [clockRollbackAppend, laterAppend] }
        : runner) });
    }
    if (url.endsWith("personal-opinion-revisions")) {
      opinionPosts += 1;
      if (opinionPosts === 2) throw new TypeError("response lost after commit");
      return jsonResponse(opinionPosts === 1 ? laterAppend
        : opinionPosts === 3 ? clockRollbackAppend : secondClockRollbackAppend);
    }
    return jsonResponse({});
  });
  render(<RaceDecisionDetail detail={detail()} dataState="判定済み"
    backLabel="一覧へ戻る" onBack={() => {}} />);

  const memo = await screen.findByRole("textbox", { name: "1番 自分のメモ" });
  fireEvent.change(memo, { target: { value: "先に追加" } });
  fireEvent.click(screen.getByRole("button", { name: "1番の意見を履歴に保存" }));
  await screen.findByText("自分のメモ: 先に追加");
  fireEvent.change(memo, { target: { value: "時計が戻った記録" } });
  fireEvent.click(screen.getByRole("button", { name: "1番の意見を履歴に保存" }));
  await screen.findByRole("alert");
  const lostRequest = JSON.parse(fetchMock.mock.calls.at(-1)?.[1]?.body as string) as { operation_id: string };

  fireEvent.click(screen.getByRole("button", { name: "個人記録を再読込" }));
  await screen.findByText("自分のメモ: 時計が戻った記録");
  fireEvent.click(screen.getByRole("button", { name: "1番の意見を履歴に保存" }));
  await screen.findByText("保存しました。記録の履歴に追加しました。");
  const retry = JSON.parse(fetchMock.mock.calls.at(-1)?.[1]?.body as string) as { operation_id: string };
  expect(retry.operation_id).toBe(lostRequest.operation_id);

  const history = within(screen.getByRole("region", { name: "1番 記録の履歴" }));
  let entries = history.getAllByRole("listitem");
  expect(entries).toHaveLength(2);
  expect(entries.map((entry) => entry.textContent)).toEqual([
    expect.stringContaining("時計が戻った記録"), expect.stringContaining("先に追加"),
  ]);
  expect((memo as HTMLTextAreaElement).value).toBe("時計が戻った記録");

  fireEvent.change(memo, { target: { value: "さらに時計が戻った記録" } });
  fireEvent.click(screen.getByRole("button", { name: "1番の意見を履歴に保存" }));
  await screen.findByText("自分のメモ: さらに時計が戻った記録");
  entries = history.getAllByRole("listitem");
  expect(entries).toHaveLength(3);
  expect(entries.map((entry) => entry.textContent)).toEqual([
    expect.stringContaining("さらに時計が戻った記録"),
    expect.stringContaining("時計が戻った記録"),
    expect.stringContaining("先に追加"),
  ]);
});

it("keeps reflection history ordered and deduplicates an idempotent replay", async () => {
  const laterReflection = { id: 21, race_id: 7, horse_number: 1, reflection: "先の振り返り",
    recorded_at: "2026-09-05T12:00:00Z" };
  const clockRollbackReflection = { ...laterReflection, id: 22,
    reflection: "時計が戻った振り返り", recorded_at: "2026-09-05T09:00:00Z" };
  let personalReads = 0;
  let reflectionPosts = 0;
  fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith("personal-runner-records")) {
      personalReads += 1;
      const baseline = blankRecords([1, 2, 3, 4, 5]);
      baseline.result_saved = true;
      return jsonResponse({ ...baseline, runners: baseline.runners.map((runner, index) =>
        index === 0 && personalReads > 1 ? { ...runner, reflections: [laterReflection] } : runner) });
    }
    if (url.endsWith("personal-reflections")) {
      reflectionPosts += 1;
      if (reflectionPosts === 1) throw new TypeError("response lost after commit");
      return jsonResponse(reflectionPosts === 2 ? laterReflection : clockRollbackReflection);
    }
    return jsonResponse({});
  });
  render(<RaceDecisionDetail detail={detail()} dataState="判定済み"
    backLabel="一覧へ戻る" onBack={() => {}} />);

  const reflection = await screen.findByRole("textbox", { name: "1番 振り返りを追記" });
  fireEvent.change(reflection, { target: { value: "先の振り返り" } });
  fireEvent.click(screen.getByRole("button", { name: "1番の振り返りを追記" }));
  await screen.findByRole("alert");
  const lostRequest = JSON.parse(fetchMock.mock.calls.at(-1)?.[1]?.body as string) as { operation_id: string };

  fireEvent.click(screen.getByRole("button", { name: "個人記録を再読込" }));
  await screen.findByText("先の振り返り");
  fireEvent.click(screen.getByRole("button", { name: "1番の振り返りを追記" }));
  await screen.findByText("振り返りを追加しました。");
  const retry = JSON.parse(fetchMock.mock.calls.at(-1)?.[1]?.body as string) as { operation_id: string };
  expect(retry.operation_id).toBe(lostRequest.operation_id);
  const history = within(screen.getByRole("region", { name: "1番 自分の振り返り" }));
  expect(history.getAllByRole("listitem")).toHaveLength(1);

  fireEvent.change(reflection, { target: { value: "時計が戻った振り返り" } });
  fireEvent.click(screen.getByRole("button", { name: "1番の振り返りを追記" }));
  await screen.findByText("時計が戻った振り返り");
  const entries = history.getAllByRole("listitem");
  expect(entries).toHaveLength(2);
  expect(entries.map((entry) => entry.textContent)).toEqual([
    expect.stringContaining("時計が戻った振り返り"), expect.stringContaining("先の振り返り"),
  ]);
});
