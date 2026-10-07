import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import RulePerformancePanel from "./RulePerformancePanel";


const group = {
  rule_version: { id: 3, rule_key: "market_observation_filter", version: 2, title: "保存済み注目条件" },
  status: "available",
  counts: {
    considered_races: 1, candidate_runs: 2, selected_runs: 1,
    duplicate_runs_excluded: 1, ineligible_runs_excluded: 0,
    invalidated_runs_excluded: 0, invalidated_without_active_pre_race_run: 0,
    candidate_races: 1, selected_races: 1, selected_snapshots: 1,
    selected_runner_observations: 5, attention_runners: 2,
    dismissed_runners: 3, undecidable_runners: 0,
    selected_race_date_from: "2026-08-30", selected_race_date_to: "2026-08-30",
    selected_frozen_at_from: "2026-08-30T05:10:00Z", selected_frozen_at_to: "2026-08-30T05:10:00Z",
  },
  selected_runs: [{
    race_id: 1, race_date: "2026-08-30", racecourse: "東京", race_number: 11,
    judgement_run_id: 8, input_snapshot_id: 4, frozen_at: "2026-08-30T05:10:00Z",
    active_result_version_id: 9, active_result_version_number: 2,
  }],
  result_exclusion_races: {}, corrected_result_races: 0,
  bet_types: {
    win: {
      status: "available", candidate_tickets: 2, evaluated_tickets: 2,
      hit_tickets: 1, hit_rate: 0.5, stake_yen: 200, payout_yen: 420,
      return_rate: 2.1, refund_unverified_tickets: 0,
      refund_yen: null, refund_state: "unverified", exclusions: {},
    },
    place: {
      status: "no_evaluated_tickets", candidate_tickets: 2, evaluated_tickets: 0,
      hit_tickets: 0, hit_rate: null, stake_yen: null, payout_yen: null,
      return_rate: null, refund_unverified_tickets: 0,
      refund_yen: null, refund_state: "unverified", exclusions: { result_pending: 2 },
    },
  },
};

