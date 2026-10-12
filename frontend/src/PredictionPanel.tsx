import JstTimestamp from "./JstTimestamp";
import { useEffect, useState } from "react";
import { buildSnapshotComparison, getSnapshotAge, parseStrictTimestamp, type OddsSnapshot, type SnapshotOddsValue } from "./oddsHistory";

type RunnerOdds = {
  horse_number: number;
  win_odds: number;
  place_odds_min: number;
  place_odds_max: number;
};

type MarketRunnerPrediction = { horse_number: number; raw_inverse_win_odds: number; win_market_share: number };
type IndependentRunnerPrediction = { horse_number: number; win_probability: number | null; place_probability: number | null };
type PredictionRunCommon = {
  id: number;
  input_snapshot_id: number;
  model_identifier: string;
  model_version: string;
  frozen_at: string;
  status: "active" | "invalidated";
  invalidation_reason: string | null;
  official_evaluation_eligible: boolean;
  evaluation_exclusion_reason: string | null;
  analysis_tags: Array<{ rule_key: string; version: number; context: Record<string, unknown> }>;
};
type MarketPredictionRun = PredictionRunCommon & {
  prediction_kind: "market_baseline";
  prediction_as_of: string;
  rationale: string;
  output_capabilities: ["win"];
  runners: MarketRunnerPrediction[];
};
type IndependentPredictionRun = PredictionRunCommon & {
  prediction_kind: "independent";
  prediction_as_of: string;
  rationale: string;
  output_capabilities: Array<"win" | "place">;
  runners: IndependentRunnerPrediction[];
};
type PredictionRun = MarketPredictionRun | IndependentPredictionRun;
type RuleJudgementRun = {
  id: number;
  input_snapshot_id: number;
  rule_version_id: number;
  status: "active" | "invalidated";
};

type Props = { raceId: number; runners: RunnerOdds[]; raceStartUtc?: string };

function showOdds(value: SnapshotOddsValue | undefined): string {
  if (value === undefined) return "この時点に値なし";
  if (value === null) return "欠損";
  return String(value);
}

function signed(value: number, digits: number): string {
  return `${value >= 0 ? "+" : ""}${value.toFixed(digits)}`;
}

function SnapshotObservedAt({ value }: { value: string | null }) {
  const invalid = value !== null && parseStrictTimestamp(value) === null;
  return <>
    {invalid ? <span>時刻不正</span> : <JstTimestamp value={value} unknown="観測時刻不明" />}
    {invalid && <span className="snapshot-invalid-time">保存値（時刻不正）: <code>{value || "（空文字列）"}</code></span>}
  </>;
}

function showDuration(milliseconds: number): string {
  const totalSeconds = Math.floor(milliseconds / 1_000);
  if (milliseconds > 0 && totalSeconds === 0) return "1秒未満";
  if (totalSeconds < 60) return `${totalSeconds}秒`;
  const totalMinutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  if (totalMinutes <= 60) return seconds === 0 ? `${totalMinutes}分` : `${totalMinutes}分${seconds}秒`;
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  const duration = minutes === 0 ? `${hours}時間` : `${hours}時間${minutes}分`;
  return seconds === 0 ? duration : `${duration}${seconds}秒`;
}

function isPostStartResolverObservation(snapshot: OddsSnapshot, raceStartUtc?: string): boolean {
  if (!raceStartUtc || snapshot.source !== "jra_web" || snapshot.source_updated_at !== null) return false;
  if (snapshot.observed_at === null) return false;
  const observed = Date.parse(snapshot.observed_at);
  const received = Date.parse(snapshot.received_at);
  const start = Date.parse(raceStartUtc);
  return Number.isFinite(observed) && observed === received && Number.isFinite(start) && observed >= start;
}

