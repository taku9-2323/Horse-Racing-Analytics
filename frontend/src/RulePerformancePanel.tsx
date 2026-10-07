import { FormEvent, useEffect, useState } from "react";


type BetTypeReport = {
  status: "available" | "no_evaluated_tickets" | "market_unavailable";
  candidate_tickets: number;
  evaluated_tickets: number;
  hit_tickets: number;
  hit_rate: number | null;
  stake_yen: number | null;
  payout_yen: number | null;
  return_rate: number | null;
  refund_unverified_tickets: number;
  refund_yen: null;
  refund_state: "unverified";
  exclusions: Record<string, number>;
};

type PerformanceGroup = {
  rule_version: { id: number; rule_key: string; version: number; title: string };
  status: "available" | "no_runs";
  counts: {
    considered_races: number;
    candidate_runs: number;
    selected_runs: number;
    duplicate_runs_excluded: number;
    ineligible_runs_excluded: number;
    invalidated_runs_excluded: number;
    invalidated_without_active_pre_race_run: number;
    candidate_races: number;
    selected_races: number;
    selected_snapshots: number;
    selected_runner_observations: number;
    attention_runners: number;
    dismissed_runners: number;
    undecidable_runners: number;
    selected_race_date_from: string | null;
    selected_race_date_to: string | null;
    selected_frozen_at_from: string | null;
    selected_frozen_at_to: string | null;
  };
  selected_runs: Array<{
    race_id: number;
    race_date: string;
    racecourse: string;
    race_number: number;
    judgement_run_id: number;
    input_snapshot_id: number;
    frozen_at: string;
    active_result_version_id: number | null;
    active_result_version_number: number | null;
  }>;
  result_exclusion_races: Record<string, number>;
  corrected_result_races: number;
  bet_types: { win: BetTypeReport; place: BetTypeReport };
};

type PerformanceReport = {
  filters: {
    race_date_from: string | null;
    race_date_to: string | null;
    frozen_at_from: string | null;
    frozen_at_to: string | null;
    rule_version_id: number | null;
  };
  groups: PerformanceGroup[];
  disclaimer: string;
};

type FilterState = {
  raceDateFrom: string;
  raceDateTo: string;
  frozenAtFrom: string;
  frozenAtTo: string;
  ruleVersionId: string;
};

const numberOf = (value: number | null) => value === null ? "—" : value.toLocaleString("ja-JP");
const yen = (value: number | null) => value === null ? "—" : `${value.toLocaleString("ja-JP")}円`;
const percent = (value: number | null) => value === null ? "—" : `${(value * 100).toFixed(2)}%`;

const EXCLUSION_LABELS: Record<string, string> = {
  result_pending: "結果待ち",
  result_source_unverified: "公式出典未確認",
  result_incomplete: "結果不完全",
  payout_unavailable: "払戻不明",
  place_market_unavailable: "このアプリでは複勝対象情報なし",
  refund_unverified: "取消・除外の返還未確認",
};

function exclusionSummary(exclusions: Record<string, number>) {
  return Object.entries(exclusions)
    .filter(([, count]) => count > 0)
    .map(([reason, count]) => `${EXCLUSION_LABELS[reason] ?? reason} ${count}件`)
    .join(" / ");
}

function BetSummary({ betType, report }: { betType: "win" | "place"; report: BetTypeReport }) {
  const label = betType === "win" ? "単勝" : "複勝";
  const unavailable = report.status === "market_unavailable";
  return (
    <section className="rule-performance-bet" aria-label={`${label}成績`}>
      <h4>{label}</h4>
      {unavailable && <p>このアプリの保存出走馬数では複勝対象情報がありません。</p>}
      <dl>
        <div><dt>注目ticket</dt><dd>{numberOf(report.candidate_tickets)}件</dd></div>
        <div><dt>的中率</dt><dd>{percent(report.hit_rate)}（{report.hit_tickets}/{report.evaluated_tickets}件）</dd></div>
        <div><dt>仮想投資</dt><dd>{yen(report.stake_yen)}</dd></div>
        <div><dt>公式払戻</dt><dd>{yen(report.payout_yen)}</dd></div>
        <div><dt>回収率</dt><dd>{percent(report.return_rate)}</dd></div>
      </dl>
      {report.refund_unverified_tickets > 0 && (
        <p>取消・除外の返還は未確認（{report.refund_unverified_tickets}件、金額未確認）。</p>
      )}
      {exclusionSummary(report.exclusions) && (
        <p>評価対象外: {exclusionSummary(report.exclusions)}</p>
      )}
    </section>
  );
}