const report = {
  filters: {
    race_date_from: null, race_date_to: null, frozen_at_from: null,
    frozen_at_to: null, rule_version_id: null,
  },
  groups: [group],
  disclaimer: "保存済みの事前判定と公式払戻の記述集計です。",
};

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("rule performance panel", () => {
  it("shows separate win/place denominators without displaying null metrics as zero and applies filters", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(report), {
      status: 200, headers: { "Content-Type": "application/json" },
    }));
    vi.stubGlobal("fetch", fetchMock);
    render(<RulePerformancePanel />);

    const win = await screen.findByRole("region", { name: "単勝成績" });
    expect(screen.getByRole("heading", { level: 3, name: "保存済み注目条件 v2" })).toBeTruthy();
    const selection = screen.getByRole("table", { name: "選択した保存済み判定" });
    expect(within(selection).getByText("4")).toBeTruthy();
    expect(within(selection).getByText("v2 (#9)")).toBeTruthy();
    expect(within(win).getByText("50.00%（1/2件）")).toBeTruthy();
    expect(within(win).getByText("420円")).toBeTruthy();
    const place = screen.getByRole("region", { name: "複勝成績" });
    expect(within(place).getByText("—（0/0件）")).toBeTruthy();
    expect(within(place).getAllByText("—")).toHaveLength(3);
    expect(within(place).getByText(/結果待ち 2件/)).toBeTruthy();

    fireEvent.change(screen.getByLabelText("開催日（開始）"), { target: { value: "2026-08-01" } });
    fireEvent.change(screen.getByLabelText("開催日（終了）"), { target: { value: "2026-08-31" } });
    fireEvent.change(screen.getByLabelText("固定日時（開始）"), { target: { value: "2026-08-30T14:00" } });
    fireEvent.change(screen.getByLabelText("ルール版"), { target: { value: "3" } });
    fireEvent.click(screen.getByRole("button", { name: "成績を表示" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    const requestedUrl = String(fetchMock.mock.calls[1][0]);
    const params = new URL(requestedUrl, window.location.origin).searchParams;
    expect(params.get("race_date_from")).toBe("2026-08-01");
    expect(params.get("race_date_to")).toBe("2026-08-31");
    expect(params.get("frozen_at_from")).toBe(new Date("2026-08-30T14:00").toISOString());
    expect(params.get("rule_version_id")).toBe("3");
  });

  it("labels a four-runner place evaluation as unavailable within this app", async () => {
    const smallFieldReport = {
      ...report,
      groups: [{
        ...group,
        counts: { ...group.counts, selected_runner_observations: 4 },
        bet_types: {
          ...group.bet_types,
          place: {
            ...group.bet_types.place,
            status: "market_unavailable" as const,
            candidate_tickets: 2,
            exclusions: { place_market_unavailable: 2 },
          },
        },
      }],
    };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(smallFieldReport), {
      status: 200, headers: { "Content-Type": "application/json" },
    })));
    render(<RulePerformancePanel />);

    const place = await screen.findByRole("region", { name: "複勝成績" });
    expect(within(place).getByText("このアプリの保存出走馬数では複勝対象情報がありません。")).toBeTruthy();
    expect(within(place).getByText(/複勝対象情報なし 2件/)).toBeTruthy();
    expect(within(place).getByText("—（0/0件）")).toBeTruthy();
  });

  it("shows no result when the selected run has no active result version", async () => {
    const pendingReport = {
      ...report,
      groups: [{
        ...group,
        selected_runs: [{
          ...group.selected_runs[0],
          active_result_version_id: null,
          active_result_version_number: null,
        }],
      }],
    };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(pendingReport), {
      status: 200, headers: { "Content-Type": "application/json" },
    })));
    render(<RulePerformancePanel />);

    const selection = await screen.findByRole("table", { name: "選択した保存済み判定" });
    expect(within(selection).getByText("結果なし")).toBeTruthy();
  });

  it("shows why no eligible judgement runs were selected", async () => {
    const noRunReport = {
      ...report,
      groups: [{
        ...group,
        status: "no_runs" as const,
        counts: {
          ...group.counts,
          considered_races: 1,
          candidate_runs: 0,
          selected_runs: 0,
          ineligible_runs_excluded: 1,
          invalidated_runs_excluded: 1,
          invalidated_without_active_pre_race_run: 1,
          candidate_races: 0,
          selected_races: 0,
          selected_snapshots: 0,
          selected_runner_observations: 0,
        },
        selected_runs: [],
      }],
    };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(noRunReport), {
      status: 200, headers: { "Content-Type": "application/json" },
    })));
    render(<RulePerformancePanel />);

    await screen.findByText("選択条件に一致する保存済み事前判定はありません。");
    const groupRegion = screen.getByRole("region", { name: "保存済み注目条件 v2" });
    expect(groupRegion.textContent).toContain("対象race / 適格候補run1件 / 0件");
    expect(groupRegion.textContent).toContain("発走後・対象外run1件");
    expect(groupRegion.textContent).toContain("無効化run1件");
    expect(groupRegion.textContent).toContain("旧事前runの無効化により採用できないrace1件");
  });

  it("does not call mixed unavailable and unverified place candidates all unavailable", async () => {
    const mixedReport = {
      ...report,
      groups: [{
        ...group,
        bet_types: {
          ...group.bet_types,
          place: {
            ...group.bet_types.place,
            status: "no_evaluated_tickets" as const,
            candidate_tickets: 2,
            exclusions: { place_market_unavailable: 1, result_source_unverified: 1 },
          },
        },
      }],
    };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(mixedReport), {
      status: 200, headers: { "Content-Type": "application/json" },
    })));
    render(<RulePerformancePanel />);

    const place = await screen.findByRole("region", { name: "複勝成績" });
    expect(within(place).queryByText("このアプリの保存出走馬数では複勝対象情報がありません。")).toBeNull();
    expect(within(place).getByText(/複勝対象情報なし 1件/)).toBeTruthy();
    expect(within(place).getByText(/公式出典未確認 1件/)).toBeTruthy();
  });
});
