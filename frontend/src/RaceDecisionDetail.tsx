import JstTimestamp from "./JstTimestamp";
import BettingPanel from "./BettingPanel";

export type DecisionRunner = {
  horse_number: number; horse_name: string; win_odds: number;
  place_odds_min: number; place_odds_max: number; market_rank: number | null;
  normalized_win_market_share: number | null;
  rule_judgement: "注目" | "見送り" | "判定不能";
  rule_reason: string; missing_reasons: string[];
};

export type DecisionView = {
  race_id: number;
  race: { racecourse: string; race_date: string; race_number: number; start_time: string;
    surface: string; distance_m: number; going: string; field_size: number };
  snapshot_id: number; judgement_id: number; rule_version_id: number;
  observed_at: string | null; received_at: string; judgement_as_of: string;
  judgement_frozen_at: string; attention_horse_count: number; judged_runner_count: number;
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

export default function RaceDecisionDetail({
  detail, dataState, backLabel, onBack, selectedHorseNumber, onNavigate,
}: Props) {
  const ordered = [...detail.runners].sort((left, right) =>
    (left.horse_number === selectedHorseNumber ? 0 : 1) - (right.horse_number === selectedHorseNumber ? 0 : 1)
    || (left.rule_judgement === "注目" ? 0 : 1) - (right.rule_judgement === "注目" ? 0 : 1)
    || left.horse_number - right.horse_number);

  return <section className="weekly-detail" aria-labelledby="weekly-detail-heading">
    <button type="button" className="back-button" aria-label={backLabel} onClick={onBack}>← {backLabel}</button>
    <div className="weekly-detail-header">
      <div><span>{detail.race.race_date} {detail.race.start_time}</span>
        <h2 id="weekly-detail-heading">{detail.race.racecourse} {detail.race.race_number}R</h2>
        <p>{detail.race.surface}{detail.race.distance_m}m / {detail.race.going} / {detail.race.field_size}頭</p>
      </div>
      <strong>注目 {detail.attention_horse_count}頭 / {detail.judged_runner_count}頭</strong>
    </div>
    <p className="detail-data-state">データ状態: {dataState}</p>
    <dl className="decision-times">
      <div><dt>オッズ観測</dt><dd><JstTimestamp value={detail.observed_at} unknown="観測時刻不明" /></dd></div>
      <div><dt>判定固定</dt><dd><JstTimestamp value={detail.judgement_frozen_at} /></dd></div>
      <div><dt>ルール</dt><dd>v{detail.rule_version_id}</dd></div>
    </dl>
    <div className="decision-runner-list" aria-label="注目馬と判定理由">
      {ordered.map((runner) => <article key={runner.horse_number}
        className={`decision-runner ${runner.rule_judgement === "注目" ? "is-attention" : ""} ${runner.horse_number === selectedHorseNumber ? "is-selected-horse" : ""}`}
        aria-label={runner.horse_number === selectedHorseNumber ? `${runner.horse_name} 選択中` : runner.horse_name}>
        <div><b>{runner.horse_number}</b><strong>{runner.horse_name}</strong><span className="decision-badge">{runner.rule_judgement}</span>
          {runner.horse_number === selectedHorseNumber && <span className="selected-horse-badge">一覧で選択</span>}</div>
        <p>{runner.rule_reason}</p>
        <dl><div><dt>市場順位</dt><dd>{runner.market_rank === null ? "—" : `${runner.market_rank}位`}</dd></div>
          <div><dt>単勝</dt><dd>{runner.win_odds.toFixed(1)}</dd></div>
          <div><dt>複勝</dt><dd>{runner.place_odds_min.toFixed(1)}–{runner.place_odds_max.toFixed(1)}</dd></div>
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
