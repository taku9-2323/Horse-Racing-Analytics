import { useState } from "react";

type Snapshot = { id: number; observed_at: string | null; received_at: string };
type Ranking = {
  snapshot_id: number; observed_at: string | null; received_at: string;
  status: "available" | "unavailable"; unavailable_reason: string | null;
  label: string; disclaimer: string;
  runners: Array<{ rank: number; horse_number: number; horse_name: string; win_odds: number; normalized_win_market_share: number }>;
};

export default function MarketAttentionPanel({ raceId }: { raceId: number }) {
  const [snapshots, setSnapshots] = useState<Snapshot[]>([]);
  const [selected, setSelected] = useState<number | null>(null);
  const [ranking, setRanking] = useState<Ranking | null>(null);
  const [error, setError] = useState("");
  const [opened, setOpened] = useState(false);

  const loadRanking = async (snapshotId: number) => {
    setError("");
    try {
      const response = await fetch(`/api/races/${raceId}/market-attention?snapshot_id=${snapshotId}`);
      if (!response.ok) throw new Error();
      setRanking((await response.json()) as Ranking);
    } catch {
      setError("市場注目順位を読み込めませんでした。");
    }
  };

  const open = async () => {
    setOpened(true);
    setError("");
    try {
      const response = await fetch(`/api/races/${raceId}/odds-snapshots`);
      if (!response.ok) throw new Error();
      const loaded = (await response.json()) as Snapshot[];
      setSnapshots(loaded);
      const latest = loaded.at(-1);
      if (!latest) { setSelected(null); setRanking(null); return; }
      setSelected(latest.id);
      await loadRanking(latest.id);
    } catch {
      setError("オッズ時点を読み込めませんでした。");
    }
  };

  return (
    <section className="workflow-section" aria-labelledby="market-attention-heading">
      <h4 id="market-attention-heading">市場評価による順位</h4>
      {selected === null && snapshots.length === 0 && <button type="button" onClick={() => void open()}>市場注目順位を表示</button>}
      {selected === null && snapshots.length === 0 && ranking === null && <p className="empty-note">{opened ? "保存済みのオッズ時点がないため順位を表示できません。" : "選択したレースの保存済みオッズから確認します。"}</p>}
      {snapshots.length > 0 && <label>対象オッズ時点 <select value={selected ?? ""} onChange={(event) => { setSelected(Number(event.target.value)); setRanking(null); }}>
        {snapshots.map((snapshot) => <option key={snapshot.id} value={snapshot.id}>
          #{snapshot.id} 観測 {snapshot.observed_at ?? "不明"} / 受付 {snapshot.received_at}
        </option>)}
      </select></label>}
      {snapshots.length > 0 && selected !== null && <button type="button" onClick={() => void loadRanking(selected)}>選択時点を表示</button>}
      {error && <p role="alert">{error}</p>}
      {ranking?.status === "unavailable" && <p role="status">{ranking.unavailable_reason}</p>}
      {ranking?.status === "available" && <>
        <p>観測 {ranking.observed_at ?? "不明"} / 受付 {ranking.received_at}</p>
        <div className="table-wrap"><table><thead><tr><th>順位</th><th>馬番・馬名</th><th>単勝オッズ</th><th>正規化市場シェア</th></tr></thead>
          <tbody>{ranking.runners.map((runner) => <tr key={runner.horse_number}><td>{runner.rank}</td><td>{runner.horse_number} {runner.horse_name}</td><td>{runner.win_odds.toFixed(1)}</td><td>{(runner.normalized_win_market_share * 100).toFixed(2)}%</td></tr>)}</tbody>
        </table></div>
        <p className="empty-note">{ranking.disclaimer}</p>
      </>}
    </section>
  );
}
