import { useState } from "react";

import JstTimestamp from "./JstTimestamp";
import BettingPanel from "./BettingPanel";
import RuleConditionList, { type RuleCondition } from "./RuleConditionList";

export type DecisionRunner = {
  horse_number: number; horse_name: string; gate: number | null; age: number | null;
  sex: string | null; assigned_weight: number | null; status: string | null;
  win_odds: number | null;
  place_odds_min: number | null; place_odds_max: number | null; market_rank: number | null;
  normalized_win_market_share: number | null;
  rule_judgement: "注目" | "見送り" | "判定不能";
  rule_reason: string; rule_conditions?: RuleCondition[]; missing_reasons: string[];
};

export type DecisionView = {
  race_id: number;
  race: { racecourse: string; race_date: string; race_number: number; start_time: string;
    surface: string; distance_m: number; going: string; field_size: number };
  snapshot_id: number; judgement_id: number; rule_version_id: number;
  observed_at: string | null; received_at: string; judgement_as_of: string;
  judgement_frozen_at: string; attention_horse_count: number; judged_runner_count: number;
  attention_level: "none" | "low" | "medium" | "high" | null;
  runners: DecisionRunner[]; disclaimer: string;
};

type Props = {
  detail: DecisionView;
  dataState: string;
  backLabel: string;
  onBack: () => void;
  selectedHorseNumber?: number;
  onNavigate?: (area: "evaluation" | "import" | "settings", raceId?: number) => void;
};

const percent = (value: number | null) => value === null ? "—" : `${Math.round(value * 100)}%`;
const attentionLevelLabels = { none: "なし", low: "低", medium: "中", high: "高" } as const;
type ComparisonOrder = "number" | "popularity";

const compareByNumber = (left: DecisionRunner, right: DecisionRunner) => left.horse_number - right.horse_number;
const compareByPopularity = (left: DecisionRunner, right: DecisionRunner) => {
  if (left.market_rank === null && right.market_rank !== null) return 1;
  if (left.market_rank !== null && right.market_rank === null) return -1;
  return (left.market_rank ?? 0) - (right.market_rank ?? 0) || compareByNumber(left, right);
};
const unknown = (value: string | number | null) => value === null ? "不明" : String(value);
const winOdds = (runner: DecisionRunner) => runner.win_odds === null ? "不明" : runner.win_odds.toFixed(1);
const placeOdds = (runner: DecisionRunner) => runner.place_odds_min === null || runner.place_odds_max === null
  ? "不明" : `${runner.place_odds_min.toFixed(1)}–${runner.place_odds_max.toFixed(1)}`;

const comparisonRows: Array<{ label: string; value: (runner: DecisionRunner) => string }> = [
  { label: "馬番", value: (runner) => `${runner.horse_number}番` },
  { label: "馬名", value: (runner) => runner.horse_name },
  { label: "枠", value: (runner) => unknown(runner.gate) },
  { label: "年齢", value: (runner) => runner.age === null ? "不明" : `${runner.age}歳` },
  { label: "性別", value: (runner) => unknown(runner.sex) },
  { label: "斤量", value: (runner) => runner.assigned_weight === null ? "不明" : `${runner.assigned_weight.toFixed(1)}kg` },
  { label: "単勝", value: winOdds },
  { label: "複勝", value: placeOdds },
  { label: "市場順位（保存値）", value: (runner) => runner.market_rank === null ? "不明" : `${runner.market_rank}位` },
  { label: "判定", value: (runner) => runner.rule_judgement },
  { label: "判定理由", value: (runner) => runner.rule_reason },
  { label: "登録状態", value: (runner) => unknown(runner.status) },
];

export default function RaceDecisionDetail(props: Props) {
  const { detail } = props;
  const comparisonScope = `${detail.race_id}:${detail.snapshot_id}:${detail.judgement_id}`;
  return <RaceDecisionDetailView key={comparisonScope} {...props} />;
}

