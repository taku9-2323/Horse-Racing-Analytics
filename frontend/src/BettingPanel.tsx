import { useState } from "react";

type Bet = {
  id: number; horse_number: number; bet_type: "win" | "place";
  decision_type: "candidate" | "discretionary"; amount_yen: number;
};
type Totals = { stake_yen: number; payout_yen: number; refund_yen: number; profit_yen: number | null; return_rate: number | null };
type Ledger = {
  bets: Bet[];
  result_version: { id: number; version: number } | null;
  totals: Totals;
  by_decision_type: Record<"candidate" | "discretionary", Totals>;
};
type Props = { raceId: number; runners: Array<{ horse_number: number; horse_name: string }> };

const responseError = async (response: Response) => {
  const payload = (await response.json()) as { detail?: { message?: string } | string };
  return (typeof payload.detail === "object" ? payload.detail.message : payload.detail) ?? `HTTP ${response.status}`;
};

export default function BettingPanel({ raceId, runners }: Props) {
  const [ledger, setLedger] = useState<Ledger | null>(null);
  const [horseNumber, setHorseNumber] = useState(runners[0]?.horse_number ?? 1);
  const [betType, setBetType] = useState<"win" | "place">("win");
  const [amountYen, setAmountYen] = useState(100);
  const [resultFile, setResultFile] = useState<File | null>(null);
  const [correctionReason, setCorrectionReason] = useState("");
  const [message, setMessage] = useState("");

  const loadLedger = async () => {
    const response = await fetch(`/api/races/${raceId}/ledger`);
    if (!response.ok) throw new Error(await responseError(response));
    setLedger((await response.json()) as Ledger);
  };

  const showLedger = async () => {
    try {
      await loadLedger();
      setMessage("購入台帳を読み込みました。");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "購入台帳を読み込めませんでした。");
    }
  };

  const createBet = async () => {
    try {
      const response = await fetch(`/api/races/${raceId}/bets`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ horse_number: horseNumber, bet_type: betType, decision_type: "discretionary", amount_yen: amountYen }),
      });
      if (!response.ok) throw new Error(await responseError(response));
      await loadLedger();
      setMessage("実購入を登録しました。");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "購入を登録できませんでした。");
    }
  };

  const importResults = async (correct = false) => {
    if (!resultFile) return;
    try {
      const endpoint = correct
        ? `/api/races/${raceId}/results/correct?reason=${encodeURIComponent(correctionReason.trim())}`
        : `/api/races/${raceId}/results/import`;
      const response = await fetch(endpoint, {
        method: "POST", headers: { "Content-Type": "text/csv; charset=utf-8" }, body: resultFile,
      });
      if (!response.ok) throw new Error(await responseError(response));
      await loadLedger();
      if (correct) setCorrectionReason("");
      setMessage(correct ? "元の結果を残し、訂正版で再精算しました。" : "公式結果を取り込み、購入を精算しました。");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "結果を取り込めませんでした。");
    }
  };

  const yen = (value: number) => `¥${value.toLocaleString("ja-JP")}`;
  const settledYen = (value: number | null) => value === null ? "未確定" : yen(value);
  const rate = (value: number | null) => value === null ? "—" : `${(value * 100).toFixed(2)}%`;

  return (
    <section className="betting-workflow" aria-labelledby="betting-heading">
      <div className="betting-heading">
        <div>
          <h4 id="betting-heading">実購入・結果・収支</h4>
          <p>現在は期待値候補がないため、実購入は候補外裁量として記録し、JRA発表の100円当たり払戻で精算します。</p>
        </div>
        <button type="button" onClick={() => void showLedger()}>購入・収支を表示</button>
      </div>
      {message && <p className="workflow-message" role="status">{message}</p>}
      {ledger && (
        <>
          <div className="bet-form">
            <label>馬番・馬名<select value={horseNumber} onChange={(event) => setHorseNumber(Number(event.target.value))}>
              {runners.map((runner) => <option key={runner.horse_number} value={runner.horse_number}>{runner.horse_number}番 {runner.horse_name}</option>)}
            </select></label>
            <label>券種<select value={betType} onChange={(event) => setBetType(event.target.value as "win" | "place")}>
              <option value="win">単勝</option><option value="place">複勝</option>
            </select></label>
            <p className="fixed-decision-type">購入区分: 候補外裁量</p>
            <label>購入額（円）<input type="number" min="100" step="100" value={amountYen} onChange={(event) => setAmountYen(Number(event.target.value))} /></label>
            <button type="button" onClick={() => void createBet()}>実購入を登録</button>
          </div>
          <div className="result-import">
            <label htmlFor={`result-csv-${raceId}`}>結果CSVファイル</label>
            <input id={`result-csv-${raceId}`} type="file" accept=".csv,text/csv" onChange={(event) => setResultFile(event.target.files?.[0] ?? null)} />
            {ledger.result_version === null ? (
              <button type="button" disabled={!resultFile} onClick={() => void importResults()}>結果を取り込んで精算</button>
            ) : (
              <>
                <label>訂正理由<input value={correctionReason} onChange={(event) => setCorrectionReason(event.target.value)} /></label>
                <button type="button" disabled={!resultFile || !correctionReason.trim()} onClick={() => void importResults(true)}>訂正版で再精算</button>
              </>
            )}
          </div>
          <div className="bet-list">
            {ledger.bets.length === 0 ? <p className="empty-note">実購入はまだありません。</p> : ledger.bets.map((bet) => (
              <span key={bet.id}>{bet.horse_number}番 / {bet.bet_type === "win" ? "単勝" : "複勝"} / {bet.decision_type === "candidate" ? "候補内" : "候補外裁量"} / {yen(bet.amount_yen)}</span>
            ))}
          </div>
          <section className="settlement-summary" aria-label="収支集計">
            <strong>{ledger.result_version ? `結果 v${ledger.result_version.version} 精算済み` : "結果未取込"}</strong>
            <span>購入額 {yen(ledger.totals.stake_yen)}</span>
            <span>払戻額 {yen(ledger.totals.payout_yen)}</span>
            <span>返還額 {yen(ledger.totals.refund_yen)}</span>
            <span>損益 {settledYen(ledger.totals.profit_yen)}</span>
            <span>回収率 {rate(ledger.totals.return_rate)}</span>
          </section>
          <div className="decision-totals">
            {(["candidate", "discretionary"] as const).map((decisionType) => {
              const totals = ledger.by_decision_type[decisionType];
              const label = decisionType === "candidate" ? "候補内" : "候補外裁量";
              return <span key={decisionType}>{label} 購入額 {yen(totals.stake_yen)} / 払戻額 {yen(totals.payout_yen)} / 返還額 {yen(totals.refund_yen)} / 損益 {settledYen(totals.profit_yen)} / 回収率 {rate(totals.return_rate)}</span>;
            })}
          </div>
        </>
      )}
    </section>
  );
}
