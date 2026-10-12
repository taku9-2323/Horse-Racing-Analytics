import { expect, it } from "vitest";

import { buildSnapshotComparison, getSnapshotAge, type OddsSnapshot } from "./oddsHistory";

function snapshot(overrides: Partial<OddsSnapshot> & Pick<OddsSnapshot, "id">): OddsSnapshot {
  return {
    race_id: 1,
    observed_at: "2026-08-30T10:00:00Z",
    received_at: "2026-08-30T10:02:00Z",
    source_updated_at: null,
    source: "user_csv",
    runners: [],
    ...overrides,
  };
}

it("computes interval and each odds metric from the immediate predecessor by horse number", () => {
  const first = snapshot({
    id: 100,
    observed_at: "2026-08-30T10:00:00Z",
    runners: [
      { horse_number: 2, win_odds: 4, place_odds_min: 2, place_odds_max: 3 },
      { horse_number: 1, win_odds: 2, place_odds_min: 1.2, place_odds_max: 1.5 },
    ],
  });
  const selected = snapshot({
    id: 101,
    observed_at: "2026-08-30T11:00:00Z",
    runners: [
      { horse_number: 1, win_odds: 2.5, place_odds_min: 1.3, place_odds_max: 1.8 },
      { horse_number: 2, win_odds: 5, place_odds_min: 2.2, place_odds_max: 3.3 },
    ],
  });

  const comparison = buildSnapshotComparison([first, selected], 101);

  expect(comparison?.previous?.id).toBe(100);
  expect(comparison?.timeComparable).toBe(true);
  expect(comparison?.intervalMilliseconds).toBe(60 * 60 * 1000);
  expect(comparison?.rows.map((row) => row.horseNumber)).toEqual([1, 2]);
  expect(comparison?.rows[0].metrics[0].difference).toBeCloseTo(0.5);
  expect(comparison?.rows[0].metrics[1].difference).toBeCloseTo(0.1);
  expect(comparison?.rows[0].metrics[2].difference).toBeCloseTo(0.3);
  expect(comparison?.rows[0].metrics[0].percentChange).toBeCloseTo(25);
  expect(comparison?.rows[0].metrics[1].percentChange).toBeCloseTo(100 / 12);
  expect(comparison?.rows[0].metrics[2].percentChange).toBeCloseTo(20);
});

it("uses only the preceding saved ID and blocks every delta when its observation time reverses", () => {
  const oldest = snapshot({
    id: 100,
    observed_at: "2026-08-30T10:00:00Z",
    runners: [{ horse_number: 1, win_odds: 2, place_odds_min: 1.2, place_odds_max: 1.5 }],
  });
  const immediate = snapshot({
    id: 101,
    observed_at: "2026-08-30T12:00:00Z",
    received_at: "2026-08-30T12:01:00Z",
    runners: [{ horse_number: 1, win_odds: 3, place_odds_min: 1.4, place_odds_max: 1.8 }],
  });
  const selected = snapshot({
    id: 102,
    observed_at: "2026-08-30T11:00:00Z",
    received_at: "2026-08-30T12:02:00Z",
    runners: [{ horse_number: 1, win_odds: 4, place_odds_min: 1.6, place_odds_max: 2 }],
  });

  const comparison = buildSnapshotComparison([oldest, immediate, selected], 102);

  expect(comparison?.previous?.id).toBe(101);
  expect(comparison?.timeComparable).toBe(false);
  expect(comparison?.reason).toContain("逆転");
  expect(comparison?.intervalMilliseconds).toBeNull();
  expect(comparison?.rows[0].metrics.map((metric) => metric.difference)).toEqual([null, null, null]);
  expect(comparison?.rows[0].metrics[0].previousValue).toBe(3);
  expect(comparison?.rows[0].metrics[0].selectedValue).toBe(4);
});