function RaceDecisionDetailView({
  detail, dataState, backLabel, onBack, selectedHorseNumber, onNavigate,
}: Props) {
  const [selectedHorseNumbers, setSelectedHorseNumbers] = useState<number[]>([]);
  const [comparisonOrder, setComparisonOrder] = useState<ComparisonOrder>("number");
  const comparisonSorter = comparisonOrder === "number" ? compareByNumber : compareByPopularity;
  const orderedForComparison = [...detail.runners].sort(comparisonSorter);
  const selectedRunners = orderedForComparison.filter((runner) => selectedHorseNumbers.includes(runner.horse_number));
  const toggleComparison = (horseNumber: number) => {
    setSelectedHorseNumbers((current) => {
      return current.includes(horseNumber)
        ? current.filter((number) => number !== horseNumber)
        : [...current, horseNumber];
    });
  };
  const ordered = [...detail.runners].sort((left, right) =>
    (left.horse_number === selectedHorseNumber ? 0 : 1) - (right.horse_number === selectedHorseNumber ? 0 : 1)
    || (left.rule_judgement === "注目" ? 0 : 1) - (right.rule_judgement === "注目" ? 0 : 1)
    || left.horse_number - right.horse_number);
  const attentionSummary = detail.judged_runner_count === 0
    ? "判定対象なし（0頭）"
    : `注目度: ${detail.attention_level === null ? "段階なし" : attentionLevelLabels[detail.attention_level]} — 注目 ${detail.attention_horse_count}頭 / 判定対象 ${detail.judged_runner_count}頭`;

  return <section className="weekly-detail" aria-labelledby="weekly-detail-heading">
    <button type="button" className="back-button" aria-label={backLabel} onClick={onBack}>← {backLabel}</button>
    <div className="weekly-detail-header">
      <div><span>{detail.race.race_date} {detail.race.start_time}</span>
        <h2 id="weekly-detail-heading">{detail.race.racecourse} {detail.race.race_number}R</h2>
        <p>{detail.race.surface}{detail.race.distance_m}m / {detail.race.going} / {detail.race.field_size}頭</p>
      </div>
      <div className="decision-attention-summary"
        aria-label={`詳細で選択中の保存済み判定 ${attentionSummary}`}>
        <span>詳細で選択中の保存済み判定</span>
        <strong>{attentionSummary}</strong>
      </div>
    </div>
    <p className="detail-data-state">データ状態: {dataState}</p>
    <dl className="decision-times">
      <div><dt>オッズ観測</dt><dd><JstTimestamp value={detail.observed_at} unknown="観測時刻不明" /></dd></div>
      <div><dt>受信時刻</dt><dd><JstTimestamp value={detail.received_at} /></dd></div>
      <div><dt>判定固定</dt><dd><JstTimestamp value={detail.judgement_frozen_at} /></dd></div>
      <div><dt>ルール</dt><dd>v{detail.rule_version_id}</dd></div>
    </dl>
    <section className="runner-comparison" aria-labelledby="runner-comparison-heading">
      <div className="runner-comparison-heading">
        <div><h3 id="runner-comparison-heading">出走馬を比較</h3>
          <p>レースID {detail.race_id} / 保存オッズ記録（snapshot {detail.snapshot_id}）/ 固定判定 {detail.judgement_id}</p>
          <p>市場順位は選択中snapshotの保存値です。選択馬だけで順位を再計算しません。</p>
        </div>
        <label>比較の順序
          <select aria-label="比較の順序" value={comparisonOrder}
            onChange={(event) => setComparisonOrder(event.currentTarget.value as ComparisonOrder)}>
            <option value="number">馬番順</option>
            <option value="popularity">人気順（保存済み市場順位）</option>
          </select>
        </label>
      </div>
      <fieldset className="comparison-runner-choices">
        <legend>比較する馬を選択</legend>
        {orderedForComparison.map((runner) => <label key={runner.horse_number}>
          <input type="checkbox" checked={selectedHorseNumbers.includes(runner.horse_number)}
            aria-label={`${runner.horse_number}番 ${runner.horse_name}を比較する`}
            onChange={() => toggleComparison(runner.horse_number)} />
          <span>{runner.horse_number}番 {runner.horse_name}</span>
          {runner.status !== null && runner.status !== "出走" && <span className="comparison-runner-status">{runner.status}</span>}
          {runner.market_rank === null ? <small>市場順位不明</small> : <small>市場 {runner.market_rank}位</small>}
        </label>)}
      </fieldset>
      {selectedRunners.length === 0
        ? <p className="comparison-empty">比較する馬を選択してください</p>
        : <>
          <div className="comparison-table-scroll desktop-only">
            <table className="comparison-table" aria-label="選択馬の比較">
              <thead><tr><th scope="col">項目</th>{selectedRunners.map((runner) =>
                <th scope="col" key={runner.horse_number}>{runner.horse_number}番 {runner.horse_name}</th>)}</tr></thead>
              <tbody>{comparisonRows.map((row) => <tr key={row.label}>
                <th scope="row">{row.label}</th>{selectedRunners.map((runner) =>
                  <td key={runner.horse_number}>{row.value(runner)}</td>)}
              </tr>)}</tbody>
            </table>
          </div>
          <div className="comparison-mobile-list mobile-only" aria-label="選択馬の比較">
            {selectedRunners.map((runner) => <article key={runner.horse_number}>
              <h4>{runner.horse_number}番 {runner.horse_name}</h4>
              <dl>{comparisonRows.filter((row) => row.label !== "馬番" && row.label !== "馬名")
                .map((row) => <div key={row.label}><dt>{row.label}</dt><dd>{row.value(runner)}</dd></div>)}</dl>
            </article>)}
          </div>
        </>}
    </section>
    <div className="decision-runner-list" aria-label="注目馬と判定理由">
      {ordered.map((runner) => <article key={runner.horse_number}
        className={`decision-runner ${runner.rule_judgement === "注目" ? "is-attention" : ""} ${runner.horse_number === selectedHorseNumber ? "is-selected-horse" : ""}`}
        aria-label={runner.horse_number === selectedHorseNumber ? `${runner.horse_name} 選択中` : runner.horse_name}>
        <div><b>{runner.horse_number}</b><strong>{runner.horse_name}</strong><span className="decision-badge">{runner.rule_judgement}</span>
          {runner.horse_number === selectedHorseNumber && <span className="selected-horse-badge">一覧で選択</span>}</div>
        {runner.rule_conditions && runner.rule_conditions.length > 0
          ? <RuleConditionList conditions={runner.rule_conditions} />
          : <p>{runner.rule_reason}</p>}
        <dl><div><dt>市場順位</dt><dd>{runner.market_rank === null ? "—" : `${runner.market_rank}位`}</dd></div>
          <div><dt>単勝</dt><dd>{runner.win_odds === null ? "—" : runner.win_odds.toFixed(1)}</dd></div>
          <div><dt>複勝</dt><dd>{runner.place_odds_min === null || runner.place_odds_max === null
            ? "—" : `${runner.place_odds_min.toFixed(1)}–${runner.place_odds_max.toFixed(1)}`}</dd></div>
          <div><dt>市場シェア</dt><dd>{percent(runner.normalized_win_market_share)}</dd></div></dl>
      </article>)}
    </div>
    <aside className="attention-disclaimer">{detail.disclaimer}</aside>
    <BettingPanel raceId={detail.race_id} runners={detail.runners.map((runner) => ({
      horse_number: runner.horse_number, horse_name: runner.horse_name,
    }))} />
    <aside className="detail-next"><strong>履歴・メンテナンス</strong><p>判定履歴、訂正、バックアップを各管理画面で確認できます。</p>
      <div className="detail-next-actions">
        <button type="button" onClick={() => onNavigate?.("evaluation")}>成績・検証へ</button>
        <button type="button" onClick={() => onNavigate?.("import", detail.race_id)}>データ取込・訂正へ</button>
        <button type="button" onClick={() => onNavigate?.("settings")}>設定・バックアップへ</button>
      </div>
    </aside>
  </section>;
}
