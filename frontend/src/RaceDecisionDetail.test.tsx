import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";

import RaceDecisionDetail, { type DecisionRunner, type DecisionView } from "./RaceDecisionDetail";

afterEach(cleanup);

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
