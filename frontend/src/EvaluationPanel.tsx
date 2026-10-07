import { useViewState } from "./ViewState";
import { useEffect, useRef, useState } from "react";


type Totals = {
  stake_yen: number;
  payout_yen: number;
  refund_yen: number;
  profit_yen: number | null;
  return_rate: number | null;
};

type EvaluationReport = {
  filters: Record<string, string | number | null>;
  filter_options: {
    models: Array<{ identifier: string; version: string }>;
    tags: Array<{ rule_key: string; version: number }>;
    racecourses: string[];
    bet_types: Array<"win" | "place">;
  };
  calibration_status: "no_groups" | "single_group" | "multiple_groups";
  calibration_reason: string | null;
  calibration: {
    eligible_prediction_runs: number;
    excluded_prediction_runs: number;
    runner_count: number;
    brier_score: number | null;
    bands: Array<{
      lower_bound: number;
      upper_bound: number;
      count: number;
      average_predicted_probability: number | null;
      actual_win_rate: number | null;
      small_sample: boolean;
    }>;
  } | null;
  calibration_groups: Array<{
    model_identifier: string;
    model_version: string;
    bet_type: "win" | "place";
    eligible_prediction_runs: number;
    excluded_prediction_runs: number;
    raw_unique_run_count: number;
    eligible_candidate_run_count: number;
    capability_qualified_run_count: number;
    selected_prediction_run_count: number;
    ineligible_excluded_run_count: number;
    unsupported_bet_excluded_run_count: number;
    duplicate_excluded_run_count: number;
    race_date_from: string | null;
    race_date_to: string | null;
    prediction_frozen_from: string | null;
    prediction_frozen_to: string | null;
    distinct_race_count: number;
    distinct_observation_count: number;
    runner_observation_count: number;
    brier_score: number | null;
    bands: Array<{
      lower_bound: number;
      upper_bound: number;
      count: number;
      average_predicted_probability: number | null;
      actual_win_rate: number | null;
    }>;
    market_comparison_status: "available" | "no_baseline" | "no_common_observations" | "not_applicable";
    market_comparison_reason: string | null;
    market_comparisons: Array<{
      baseline_version: string | null;
      status: "available" | "no_baseline" | "no_common_observations" | "not_applicable";
      reason: string | null;
      model_brier_score: number | null;
      market_brier_score: number | null;
      brier_difference: number | null;
      matched_runner_observation_count: number;
      matched_race_count: number;
      matched_observation_count: number;
      raw_unique_run_count: number;
      eligible_candidate_run_count: number;
      capability_qualified_run_count: number;
      selected_run_count: number;
      ineligible_excluded_run_count: number;
      unsupported_bet_excluded_run_count: number;
      duplicate_excluded_run_count: number;
    }>;
    uncertainty_status: "not_estimated";
    uncertainty_interval: null;
    uncertainty_reason: string;
  }>;
  returns: { candidate: Totals; discretionary: Totals };
};

type FilterState = {
  modelOption: string;
  tagOption: string;
  racecourse: string;
  betType: string;
  oddsMin: string;
  oddsMax: string;
  popularityMin: string;
  popularityMax: string;
  frozenFrom: string;
  frozenTo: string;
};

const EMPTY_FILTERS: FilterState = {
  modelOption: "", tagOption: "", racecourse: "", betType: "",
  oddsMin: "", oddsMax: "", popularityMin: "", popularityMax: "",
  frozenFrom: "", frozenTo: "",
};
const optionId = (identifier: string, version: string | number) => JSON.stringify([identifier, String(version)]);
const filterLabels: Record<keyof FilterState, string> = {
  modelOption: "モデル版", tagOption: "タグ版", racecourse: "競馬場", betType: "券種",
  oddsMin: "オッズ下限", oddsMax: "オッズ上限", popularityMin: "人気下限", popularityMax: "人気上限",
  frozenFrom: "予測固定時刻（開始）", frozenTo: "予測固定時刻（終了）",
};
const describeFilters = (selected: FilterState) => Object.entries(selected)
  .filter(([, value]) => value !== "")
  .map(([key, value]) => {
    const display = key === "modelOption" || key === "tagOption" ? (JSON.parse(value) as string[]).join(" / ")
      : key === "betType" ? (value === "win" ? "単勝" : "複勝") : value;
    return `${filterLabels[key as keyof FilterState]}: ${display}`;
  }).join("、") || "すべて";