export default function PredictionPanel({ raceId, runners, raceStartUtc }: Props) {
  const [observedAt, setObservedAt] = useState("");
  const [draftOdds, setDraftOdds] = useState<RunnerOdds[]>(runners);
  const [snapshots, setSnapshots] = useState<OddsSnapshot[]>([]);
  const [predictions, setPredictions] = useState<PredictionRun[]>([]);
  const [judgements, setJudgements] = useState<RuleJudgementRun[]>([]);
  const [selectedSnapshotId, setSelectedSnapshotId] = useState<number | null>(null);
  const [asOfMilliseconds, setAsOfMilliseconds] = useState<number | null>(null);
  const [correctionReason, setCorrectionReason] = useState("");
  const [message, setMessage] = useState("");

  const loadHistory = async () => {
    const [snapshotResponse, predictionResponse, judgementResponse] = await Promise.all([
      fetch(`/api/races/${raceId}/odds-snapshots`),
      fetch(`/api/races/${raceId}/predictions`),
      fetch(`/api/races/${raceId}/rule-judgements`),
    ]);
    if (!snapshotResponse.ok || !predictionResponse.ok || !judgementResponse.ok) {
      throw new Error("保存済み履歴を読み込めませんでした。");
    }
    const [loadedSnapshots, loadedPredictions, loadedJudgements] = await Promise.all([
      snapshotResponse.json() as Promise<OddsSnapshot[]>,
      predictionResponse.json() as Promise<PredictionRun[]>,
      judgementResponse.json() as Promise<RuleJudgementRun[]>,
    ]);
    setSnapshots(loadedSnapshots);
    setPredictions(loadedPredictions);
    setJudgements(loadedJudgements);
    setSelectedSnapshotId((current) => loadedSnapshots.some((snapshot) => snapshot.id === current)
      ? current
      : loadedSnapshots.at(-1)?.id ?? null);
    setAsOfMilliseconds(Date.now());
  };

  useEffect(() => {
    setDraftOdds(runners);
    void loadHistory().catch((error: unknown) => {
      setMessage(error instanceof Error ? error.message : "保存済み履歴を読み込めませんでした。");
    });
  }, [raceId, runners]);

  const request = async <T,>(url: string, body: object): Promise<T> => {
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!response.ok) {
      const payload = (await response.json()) as { detail?: { message?: string } | string };
      throw new Error(typeof payload.detail === "object" ? payload.detail.message : payload.detail ?? `HTTP ${response.status}`);
    }
    return (await response.json()) as T;
  };

  const updateOdds = (horseNumber: number, field: keyof Omit<RunnerOdds, "horse_number">, value: string) => {
    setDraftOdds((current) => current.map((runner) => runner.horse_number === horseNumber
      ? { ...runner, [field]: Number(value) }
      : runner));
  };

  const saveSnapshot = async () => {
    if (!observedAt) return;
    try {
      const snapshot = await request<OddsSnapshot>(`/api/races/${raceId}/odds-snapshots`, {
        observed_at: new Date(observedAt).toISOString(), source: "ui", runners: draftOdds,
      });
      setSnapshots((current) => [...current, snapshot]);
      setMessage("オッズ時点を保存しました。");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "保存できませんでした。");
    }
  };

  const freeze = async (snapshotId: number) => {
    try {
      const prediction = await request<PredictionRun>(`/api/odds-snapshots/${snapshotId}/freeze`, {
        model_identifier: "market-baseline", model_version: "1.0",
      });
      setPredictions((current) => [...current, prediction]);
      setMessage("予測を固定しました。");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "固定できませんでした。");
    }
  };

  const correct = async (prediction: PredictionRun) => {
    const latest = snapshots.at(-1);
    if (!latest || !correctionReason.trim()) return;
    try {
      await request<PredictionRun>(`/api/predictions/${prediction.id}/correct`, {
        reason: correctionReason, input_snapshot_id: latest.id,
      });
      await loadHistory();
      setCorrectionReason("");
      setMessage("旧版を無効化し、訂正版を作成しました。");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "訂正できませんでした。");
    }
  };

  const selectedSnapshot = snapshots.find((snapshot) => snapshot.id === selectedSnapshotId) ?? null;
  const latestSnapshotId = snapshots.at(-1)?.id ?? null;
  const comparison = selectedSnapshotId === null ? null : buildSnapshotComparison(snapshots, selectedSnapshotId);
  const selectedAge = selectedSnapshot && asOfMilliseconds !== null
    ? getSnapshotAge(selectedSnapshot.observed_at, asOfMilliseconds)
    : null;
  const renderReferences = (snapshotId: number) => {
    const predictionReferences = predictions.filter((prediction) => prediction.input_snapshot_id === snapshotId);
    const judgementReferences = judgements.filter((judgement) => judgement.input_snapshot_id === snapshotId);
    return <>
      {predictionReferences.length > 0 && <span className="snapshot-reference">固定予測参照: {predictionReferences.map((prediction) =>
        `#${prediction.id} ${prediction.status === "active" ? "固定済み" : "無効化済み"}`,
      ).join(", ")}</span>}
      {judgementReferences.length > 0 && <span className="snapshot-reference">固定判定参照: {judgementReferences.map((judgement) =>
        `#${judgement.id} ルール版ID ${judgement.rule_version_id} ${judgement.status === "active" ? "固定済み" : "無効化済み"}`,
      ).join(", ")}</span>}
    </>;
  };

  return (
    <section className="prediction-workflow" aria-labelledby="prediction-heading">
      <h4 id="prediction-heading">オッズ時点と固定予測</h4>
      <div className="odds-editor" aria-label="保存するオッズ">
        {draftOdds.map((runner) => (
          <fieldset key={runner.horse_number}>
            <legend>{runner.horse_number}番</legend>
            <label>{runner.horse_number}番 単勝オッズ<input type="number" min="0.01" step="0.1" value={runner.win_odds} onChange={(event) => updateOdds(runner.horse_number, "win_odds", event.target.value)} /></label>
            <label>{runner.horse_number}番 複勝下限<input type="number" min="0.01" step="0.1" value={runner.place_odds_min} onChange={(event) => updateOdds(runner.horse_number, "place_odds_min", event.target.value)} /></label>
            <label>{runner.horse_number}番 複勝上限<input type="number" min="0.01" step="0.1" value={runner.place_odds_max} onChange={(event) => updateOdds(runner.horse_number, "place_odds_max", event.target.value)} /></label>
          </fieldset>
        ))}
      </div>
      <div className="snapshot-form">
        <label htmlFor="observed-at">オッズ観測時刻</label>
        <input id="observed-at" type="datetime-local" value={observedAt}
          onInput={(event) => setObservedAt(event.currentTarget.value)}
          onChange={(event) => setObservedAt(event.target.value)} />
        <button type="button" disabled={!observedAt} onClick={() => void saveSnapshot()}>オッズ時点を保存</button>
      </div>
      {message && <p className="workflow-message" role="status">{message}</p>}
      <div className="workflow-columns">
        <div>
          <h5>保存済み時点</h5>
          <div className="history-controls">
            <label htmlFor="comparison-snapshot">比較する保存時点</label>
            <select id="comparison-snapshot" aria-label="比較する保存時点"
              value={selectedSnapshotId ?? ""}
              onChange={(event) => setSelectedSnapshotId(Number(event.target.value))}
              disabled={snapshots.length === 0}>
              {snapshots.map((snapshot) => <option key={snapshot.id} value={snapshot.id}>時点 #{snapshot.id}</option>)}
            </select>
            <button type="button" aria-label="保存履歴を再読込" onClick={() => void loadHistory().catch((error: unknown) => {
              setMessage(error instanceof Error ? error.message : "保存済み履歴を読み込めませんでした。");
            })}>保存履歴を再読込</button>
          </div>
          {snapshots.length === 0 && <p className="empty-note">まだ保存されていません。</p>}
          {snapshots.map((snapshot) => (
            <article className="workflow-card" key={snapshot.id}>
              <strong>時点 #{snapshot.id}</strong>
              {snapshot.id === selectedSnapshotId && <span className="snapshot-state">選択中</span>}
              {snapshot.id === latestSnapshotId && <span className="snapshot-state">最新保存済み</span>}
              <span>観測 <SnapshotObservedAt value={snapshot.observed_at} /></span>
              <span>取得元 {snapshot.source}</span>
              <span>アプリ受信 <JstTimestamp value={snapshot.received_at} /></span>
              <span>{snapshot.source_updated_at
                ? <>取得元更新時刻 <JstTimestamp value={snapshot.source_updated_at} /></>
                : "取得元更新時刻不明"}</span>
              <span>{snapshot.runners.map((runner) => `${runner.horse_number}番 単勝${showOdds(runner.win_odds)} / 複勝${showOdds(runner.place_odds_min)}–${showOdds(runner.place_odds_max)}`).join(" / ")}</span>
              {renderReferences(snapshot.id)}
              <button type="button" onClick={() => void freeze(snapshot.id)}>この時点の予測を固定</button>
            </article>
          ))}
        </div>
        <div className="snapshot-comparison" aria-labelledby="snapshot-comparison-heading">
          <h5 id="snapshot-comparison-heading">直前保存時点との比較</h5>
          {!selectedSnapshot && <p className="empty-note">比較する保存時点がありません。</p>}
          {selectedSnapshot && <div className="selected-snapshot-summary">
            <strong>選択中の時点 #{selectedSnapshot.id}</strong>
            <span>保存観測時刻 <SnapshotObservedAt value={selectedSnapshot.observed_at} /></span>
            <span>取得元 {selectedSnapshot.source}</span>
            <span>アプリ受信 <JstTimestamp value={selectedSnapshot.received_at} /></span>
            <span>{selectedSnapshot.source_updated_at
              ? <>取得元更新時刻 <JstTimestamp value={selectedSnapshot.source_updated_at} /></>
              : "取得元更新時刻不明"}</span>
            <span><b>この表示を開いた時点での経過</b> {selectedAge?.elapsedMilliseconds === null || selectedAge === null
              ? `経過不明: ${selectedAge?.reason ?? "表示時点が不明です。"}`
              : showDuration(selectedAge.elapsedMilliseconds)}</span>
            {isPostStartResolverObservation(selectedSnapshot, raceStartUtc)
              && <span>発走後の最終オッズ観測として記録された時刻です。</span>}
          </div>}
          {comparison && <>
            <p className="comparison-explanation">
              {comparison.timeComparable
                ? `観測間隔: ${comparison.intervalMilliseconds === null ? "不明" : showDuration(comparison.intervalMilliseconds)} / 前時点 #${comparison.previous?.id} → 選択時点 #${comparison.selected.id}`
                : `${comparison.previous ? `前時点 #${comparison.previous.id} / ` : ""}選択時点 #${comparison.selected.id} / ${comparison.reason}`}
            </p>
            <div className="snapshot-comparison-table-wrap">
              <table className="snapshot-comparison-table" aria-label="保存オッズの時点比較">
                <thead><tr><th scope="col">馬番</th><th scope="col">構成</th><th scope="col">単勝</th><th scope="col">複勝下限</th><th scope="col">複勝上限</th></tr></thead>
                <tbody>{comparison.rows.map((row) => <tr key={row.horseNumber}>
                  <th scope="row" data-label="馬番">{row.horseNumber}番</th>
                  <td data-label="構成">{row.runnerState === "matched" ? "両時点に保存" : row.runnerState === "selected_only" ? "選択時点のみ" : "前時点のみ"}</td>
                  {row.metrics.map((metric) => <td key={metric.key} data-label={metric.label}>
                    <span>前 {showOdds(metric.previousValue)} / 選択 {showOdds(metric.selectedValue)}</span>
                    {comparison.timeComparable && metric.difference !== null && <span>差 {signed(metric.difference, 2)}</span>}
                    {comparison.timeComparable && metric.percentChange !== null && <span>前値比 {signed(metric.percentChange, 2)}%</span>}
                    {comparison.timeComparable && metric.unavailableReason && <span>比較できません: {metric.unavailableReason}</span>}
                  </td>)}
                </tr>)}</tbody>
              </table>
            </div>
          </>}
        </div>
        <div className="prediction-history">
          <h5>予測履歴</h5>
          {predictions.length === 0 && <p className="empty-note">まだ固定予測はありません。</p>}
          {predictions.map((prediction) => (
            <article className={`workflow-card ${prediction.status}`} key={prediction.id}>
              <strong>{prediction.status === "active" ? "固定済み" : "無効化済み"} / {prediction.prediction_kind === "independent"
                ? `独立予測 ${prediction.model_identifier} ${prediction.model_version}`
                : `市場基準 ${prediction.model_version}`}</strong>
              <span>入力時点 #{prediction.input_snapshot_id} / 固定 <JstTimestamp value={prediction.frozen_at} /></span>
              {prediction.prediction_kind === "independent" ? (
                <>
                  <span>モデル時点 <JstTimestamp value={prediction.prediction_as_of} /> / 根拠 {prediction.rationale}</span>
                  <span>{prediction.runners.map((runner) => {
                    const values = [
                      runner.win_probability === null ? null : `単勝予測確率 ${(runner.win_probability * 100).toFixed(2)}%`,
                      runner.place_probability === null ? null : `複勝予測確率 ${(runner.place_probability * 100).toFixed(2)}%`,
                    ].filter(Boolean).join(" / ");
                    return `${runner.horse_number}番 ${values}`;
                  }).join(" / ")}</span>
                </>
              ) : (
                <span>{prediction.runners.map((runner) => {
                  return `${runner.horse_number}番 単勝市場投票シェア ${(runner.win_market_share * 100).toFixed(2)}%`;
                }).join(" / ")}</span>
              )}
              <span>{prediction.analysis_tags.length === 0
                ? "一致した有効タグなし"
                : `一致タグ: ${prediction.analysis_tags.map((tag) => `${tag.rule_key} v${tag.version}`).join(" / ")}`}</span>
              <span>{prediction.official_evaluation_eligible ? "公式評価対象" : prediction.evaluation_exclusion_reason}</span>
              {prediction.invalidation_reason && <span>理由: {prediction.invalidation_reason}</span>}
              {prediction.status === "active" && (
                <div className="correction-form">
                  <label htmlFor={`reason-${prediction.id}`}>訂正理由</label>
                  <input id={`reason-${prediction.id}`} value={correctionReason} onChange={(event) => setCorrectionReason(event.target.value)} />
                  <button type="button" disabled={!correctionReason.trim() || snapshots.length === 0} onClick={() => void correct(prediction)}>最新時点で訂正版を作成</button>
                </div>
              )}
            </article>
          ))}
        </div>
      </div>
    </section>
  );
}
