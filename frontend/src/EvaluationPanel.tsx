import { useState } from "react";


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
  };
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
const percent = (value: number | null) => value === null ? "—" : `${(value * 100).toFixed(2)}%`;
const yen = (value: number | null) => value === null ? "—" : `¥${value.toLocaleString("ja-JP")}`;

function EvaluationPanel() {
  const [report, setReport] = useState<EvaluationReport | null>(null);
  const [filters, setFilters] = useState<FilterState>(EMPTY_FILTERS);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const load = async (selected: FilterState = filters) => {
    setLoading(true);
    setError("");
    const params = new URLSearchParams();
    if (selected.modelOption && report) {
      const model = report.filter_options.models[Number(selected.modelOption)];
      if (model) {
        params.set("model_identifier", model.identifier);
        params.set("model_version", model.version);
      }
    }
    if (selected.tagOption && report) {
      const tag = report.filter_options.tags[Number(selected.tagOption)];
      if (tag) {
        params.set("tag_rule_key", tag.rule_key);
        params.set("tag_version", String(tag.version));
      }
    }
    const scalarFilters: Array<[string, string]> = [
      ["racecourse", selected.racecourse], ["bet_type", selected.betType],
      ["odds_min", selected.oddsMin], ["odds_max", selected.oddsMax],
      ["popularity_min", selected.popularityMin], ["popularity_max", selected.popularityMax],
    ];
    for (const [key, value] of scalarFilters) if (value) params.set(key, value);
    if (selected.frozenFrom) params.set("prediction_frozen_from", new Date(selected.frozenFrom).toISOString());
    if (selected.frozenTo) params.set("prediction_frozen_to", new Date(selected.frozenTo).toISOString());
    try {
      const response = await fetch(`/api/evaluation${params.size ? `?${params.toString()}` : ""}`);
      if (!response.ok) {
        const payload = await response.json() as { detail?: { message?: string } | string };
        const detail = payload.detail;
        throw new Error(typeof detail === "object" && detail?.message
          ? detail.message : typeof detail === "string" ? detail : `HTTP ${response.status}`);
      }
      setReport(await response.json() as EvaluationReport);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "成績を読み込めませんでした。");
    } finally {
      setLoading(false);
    }
  };

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
      {error && <p className="message error" role="alert">{error}</p>}
      {report && (
        <div className="evaluation-content">
          <div className="evaluation-filters">
            <label>モデル版<select value={filters.modelOption} onChange={(event) => update("modelOption", event.target.value)}>
              <option value="">すべて</option>
              {report.filter_options.models.map((model, index) => (
                <option key={`${model.identifier}-${model.version}`} value={String(index)}>
                  {model.identifier} / {model.version}
                </option>
              ))}
            </select></label>
            <label>タグ版<select value={filters.tagOption} onChange={(event) => update("tagOption", event.target.value)}>
              <option value="">すべて</option>
              {report.filter_options.tags.map((tag, index) => (
                <option key={`${tag.rule_key}-${tag.version}`} value={String(index)}>
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
          </div>

          <div className="evaluation-summary">
            <article><span>Brierスコア</span><strong>{report.calibration.brier_score?.toFixed(4) ?? "—"}</strong></article>
            <article><span>対象</span><strong>公式評価対象 {report.calibration.eligible_prediction_runs}予測 / {report.calibration.runner_count}頭</strong></article>
            <article><span>除外</span><strong>公式評価対象外 {report.calibration.excluded_prediction_runs}予測</strong></article>
          </div>
          <p className="evaluation-note">市場基準は比較用の投票シェアであり、利益優位性を示す予測ではありません。複勝のオッズ帯は購入時に保存した下限オッズで判定します。</p>

          <div className="table-wrap">
            <table aria-label="確率帯別の校正">
              <thead><tr><th>確率帯</th><th>件数</th><th>平均予測確率</th><th>実的中率</th><th>標本</th></tr></thead>
              <tbody>{report.calibration.bands.filter((band) => band.count > 0).map((band) => (
                <tr key={band.lower_bound}>
                  <td>{Math.round(band.lower_bound * 100)}–{Math.round(band.upper_bound * 100)}%未満</td>
                  <td>{band.count}</td><td>{percent(band.average_predicted_probability)}</td>
                  <td>{percent(band.actual_win_rate)}</td><td>{band.small_sample ? "少数標本" : "十分"}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
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
