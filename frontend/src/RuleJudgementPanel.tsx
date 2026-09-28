import { formatJst } from "./JstTimestamp";
import { useState } from "react";

type Rule = { id: number; title: string; version: number };
type Snapshot = { id: number; observed_at: string | null };
type RunnerJudgement = { horse_number: number; horse_name: string; judgement: "注目" | "見送り" | "判定不能"; satisfied_conditions: string[]; failed_conditions: string[]; missing_reasons: string[] };
type Run = { id: number; input_snapshot_id: number; judgement_as_of: string; status: "active" | "invalidated"; official_pre_race_eligible: boolean; exclusion_reason: string | null; disclaimer: string; runners: RunnerJudgement[] };

export default function RuleJudgementPanel({ raceId }: { raceId: number }) {
  const [rules, setRules] = useState<Rule[]>([]);
  const [snapshots, setSnapshots] = useState<Snapshot[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [ruleId, setRuleId] = useState<number | null>(null);
  const [snapshotId, setSnapshotId] = useState<number | null>(null);
  const [message, setMessage] = useState("");

  const open = async () => {
    try {
      const [ruleResponse, snapshotResponse, historyResponse] = await Promise.all([
        fetch("/api/rule-versions"), fetch(`/api/races/${raceId}/odds-snapshots`), fetch(`/api/races/${raceId}/rule-judgements`),
      ]);
      if (!ruleResponse.ok || !snapshotResponse.ok || !historyResponse.ok) throw new Error();
      const loadedRules = await ruleResponse.json() as Rule[];
      const loadedSnapshots = await snapshotResponse.json() as Snapshot[];
      setRules(loadedRules); setSnapshots(loadedSnapshots); setRuns(await historyResponse.json() as Run[]);
      setRuleId(loadedRules.at(-1)?.id ?? null); setSnapshotId(loadedSnapshots.at(-1)?.id ?? null);
    } catch { setMessage("ルール判定を読み込めませんでした。"); }
  };

  const freeze = async () => {
    const snapshot = snapshots.find((item) => item.id === snapshotId);
    if (!snapshot?.observed_at || ruleId === null) return;
    const response = await fetch(`/api/races/${raceId}/rule-judgements/freeze`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ snapshot_id: snapshot.id, rule_version_id: ruleId, judgement_as_of: snapshot.observed_at }),
    });
    if (!response.ok) { setMessage("判定時点より後の情報は使用できません。"); return; }
    const created = await response.json() as Run;
    setRuns((current) => [...current, created]);
    setMessage("ルール判定を固定しました。");
  };

  return <section className="workflow-section" aria-labelledby="rule-judgement-heading">
    <h4 id="rule-judgement-heading">ルールベース判定</h4>
    {rules.length === 0 && <button type="button" onClick={() => void open()}>ルール判定を開く</button>}
    {rules.length > 0 && <div className="snapshot-form">
      <label>ルール版 <select value={ruleId ?? ""} onChange={(event) => setRuleId(Number(event.target.value))}>{rules.map((rule) => <option key={rule.id} value={rule.id}>{rule.title} v{rule.version}</option>)}</select></label>
      <label>入力時点 <select value={snapshotId ?? ""} onChange={(event) => setSnapshotId(Number(event.target.value))}>{snapshots.map((snapshot) => <option title={snapshot.observed_at ?? undefined} key={snapshot.id} value={snapshot.id}>#{snapshot.id} / {formatJst(snapshot.observed_at, "観測時刻不明")}</option>)}</select></label>
      <button type="button" disabled={snapshotId === null} onClick={() => void freeze()}>この時点で判定を固定</button>
    </div>}
    {message && <p role="status">{message}</p>}
    {runs.map((run) => <article className={`workflow-card ${run.status}`} key={run.id}>
      <strong>{run.status === "active" ? "固定済み" : "無効化済み"} / 入力時点 #{run.input_snapshot_id}</strong>
      <span>{run.official_pre_race_eligible ? "公式な事前判定" : run.exclusion_reason}</span>
      <ul>{run.runners.map((runner) => <li key={runner.horse_number}><b>{runner.horse_number} {runner.horse_name}: {runner.judgement}</b> — {[...runner.satisfied_conditions, ...runner.failed_conditions, ...runner.missing_reasons].join(" / ")}</li>)}</ul>
      <span>{run.disclaimer}</span>
    </article>)}
  </section>;
}
