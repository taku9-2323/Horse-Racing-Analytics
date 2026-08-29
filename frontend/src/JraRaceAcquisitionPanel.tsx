import { useState } from "react";

type RaceCard = {
  card_id: number;
  race: { racecourse: string; race_date: string; race_number: number; start_time: string; surface: string; distance_m: number; going: string; field_size: number };
  runners: Array<{ gate: number; horse_number: number; horse_name: string; age: number; sex: string; assigned_weight: number; status: string }>;
  source: { url: string; received_at: string; source_updated_at: string | null; parser_version: string; response_sha256: string; validation_status: string };
};

export default function JraRaceAcquisitionPanel() {
  const [url, setUrl] = useState("");
  const [card, setCard] = useState<RaceCard | null>(null);
  const [state, setState] = useState<"idle" | "loading" | "error">("idle");
  const [message, setMessage] = useState("");
  const [oddsUrl, setOddsUrl] = useState("");
  const [oddsMessage, setOddsMessage] = useState("");
  const [oddsLoading, setOddsLoading] = useState(false);

  const acquire = async () => {
    setState("loading");
    setMessage("");
    try {
      const response = await fetch("/api/acquisition/jra/race-card", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ url }),
      });
      if (!response.ok) {
        const payload = (await response.json()) as { detail?: { message?: string } };
        throw new Error(payload.detail?.message ?? `HTTP ${response.status}`);
      }
      setCard((await response.json()) as RaceCard);
      setState("idle");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "取得できませんでした。CSV取込を使用してください。");
      setState("error");
    }
  };

  const acquireOdds = async () => {
    if (!card) return;
    setOddsLoading(true); setOddsMessage("");
    try {
      const response = await fetch(`/api/acquisition/jra/race-cards/${card.card_id}/odds`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ url: oddsUrl }),
      });
      const payload = (await response.json()) as { race_id?: number; snapshot_id?: number; detail?: { message?: string } };
      if (!response.ok) throw new Error(payload.detail?.message ?? `HTTP ${response.status}`);
      setOddsMessage(`正式レース ${payload.race_id} / オッズ時点 ${payload.snapshot_id} を登録しました。`);
    } catch (error) {
      setOddsMessage(error instanceof Error ? error.message : "オッズを取得できませんでした。CSV取込を使用してください。");
    } finally { setOddsLoading(false); }
  };

  return <section className="panel analysis-panel" aria-labelledby="jra-acquisition-heading">
    <div className="panel-heading"><div><span className="section-number">02</span><h2 id="jra-acquisition-heading">JRAレース情報取得</h2></div><span className="local-badge">利用者操作のみ</span></div>
    <div className="import-form">
      <label htmlFor="jra-race-card-url">JRAレースページURL</label>
      <input id="jra-race-card-url" type="url" value={url} onChange={(event) => setUrl(event.target.value)} placeholder="https://www.jra.go.jp/JRADB/accessD.html?... または accessS.html?..." />
      <button type="button" disabled={!url || state === "loading"} onClick={() => void acquire()}>{state === "loading" ? "取得・検証中…" : "取得して登録"}</button>
      <p>開催前は出馬表、過去レースはレース結果ページから基本情報と出走馬を直列取得します。停止時はCSV取込を使用してください。</p>
      {state === "error" && <div className="import-error" role="alert"><strong>{message}</strong><p>CSV取込は恒久的な代替手段です。</p></div>}
    </div>
    {card && <div className="analysis-result">
      <div className="race-heading"><div><h3>{card.race.racecourse} {card.race.race_number}R</h3><p>{card.race.race_date} {card.race.start_time} / {card.race.surface}{card.race.distance_m}m / {card.race.going}</p></div><strong>{card.race.field_size}頭</strong></div>
      <div className="table-wrap"><table><thead><tr><th>枠・馬番</th><th>馬名</th><th>性齢</th><th>負担重量</th><th>状態</th></tr></thead><tbody>{card.runners.map((runner) => <tr key={runner.horse_number}><td>{runner.gate}枠 {runner.horse_number}番</td><td>{runner.horse_name}</td><td>{runner.sex}{runner.age}</td><td>{runner.assigned_weight.toFixed(1)}kg</td><td>{runner.status}</td></tr>)}</tbody></table></div>
      <aside className="candidate-empty"><strong>検証済み / {card.source.parser_version}</strong><span>受付 {card.source.received_at} / JRA更新 {card.source.source_updated_at ?? "不明"} / hash {card.source.response_sha256.slice(0, 12)}…</span></aside>
      <div className="import-form">
        <label htmlFor="jra-odds-url">JRA単勝・複勝オッズURL</label>
        <input id="jra-odds-url" type="url" value={oddsUrl} onChange={(event) => setOddsUrl(event.target.value)} placeholder="https://www.jra.go.jp/JRADB/accessO.html?CNAME=..." />
        <button type="button" disabled={!oddsUrl || oddsLoading} onClick={() => void acquireOdds()}>{oddsLoading ? "取得・検証中…" : "オッズを取得して正式登録"}</button>
        {oddsMessage && <div role="status">{oddsMessage}</div>}
      </div>
    </div>}
  </section>;
}
