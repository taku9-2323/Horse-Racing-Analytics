import { formatJst } from "./JstTimestamp";
import JstTimestamp from "./JstTimestamp";
import { useState } from "react";

type Option = { id: number; observed_at?: string | null; title?: string; version?: number };
type Row = { horse_number: number; horse_name: string; market_rank: number | null; normalized_win_market_share: number | null; market_reason: string; rule_judgement: string | null; rule_reason: string; missing_reasons: string[] };
type Comparison = { state: "available" | "odds_not_registered" | "judgement_not_generated" | "rule_version_mismatch"; next_action: string | null; observed_at: string | null; received_at: string | null; fixed_state: "not_generated" | "fixed" | "invalidated" | "post_start"; official_pre_race_eligible: boolean; rows: Row[]; disclaimer: string };

const fixedLabel = { not_generated: "未固定", fixed: "固定済み", invalidated: "無効化済み", post_start: "発走後作成" } as const;

export default function MarketRuleComparisonPanel({ raceId }: { raceId: number }) {
  const [snapshots, setSnapshots] = useState<Option[]>([]);
  const [rules, setRules] = useState<Option[]>([]);
  const [snapshotId, setSnapshotId] = useState<number | null>(null);
  const [ruleId, setRuleId] = useState<number | null>(null);
  const [comparison, setComparison] = useState<Comparison | null>(null);
  const [error, setError] = useState("");

  const open = async () => {
    try {
      const [snapshotResponse, ruleResponse] = await Promise.all([fetch(`/api/races/${raceId}/odds-snapshots`), fetch("/api/rule-versions")]);
      if (!snapshotResponse.ok || !ruleResponse.ok) throw new Error();
      const loadedSnapshots = await snapshotResponse.json() as Option[];
      const loadedRules = await ruleResponse.json() as Option[];
      setSnapshots(loadedSnapshots); setRules(loadedRules);
      setSnapshotId(loadedSnapshots.at(-1)?.id ?? null); setRuleId(loadedRules.at(-1)?.id ?? null);
    } catch { setError("比較条件を読み込めませんでした。"); }
  };

  const compare = async () => {
    if (snapshotId === null || ruleId === null) return;
    const response = await fetch(`/api/races/${raceId}/market-rule-comparison?snapshot_id=${snapshotId}&rule_version_id=${ruleId}`);
    if (!response.ok) { setError("比較を読み込めませんでした。"); return; }
    setComparison(await response.json() as Comparison);
  };

  return <section className="workflow-section" aria-labelledby="market-rule-comparison-heading">
    <h4 id="market-rule-comparison-heading">市場順位と固定ルール判定の比較</h4>
    {rules.length === 0 && snapshots.length === 0 && <button type="button" onClick={() => void open()}>比較を開く</button>}
    {(rules.length > 0 || snapshots.length > 0) && <div className="snapshot-form">
      <label>比較オッズ時点 <select value={snapshotId ?? ""} onChange={(event) => { setSnapshotId(Number(event.target.value)); setComparison(null); }}>{snapshots.map((item) => <option title={item.observed_at ?? undefined} key={item.id} value={item.id}>#{item.id} / {formatJst(item.observed_at, "観測時刻不明")}</option>)}</select></label>
      <label>比較ルール版 <select value={ruleId ?? ""} onChange={(event) => { setRuleId(Number(event.target.value)); setComparison(null); }}>{rules.map((item) => <option key={item.id} value={item.id}>{item.title} v{item.version}</option>)}</select></label>
      <button type="button" disabled={snapshotId === null || ruleId === null} onClick={() => void compare()}>同じ時点で比較</button>
    </div>}
    {error && <p role="alert">{error}</p>}
    {comparison && <>
      <p><b>{fixedLabel[comparison.fixed_state]}</b>{comparison.official_pre_race_eligible ? " / 公式な事前判定" : ""} / 観測 <JstTimestamp value={comparison.observed_at} unknown="観測時刻不明" /> / 受付 <JstTimestamp value={comparison.received_at} /></p>
      {comparison.next_action && <p role="status">{comparison.next_action}</p>}
      {comparison.rows.length > 0 && <div className="table-wrap"><table><thead><tr><th>馬番・馬名</th><th>市場順位・シェア</th><th>市場の根拠</th><th>ルール判定</th><th>判定理由・欠損</th></tr></thead><tbody>
        {comparison.rows.map((row) => <tr key={row.horse_number}><td>{row.horse_number} {row.horse_name}</td><td>{row.market_rank === null ? "順位なし" : `${row.market_rank}位 / ${((row.normalized_win_market_share ?? 0) * 100).toFixed(2)}%`}</td><td>{row.market_reason}</td><td>{row.rule_judgement ?? "未生成"}</td><td>{[row.rule_reason, ...row.missing_reasons].join(" / ")}</td></tr>)}
      </tbody></table></div>}
      <p className="empty-note">{comparison.disclaimer}</p>
    </>}
  </section>;
}