const percent = (value: number | null) => value === null ? "—" : `${(value * 100).toFixed(2)}%`;
const yen = (value: number | null) => value === null ? "—" : `¥${value.toLocaleString("ja-JP")}`;
const span = (from: string | null, to: string | null) => from === null || to === null ? "—" : from === to ? from : `${from} ～ ${to}`;

function EvaluationPanel() {
  const requestVersion = useRef(0);
  const [savedFilters, setSavedFilters] = useViewState<string | null>("evaluation", null);
  const [result, setResult] = useState<{ report: EvaluationReport; applied: FilterState } | null>(null);
  const report = result?.report;

  const [filters, setFilters] = useState<FilterState>(EMPTY_FILTERS);
  const unapplied = result && JSON.stringify(filters) !== JSON.stringify(result.applied);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const load = async (selected: FilterState = filters, persist = true) => {
    const request = ++requestVersion.current;
    setLoading(true);
    setError("");
    try {
      const params = new URLSearchParams();
      if (selected.modelOption) {
        const [identifier, version] = JSON.parse(selected.modelOption) as string[];
        params.set("model_identifier", identifier);
        params.set("model_version", version);
      }
      if (selected.tagOption) {
        const [ruleKey, version] = JSON.parse(selected.tagOption) as string[];
        params.set("tag_rule_key", ruleKey);
        params.set("tag_version", version);
      }
      const scalarFilters: Array<[string, string]> = [
        ["racecourse", selected.racecourse], ["bet_type", selected.betType],
        ["odds_min", selected.oddsMin], ["odds_max", selected.oddsMax],
        ["popularity_min", selected.popularityMin], ["popularity_max", selected.popularityMax],
      ];
      for (const [key, value] of scalarFilters) if (value) params.set(key, value);
      if (selected.frozenFrom) params.set("prediction_frozen_from", new Date(selected.frozenFrom).toISOString());
      if (selected.frozenTo) params.set("prediction_frozen_to", new Date(selected.frozenTo).toISOString());
      const response = await fetch(`/api/evaluation${params.size ? `?${params.toString()}` : ""}`);
      if (!response.ok) {
        const payload = await response.json() as { detail?: { message?: string } | string };
        const detail = payload.detail;
        throw new Error(typeof detail === "object" && detail?.message
          ? detail.message : typeof detail === "string" ? detail : `HTTP ${response.status}`);
      }
      const report = await response.json() as EvaluationReport;
      if (request !== requestVersion.current) return;
      setResult({ report, applied: { ...selected } });
      if (persist) setSavedFilters(JSON.stringify(selected));
    } catch (caught) {
      if (request !== requestVersion.current) return;
      setError(caught instanceof Error ? caught.message : "成績を読み込めませんでした。");
    } finally {
      if (request === requestVersion.current) setLoading(false);
    }
  };

  useEffect(() => () => { requestVersion.current++; }, []);

  useEffect(() => {
    requestVersion.current++;
    setLoading(false);
    setError("");
    if (savedFilters === null) { setResult(null); setFilters(EMPTY_FILTERS); return; }
    // A successful local apply already has the same result; history/reload needs GET only.
    if (result && JSON.stringify(result.applied) === savedFilters) return;
    try {
      const raw = JSON.parse(savedFilters);
      if (!raw || typeof raw !== "object" || Object.keys(EMPTY_FILTERS).some((key) => typeof raw[key] !== "string")) throw new Error();
      const restored = Object.fromEntries(Object.keys(EMPTY_FILTERS).map((key) => [key, raw[key]])) as FilterState;
      for (const id of [restored.modelOption, restored.tagOption]) {
        if (id) { const pair = JSON.parse(id); if (!Array.isArray(pair) || pair.length !== 2 || pair.some((part) => typeof part !== "string")) throw new Error(); }
      }
      setFilters(restored);
      void load(restored, false);
    } catch { setResult(null); setLoading(false); setError("URLの成績条件が不正です。条件をリセットしてください。"); }
  }, [savedFilters]);

  const update = (key: keyof FilterState, value: string) => {
    setFilters((current) => ({ ...current, [key]: value }));
  };

  const totalsCard = (title: string, totals: Totals) => (
    <article aria-label={title}>
      <strong>{title}</strong>
      <span>購入額 {yen(totals.stake_yen)}</span>
      <span>払戻 {yen(totals.payout_yen)}</span>
      <span>返還 {yen(totals.refund_yen)}</span>
      <span>損益 {yen(totals.profit_yen)}</span>
      <span>回収率 {percent(totals.return_rate)}</span>
    </article>
  );

  return (
    <section className="panel evaluation-panel" aria-labelledby="evaluation-heading">
      <div className="panel-heading">
        <div>
          <span className="section-number">05</span>
          <h2 id="evaluation-heading">予測精度と収支</h2>
        </div>
        <span className="local-badge">確定済みデータ</span>
      </div>
      {!report && (
        <div className="evaluation-empty">
          <p>発走前に固定した予測と確定結果から、校正と購入収支を集計します。</p>
          <button type="button" disabled={loading} onClick={() => void load(EMPTY_FILTERS)}>
            {loading ? "集計中…" : "成績を表示"}
          </button>
        </div>
      )}
      {error && <p className="message error" role="alert">{report ? `更新失敗：${error} 表示中の結果は適用済み条件の集計です。` : error}{!report && <button type="button" onClick={() => { setSavedFilters(null); setError(""); }}>条件をリセット</button>}</p>}
      {report && (
        <div className="evaluation-content">
          <div className="evaluation-filters">
            <label>モデル版<select value={filters.modelOption} onChange={(event) => update("modelOption", event.target.value)}>
              <option value="">すべて</option>
              {report.filter_options.models.map((model) => (
                <option key={optionId(model.identifier, model.version)} value={optionId(model.identifier, model.version)}>
                  {model.identifier} / {model.version}
                </option>
              ))}
            </select></label>
            <label>タグ版<select value={filters.tagOption} onChange={(event) => update("tagOption", event.target.value)}>
              <option value="">すべて</option>
              {report.filter_options.tags.map((tag) => (
                <option key={optionId(tag.rule_key, tag.version)} value={optionId(tag.rule_key, tag.version)}>
                  {tag.rule_key} / v{tag.version}
                </option>
              ))}
            </select></label>
            <label>競馬場<select value={filters.racecourse} onChange={(event) => update("racecourse", event.target.value)}>
              <option value="">すべて</option>
              {report.filter_options.racecourses.map((course) => <option key={course}>{course}</option>)}
            </select></label>
            <label>券種<select value={filters.betType} onChange={(event) => update("betType", event.target.value)}>
              <option value="">すべて</option>
              <option value="win">単勝</option><option value="place">複勝</option>
            </select></label>
            <label>オッズ下限<input type="number" min="0.1" step="0.1" value={filters.oddsMin} onChange={(event) => update("oddsMin", event.target.value)} /></label>
            <label>オッズ上限<input type="number" min="0.1" step="0.1" value={filters.oddsMax} onChange={(event) => update("oddsMax", event.target.value)} /></label>
            <label>人気下限<input type="number" min="1" step="1" value={filters.popularityMin} onChange={(event) => update("popularityMin", event.target.value)} /></label>
            <label>人気上限<input type="number" min="1" step="1" value={filters.popularityMax} onChange={(event) => update("popularityMax", event.target.value)} /></label>
            <label>予測固定時刻（開始）<input type="datetime-local" value={filters.frozenFrom} onChange={(event) => update("frozenFrom", event.target.value)} /></label>
            <label>予測固定時刻（終了）<input type="datetime-local" value={filters.frozenTo} onChange={(event) => update("frozenTo", event.target.value)} /></label>
            <button type="button" disabled={loading} onClick={() => void load()}>{loading ? "集計中…" : "条件を適用"}</button>
            <button type="button" disabled={loading} onClick={() => { setFilters(EMPTY_FILTERS); void load(EMPTY_FILTERS); }}>条件をリセット</button>
          </div>
          {unapplied && <p role="status">未適用の変更があります。</p>}
          {loading && <p role="status">再集計中です。表示中の結果は適用済み条件の集計です。</p>}
          <p aria-label="適用済み条件">適用済み条件：{result && describeFilters(result.applied)}</p>
          {report.calibration_reason && <p role="status">{report.calibration_reason}</p>}
          {report.calibration_groups.map((group) => (
            <section className="evaluation-group" aria-label={`${group.model_identifier} ${group.model_version} ${group.bet_type === "win" ? "単勝" : "複勝"}`} key={`${group.model_identifier}-${group.model_version}-${group.bet_type}`}>
              <h3>{group.model_identifier} / {group.model_version} / {group.bet_type === "win" ? "単勝" : "複勝"}</h3>
              <div className="evaluation-summary">
                <article><span>Brierスコア</span><strong>{group.brier_score?.toFixed(4) ?? "—"}</strong></article>
                <article><span>採用run</span><strong>{group.selected_prediction_run_count}</strong></article>
                <article><span>run・馬の観測数</span><strong>{group.runner_observation_count}</strong></article>
                <article><span>対象race数</span><strong>{group.distinct_race_count}</strong></article>
                <article><span>race/snapshot観測数</span><strong>{group.distinct_observation_count}</strong></article>
              </div>
              <p>対象race日: {span(group.race_date_from, group.race_date_to)} / 予測固定時刻: {span(group.prediction_frozen_from, group.prediction_frozen_to)}</p>
              <p>run総数 {group.raw_unique_run_count} / 公式対象候補 {group.eligible_candidate_run_count} / 券種対応候補 {group.capability_qualified_run_count} / 採用 {group.selected_prediction_run_count} / 除外（公式対象外 {group.ineligible_excluded_run_count}・券種非対応 {group.unsupported_bet_excluded_run_count}・重複 {group.duplicate_excluded_run_count}）</p>
              <p>不確実性: 推定なし。{group.uncertainty_reason}</p>
              <section aria-label="市場基準との比較">
                <h4>市場基準との共通標本比較</h4>
                {group.market_comparison_reason && <p role="status">{group.market_comparison_reason}</p>}
                {group.market_comparisons.map((comparison) => (
                  <article aria-label={`市場基準版 ${comparison.baseline_version ?? "なし"}`} key={comparison.baseline_version ?? comparison.status}>
                    <h5>市場基準版 {comparison.baseline_version ?? "なし"}</h5>
                    {comparison.reason && <p>{comparison.reason}</p>}
                    <p>共通run・馬の観測 {comparison.matched_runner_observation_count} / race {comparison.matched_race_count} / race-snapshot {comparison.matched_observation_count}</p>
                    <p>モデル Brier {comparison.model_brier_score?.toFixed(4) ?? "—"} / 市場 Brier {comparison.market_brier_score?.toFixed(4) ?? "—"} / 差（モデル−市場） {comparison.brier_difference?.toFixed(4) ?? "—"}</p>
                  </article>
                ))}
              </section>
              <div className="table-wrap">
                <table aria-label={`確率帯別の校正 ${group.model_identifier} ${group.model_version} ${group.bet_type}`}>
                  <thead><tr><th>確率帯</th><th>run・馬の観測数</th><th>平均予測確率</th><th>実的中率</th></tr></thead>
                  <tbody>{group.bands.filter((band) => band.count > 0).map((band) => (
                    <tr key={band.lower_bound}>
                      <td>{Math.round(band.lower_bound * 100)}–{Math.round(band.upper_bound * 100)}%未満</td>
                      <td>{band.count}</td><td>{percent(band.average_predicted_probability)}</td>
                      <td>{percent(band.actual_win_rate)}</td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
            </section>
          ))}
          <p className="evaluation-note">市場基準は比較用の投票シェアであり、利益優位性を示す予測ではありません。複勝のオッズ帯は購入時に保存した下限オッズで判定します。</p>
          <section className="evaluation-returns" aria-label="購入区分別収支">
            {totalsCard("候補内実購入", report.returns.candidate)}
            {totalsCard("候補外裁量", report.returns.discretionary)}
          </section>
        </div>
      )}
    </section>
  );
}

export default EvaluationPanel;