it.each([
  { label: "selected observation is missing", previousAt: "2026-08-30T10:00:00Z", selectedAt: null, reason: "不明" },
  { label: "previous observation is malformed", previousAt: "2026-08-30T10:00:00", selectedAt: "2026-08-30T11:00:00Z", reason: "解釈" },
  { label: "previous calendar date is malformed", previousAt: "2026-02-30T10:00:00Z", selectedAt: "2026-08-30T11:00:00Z", reason: "解釈" },
  { label: "observations have the same instant", previousAt: "2026-08-30T10:00:00Z", selectedAt: "2026-08-30T10:00:00+00:00", reason: "同じ" },
])("blocks every delta when $label", ({ previousAt, selectedAt, reason }) => {
  const previous = snapshot({
    id: 30,
    observed_at: previousAt,
    received_at: "2026-08-30T10:02:00Z",
    source_updated_at: "2026-08-30T09:58:00Z",
    runners: [{ horse_number: 1, win_odds: 2, place_odds_min: 1.2, place_odds_max: 1.5 }],
  });
  const selected = snapshot({
    id: 31,
    observed_at: selectedAt,
    received_at: "2026-08-30T11:02:00Z",
    source_updated_at: "2026-08-30T10:58:00Z",
    runners: [{ horse_number: 1, win_odds: 3, place_odds_min: 1.4, place_odds_max: 1.8 }],
  });

  const comparison = buildSnapshotComparison([previous, selected], selected.id);

  expect(comparison?.previous?.id).toBe(previous.id);
  expect(comparison?.timeComparable).toBe(false);
  expect(comparison?.reason).toContain(reason);
  expect(comparison?.intervalMilliseconds).toBeNull();
  expect(comparison?.rows[0].metrics.every((metric) => metric.difference === null && metric.percentChange === null)).toBe(true);
});

it("keeps valid metrics when another metric is invalid and joins a one-sided runner by horse number", () => {
  const previous = snapshot({
    id: 7,
    observed_at: "2026-08-30T10:00:00Z",
    runners: [
      { horse_number: 1, win_odds: 2, place_odds_min: null, place_odds_max: -1 },
    ],
  });
  const selected = snapshot({
    id: 8,
    observed_at: "2026-08-30T10:05:00Z",
    runners: [
      { horse_number: 9, win_odds: 7, place_odds_min: 4, place_odds_max: 5 },
      { horse_number: 1, win_odds: 2.5, place_odds_min: 1.2, place_odds_max: 1.6 },
    ],
  });

  const comparison = buildSnapshotComparison([previous, selected], 8);

  expect(comparison?.rows.map((row) => [row.horseNumber, row.runnerState])).toEqual([
    [1, "matched"], [9, "selected_only"],
  ]);
  expect(comparison?.rows[0].metrics[0].difference).toBe(0.5);
  expect(comparison?.rows[0].metrics[1].difference).toBeNull();
  expect(comparison?.rows[0].metrics[1].previousValue).toBeNull();
  expect(comparison?.rows[0].metrics[1].unavailableReason).toContain("欠損");
  expect(comparison?.rows[0].metrics[2].difference).toBeNull();
  expect(comparison?.rows[0].metrics[2].unavailableReason).toContain("正の有限値");
  expect(comparison?.rows[1].metrics.every((metric) => metric.difference === null)).toBe(true);
});

it("does not infer freshness from receipt time or display a negative age for future observations", () => {
  const asOf = Date.parse("2026-08-30T11:30:00Z");

  expect(getSnapshotAge("2026-08-30T11:00:00Z", asOf)).toEqual({
    elapsedMilliseconds: 30 * 60 * 1000, reason: null,
  });
  expect(getSnapshotAge(null, asOf).elapsedMilliseconds).toBeNull();
  expect(getSnapshotAge("2026-08-30T11:00:00", asOf).elapsedMilliseconds).toBeNull();
  expect(getSnapshotAge("2026-08-30T12:00:00Z", asOf).reason).toContain("未来");
});
