import JstTimestamp from "./JstTimestamp";
import { useEffect, useState } from "react";

type RunnerOdds = {
  horse_number: number;
  win_odds: number;
  place_odds_min: number;
  place_odds_max: number;
};

type OddsSnapshot = {
  id: number;
  observed_at: string | null;
  received_at: string;
  runners: RunnerOdds[];
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

type Props = { raceId: number; runners: RunnerOdds[] };

export default function PredictionPanel({ raceId, runners }: Props) {
  const [observedAt, setObservedAt] = useState("");
  const [draftOdds, setDraftOdds] = useState<RunnerOdds[]>(runners);
  const [snapshots, setSnapshots] = useState<OddsSnapshot[]>([]);
  const [predictions, setPredictions] = useState<PredictionRun[]>([]);
  const [correctionReason, setCorrectionReason] = useState("");
  const [message, setMessage] = useState("");

  const loadHistory = async () => {
    const [snapshotResponse, predictionResponse] = await Promise.all([
      fetch(`/api/races/${raceId}/odds-snapshots`),
      fetch(`/api/races/${raceId}/predictions`),
    ]);
    if (!snapshotResponse.ok || !predictionResponse.ok) {
      throw new Error("保存済み履歴を読み込めませんでした。");
    }
    setSnapshots((await snapshotResponse.json()) as OddsSnapshot[]);
    setPredictions((await predictionResponse.json()) as PredictionRun[]);
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
          {snapshots.length === 0 && <p className="empty-note">まだ保存されていません。</p>}
          {snapshots.map((snapshot) => (
            <article className="workflow-card" key={snapshot.id}>
              <strong>時点 #{snapshot.id} / 観測 <JstTimestamp value={snapshot.observed_at} unknown="観測時刻不明" /></strong>
              <span>受付 <JstTimestamp value={snapshot.received_at} /></span>
              <span>{snapshot.runners.map((runner) => `${runner.horse_number}番 単勝${runner.win_odds} / 複勝${runner.place_odds_min}–${runner.place_odds_max}`).join(" / ")}</span>
              <button type="button" onClick={() => void freeze(snapshot.id)}>この時点の予測を固定</button>
            </article>
          ))}
        </div>
        <div>
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