function RulePerformancePanel() {
  const [filters, setFilters] = useState<FilterState>({
    raceDateFrom: "", raceDateTo: "", frozenAtFrom: "", frozenAtTo: "", ruleVersionId: "",
  });
  const [report, setReport] = useState<PerformanceReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function load(params: URLSearchParams) {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch(`/api/rule-performance${params.size ? `?${params.toString()}` : ""}`);
      const payload = await response.json() as PerformanceReport | { detail?: { message?: string } };
      if (!response.ok) {
        const detail = "detail" in payload ? payload.detail?.message : undefined;
        throw new Error(detail ?? "ルール成績を読み込めませんでした。");
      }
      setReport(payload as PerformanceReport);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "ルール成績を読み込めませんでした。");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void load(new URLSearchParams()); }, []);

  function applyFilters(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const params = new URLSearchParams();
    if (filters.raceDateFrom) params.set("race_date_from", filters.raceDateFrom);
    if (filters.raceDateTo) params.set("race_date_to", filters.raceDateTo);
    if (filters.frozenAtFrom) params.set("frozen_at_from", new Date(filters.frozenAtFrom).toISOString());
    if (filters.frozenAtTo) params.set("frozen_at_to", new Date(filters.frozenAtTo).toISOString());
    if (filters.ruleVersionId) params.set("rule_version_id", filters.ruleVersionId);
    void load(params);
  }

  return (
    <section className="panel rule-performance-panel" aria-labelledby="rule-performance-heading">
      <header className="panel-heading">
        <div><span className="eyebrow">保存済みルール判定</span><h2 id="rule-performance-heading">注目ルールの仮想成績</h2></div>
      </header>
      <form className="rule-performance-filters" onSubmit={applyFilters}>
        <label>開催日（開始）<input type="date" value={filters.raceDateFrom} onChange={(event) => setFilters({ ...filters, raceDateFrom: event.target.value })} /></label>
        <label>開催日（終了）<input type="date" value={filters.raceDateTo} onChange={(event) => setFilters({ ...filters, raceDateTo: event.target.value })} /></label>
        <label>固定日時（開始）<input type="datetime-local" value={filters.frozenAtFrom} onChange={(event) => setFilters({ ...filters, frozenAtFrom: event.target.value })} /></label>
        <label>固定日時（終了）<input type="datetime-local" value={filters.frozenAtTo} onChange={(event) => setFilters({ ...filters, frozenAtTo: event.target.value })} /></label>
        <label>ルール版
          <select value={filters.ruleVersionId} onChange={(event) => setFilters({ ...filters, ruleVersionId: event.target.value })}>
            <option value="">すべての版を別々に表示</option>
            {report?.groups.map((group) => (
              <option key={group.rule_version.id} value={group.rule_version.id}>
                {group.rule_version.title} v{group.rule_version.version}
              </option>
            ))}
          </select>
        </label>
        <button type="submit" disabled={loading}>{loading ? "読込中…" : "成績を表示"}</button>
      </form>
      {error && <p className="import-error" role="alert">{error}</p>}
      {report && report.groups.length === 0 && <p className="rule-performance-empty">対象ルール版がありません。</p>}
      {report?.groups.map((group) => (
        <section className="rule-performance-group" key={group.rule_version.id} aria-label={`${group.rule_version.title} v${group.rule_version.version}`}>
          <h3>{group.rule_version.title} v{group.rule_version.version}</h3>
          {group.status === "no_runs" ? (
            <>
              <p>選択条件に一致する保存済み事前判定はありません。</p>
              <dl className="rule-performance-counts">
                <div><dt>対象race / 適格候補run</dt><dd>{group.counts.considered_races}件 / {group.counts.candidate_runs}件</dd></div>
                <div><dt>発走後・対象外run</dt><dd>{group.counts.ineligible_runs_excluded}件</dd></div>
                <div><dt>無効化run</dt><dd>{group.counts.invalidated_runs_excluded}件</dd></div>
                <div><dt>旧事前runの無効化により採用できないrace</dt><dd>{group.counts.invalidated_without_active_pre_race_run}件</dd></div>
              </dl>
            </>
          ) : (
            <>
              <p className="rule-performance-period">
                集計開催日 {group.counts.selected_race_date_from ?? "—"} ～ {group.counts.selected_race_date_to ?? "—"}
                {" / "}固定日時 {group.counts.selected_frozen_at_from ?? "—"} ～ {group.counts.selected_frozen_at_to ?? "—"}
              </p>
              <dl className="rule-performance-counts">
                <div><dt>対象race・候補race</dt><dd>{group.counts.considered_races}・{group.counts.candidate_races}</dd></div>
                <div><dt>採用run</dt><dd>{group.counts.selected_runs}/{group.counts.candidate_runs}</dd></div>
                <div><dt>対象race・snapshot</dt><dd>{group.counts.selected_races}・{group.counts.selected_snapshots}</dd></div>
                <div><dt>判定対象頭数</dt><dd>{group.counts.selected_runner_observations}</dd></div>
                <div><dt>注目／見送り／判定不能</dt><dd>{group.counts.attention_runners}／{group.counts.dismissed_runners}／{group.counts.undecidable_runners}</dd></div>
                <div><dt>重複・発走後・無効化run</dt><dd>{group.counts.duplicate_runs_excluded}・{group.counts.ineligible_runs_excluded}・{group.counts.invalidated_runs_excluded}</dd></div>
                <div><dt>有効な事前runなし（旧run無効化）</dt><dd>{group.counts.invalidated_without_active_pre_race_run}</dd></div>
                <div><dt>訂正後の公式結果</dt><dd>{group.corrected_result_races} race</dd></div>
              </dl>
              <details className="rule-performance-run-details">
                <summary>採用した保存判定を確認（{group.selected_runs.length}件）</summary>
                <div className="rule-performance-run-table-wrap">
                  <table aria-label="選択した保存済み判定">
                    <thead><tr><th>開催日・競馬場</th><th>判定run</th><th>snapshot</th><th>固定日時（UTC）</th><th>有効結果version</th></tr></thead>
                    <tbody>{group.selected_runs.map((run) => (
                      <tr key={run.judgement_run_id}>
                        <td>{run.race_date} {run.racecourse} {run.race_number}R</td>
                        <td>{run.judgement_run_id}</td><td>{run.input_snapshot_id}</td><td>{run.frozen_at}</td>
                        <td>{run.active_result_version_id === null || run.active_result_version_number === null
                          ? "結果なし"
                          : `v${run.active_result_version_number} (#${run.active_result_version_id})`}</td>
                      </tr>
                    ))}</tbody>
                  </table>
                </div>
              </details>
              <div className="rule-performance-bets">
                <BetSummary betType="win" report={group.bet_types.win} />
                <BetSummary betType="place" report={group.bet_types.place} />
              </div>
              {Object.keys(group.result_exclusion_races).length > 0 && (
                <p className="rule-performance-exclusions">
                  結果状態（race数）: {exclusionSummary(group.result_exclusion_races)}
                </p>
              )}
            </>
          )}
        </section>
      ))}
      <p className="rule-performance-disclaimer">{report?.disclaimer ?? "保存済みの事前判定と公式払戻の記述集計です。"}</p>
    </section>
  );
}

export default RulePerformancePanel;
