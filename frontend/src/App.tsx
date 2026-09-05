import { useEffect, useState } from "react";
import PredictionPanel from "./PredictionPanel";
import AnalysisTagsPanel from "./AnalysisTagsPanel";
import BettingPanel from "./BettingPanel";
import DataMaintenancePanel from "./DataMaintenancePanel";
import JraRaceAcquisitionPanel from "./JraRaceAcquisitionPanel";
import EvaluationPanel from "./EvaluationPanel";
import MarketAttentionPanel from "./MarketAttentionPanel";
import RuleJudgementPanel from "./RuleJudgementPanel";
import MarketRuleComparisonPanel from "./MarketRuleComparisonPanel";
import WeeklyRaceWorkspace from "./WeeklyRaceWorkspace";

type HealthStatus = "ok" | "error";

type ComponentHealth = { status: "ok" };

type HealthResponse = {
  service: string;
  status: "ok" | "degraded";
  api: ComponentHealth;
  database: { status: HealthStatus; engine: "sqlite" };
};

type LoadState =
  | { kind: "loading" }
  | { kind: "ready"; health: HealthResponse }
  | { kind: "error"; message: string };

type RaceAnalysis = {
  race_id: number;
  race: {
    organizer: string; country: string; racecourse: string; race_date: string; race_number: number; start_time: string;
    timezone: string; start_utc: string;
    surface: string; distance_m: number; going: string; field_size: number;
  };
  runners: Array<{
    horse_number: number; horse_name: string; win_odds: number;
    raw_inverse_win_odds: number; normalized_win_market_share: number;
    place_odds_min: number; place_odds_max: number;
    place_break_even_hit_rate: { minimum: number; midpoint: number; maximum: number } | null;
  }>;
  candidate_status: string;
  candidate_reason: string;
};

type RaceListItem = { race_id: number; race: RaceAnalysis["race"] };

type ImportIssue = { row: number; column: string; code: string; description: string };
type ImportErrorDetail = { message: string; errors?: ImportIssue[] };

const statusLabel = (status: HealthStatus) => (status === "ok" ? "稼働中" : "停止中");
const latestRaceFirst = (items: RaceListItem[]) => [...items].sort(
  (left, right) => right.race.start_utc.localeCompare(left.race.start_utc) || right.race_id - left.race_id,
);

function App() {
  const [activeArea, setActiveArea] = useState<"weekly" | "evaluation" | "import" | "settings">("weekly");
  const [loadState, setLoadState] = useState<LoadState>({ kind: "loading" });
  const [csvFile, setCsvFile] = useState<File | null>(null);
  const [analysis, setAnalysis] = useState<RaceAnalysis | null>(null);
  const [races, setRaces] = useState<RaceListItem[]>([]);
  const [raceListState, setRaceListState] = useState<"loading" | "ready" | "error">("loading");
  const [analysisState, setAnalysisState] = useState<"idle" | "loading" | "error">("idle");
  const [analysisError, setAnalysisError] = useState("");
  const [selectedRaceId, setSelectedRaceId] = useState<number | null>(null);
  const [importState, setImportState] = useState<"idle" | "loading" | "error">("idle");
  const [importError, setImportError] = useState<ImportErrorDetail | null>(null);

  const showAnalysis = (loaded: RaceAnalysis) => {
    setAnalysis(loaded);
    setSelectedRaceId(loaded.race_id);
    setRaces((current) => latestRaceFirst([
      { race_id: loaded.race_id, race: loaded.race },
      ...current.filter((item) => item.race_id !== loaded.race_id),
    ]));
    setRaceListState("ready");
  };

  const loadRaceList = async (signal?: AbortSignal) => {
    try {
      const response = await fetch("/api/races", { signal });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const payload = await response.json() as unknown;
      if (!Array.isArray(payload)) throw new Error("登録済みレース一覧の形式が不正です。");
      setRaces(latestRaceFirst(payload as RaceListItem[]));
      setRaceListState("ready");
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return;
      setRaceListState("error");
    }
  };

  const loadRace = async (raceId: number) => {
    setAnalysisState("loading");
    setAnalysisError("");
    setAnalysis(null);
    setSelectedRaceId(raceId);
    try {
      const response = await fetch(`/api/races/${raceId}`);
      if (!response.ok) {
        throw new Error(response.status === 404
          ? "レースが見つかりません。登録済み一覧を再読み込みするか、JRAのレース情報とオッズを登録してください。"
          : "市場分析を読み込めませんでした。再読み込みするか、CSV取込を使用してください。");
      }
      const loaded = (await response.json()) as RaceAnalysis;
      showAnalysis(loaded);
      setAnalysisState("idle");
    } catch (error) {
      setAnalysisError(error instanceof Error ? error.message : "レースを読み込めませんでした。");
      setAnalysisState("error");
    }
  };

  useEffect(() => {
    const controller = new AbortController();

    const loadHealth = async () => {
      try {
        const response = await fetch("/api/health", { signal: controller.signal });
        if (response.status !== 200 && response.status !== 503) {
          throw new Error(`HTTP ${response.status}`);
        }
        const health = (await response.json()) as HealthResponse;
        setLoadState({ kind: "ready", health });
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") {
          return;
        }
        const message = error instanceof Error ? error.message : "不明なエラー";
        setLoadState({ kind: "error", message });
      }
    };

    void loadHealth();
    void loadRaceList(controller.signal);
    return () => controller.abort();
  }, []);

  const importCsv = async () => {
    if (!csvFile) return;
    setImportState("loading");
    try {
      const response = await fetch("/api/races/import", {
        method: "POST",
        headers: { "Content-Type": "text/csv; charset=utf-8" },
        body: csvFile,
      });
      if (!response.ok) {
        const payload = (await response.json()) as { detail?: string | ImportErrorDetail };
        if (payload.detail && typeof payload.detail === "object") {
          setImportError(payload.detail);
          setImportState("error");
          return;
        }
        throw new Error(payload.detail ?? `HTTP ${response.status}`);
      }
      const imported = (await response.json()) as RaceAnalysis;
      showAnalysis(imported);
      setImportState("idle");
    } catch (error) {
      setImportError({ message: error instanceof Error ? error.message : "不明なエラー" });
      setImportState("error");
    }
  };

  return (
    <main className="shell">
      <header className="hero">
        <div className="eyebrow">LOCAL-FIRST / JRA ANALYTICS</div>
        <h1>Horse Racing Analytics</h1>
        <p>
          予測を結果より前に固定し、市場基準と実績を同じ条件で検証するための分析基盤です。
        </p>
      </header>

      <nav className="primary-nav" aria-label="主要画面">
        {([
          ["weekly", "今週のレース"], ["evaluation", "成績・検証"],
          ["import", "データ取込"], ["settings", "設定・バックアップ"],
        ] as const).map(([area, label]) => <button type="button" key={area}
          aria-current={activeArea === area ? "page" : undefined} onClick={() => setActiveArea(area)}>{label}</button>)}
      </nav>

      <section className="panel" aria-labelledby="system-status-heading">
        <div className="panel-heading">
          <div>
            <span className="section-number">01</span>
            <h2 id="system-status-heading">システム状態</h2>
          </div>
          <span className="local-badge">このPCのみ</span>
        </div>

        {loadState.kind === "loading" && (
          <p className="message" role="status">接続状態を確認しています…</p>
        )}

        {loadState.kind === "error" && (
          <div className="message error" role="alert">
            <strong>接続できませんでした</strong>
            <span>APIサーバーを確認してください（{loadState.message}）。</span>
          </div>
        )}

        {loadState.kind === "ready" && (
          <div className="status-grid">
            <article className="status-card" aria-label="APIの状態">
              <div className="status-card-label">API</div>
              <div className="status-value">
                <span className="status-dot" aria-hidden="true" />
                {statusLabel(loadState.health.api.status)}
              </div>
              <p>分析処理を受け付けられます。</p>
            </article>

            <article className="status-card" aria-label="データベースの状態">
              <div className="status-card-label">DATABASE</div>
              <div className="status-value">
                <span
                  className={`status-dot ${loadState.health.database.status === "error" ? "stopped" : ""}`}
                  aria-hidden="true"
                />
                {statusLabel(loadState.health.database.status)}
              </div>
              <p>
                {loadState.health.database.status === "ok"
                  ? "SQLiteへ接続できています。"
                  : "SQLiteへ接続できません。"}
              </p>
            </article>
          </div>
        )}
      </section>

      {activeArea === "weekly" && <WeeklyRaceWorkspace onNavigate={setActiveArea} />}

      {activeArea === "import" && <><JraRaceAcquisitionPanel onRaceRegistered={loadRace} />

      <section className="panel analysis-panel" aria-labelledby="race-analysis-heading">
        <div className="panel-heading">
          <div>
            <span className="section-number">04</span>
            <h2 id="race-analysis-heading">1レース市場分析</h2>
          </div>
          <span className="local-badge">登録済み / CSV</span>
        </div>
        <section aria-labelledby="registered-races-heading">
          <h3 id="registered-races-heading">登録済みレース</h3>
          {raceListState === "loading" && <p role="status">登録済みレースを読み込んでいます…</p>}
          {raceListState === "error" && (
            <div className="import-error" role="alert">
              <strong>登録済みレースを読み込めませんでした。</strong>
              <button type="button" onClick={() => void loadRaceList()}>再読み込み</button>
            </div>
          )}
          {raceListState === "ready" && races.length === 0 && (
            <p className="empty-note">登録済みレースはありません。JRAレース情報とオッズを登録するか、CSVを取り込んでください。</p>
          )}
          {races.length > 0 && (
            <div className="maintenance-actions" aria-label="登録済みレース一覧">
              {races.map((item) => (
                <button
                  type="button"
                  key={item.race_id}
                  aria-pressed={selectedRaceId === item.race_id}
                  onClick={() => void loadRace(item.race_id)}
                >
                  {item.race.race_date} {item.race.racecourse} {item.race.race_number}R
                </button>
              ))}
            </div>
          )}
          {analysisState === "loading" && <p role="status">市場分析を読み込んでいます…</p>}
          {analysisState === "error" && (
            <div className="import-error" role="alert">
              <strong>{analysisError}</strong>
              {selectedRaceId !== null && (
                <button type="button" onClick={() => void loadRace(selectedRaceId)}>選択したレースを再読み込み</button>
              )}
            </div>
          )}
        </section>
        <div className="import-form">
          <label htmlFor="race-csv">CSVファイル</label>
          <input
            id="race-csv"
            type="file"
            accept=".csv,text/csv"
            onChange={(event) => setCsvFile(event.target.files?.[0] ?? null)}
          />
          <button type="button" disabled={!csvFile || importState === "loading"} onClick={() => void importCsv()}>
            {importState === "loading" ? "分析中…" : "取り込んで分析"}
          </button>
          {importState === "error" && importError && (
            <div className="import-error" role="alert">
              <strong>{importError.message}</strong>
              {importError.errors && (
                <ul>{importError.errors.map((error) => (
                  <li key={`${error.row}-${error.column}-${error.code}`}>
                    <span>{error.row}行目 / {error.column} / {error.code}</span>
                    <p>{error.description}</p>
                  </li>
                ))}</ul>
              )}
            </div>
          )}
        </div>

        {analysis && (
          <div className="analysis-result">
            <div className="race-heading">
              <div>
                <h3>{analysis.race.racecourse} {analysis.race.race_number}R</h3>
                <p>{analysis.race.race_date} {analysis.race.start_time} / {analysis.race.surface}{analysis.race.distance_m}m / {analysis.race.going}</p>
              </div>
              <strong>{analysis.race.field_size}頭</strong>
            </div>
            <div className="table-wrap">
              <table>
                <thead><tr>
                  <th>馬番・馬名</th><th>単勝オッズ</th><th>生逆オッズ</th>
                  <th>単勝市場投票シェア</th><th>複勝オッズ</th><th>複勝損益分岐的中率（下限 / 代表 / 上限）</th>
                </tr></thead>
                <tbody>{analysis.runners.map((runner) => (
                  <tr key={runner.horse_number}>
                    <td><b>{runner.horse_number}</b> {runner.horse_name}</td>
                    <td>{runner.win_odds.toFixed(1)}</td>
                    <td>{formatPercent(runner.raw_inverse_win_odds)}</td>
                    <td>{formatPercent(runner.normalized_win_market_share)}</td>
                    <td>{runner.place_odds_min.toFixed(1)} 〜 {runner.place_odds_max.toFixed(1)}</td>
                    <td>{runner.place_break_even_hit_rate
                      ? `${formatPercent(runner.place_break_even_hit_rate.minimum)} / ${formatPercent(runner.place_break_even_hit_rate.midpoint)} / ${formatPercent(runner.place_break_even_hit_rate.maximum)}`
                      : "対象外（4頭以下）"}</td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
            <aside className="candidate-empty">
              <strong>{analysis.candidate_status}</strong>
              <span>{analysis.candidate_reason}</span>
            </aside>
            <MarketAttentionPanel raceId={analysis.race_id} />
            <RuleJudgementPanel raceId={analysis.race_id} />
            <MarketRuleComparisonPanel raceId={analysis.race_id} />
            <PredictionPanel
              key={analysis.race_id}
              raceId={analysis.race_id}
              runners={analysis.runners.map((runner) => ({
                horse_number: runner.horse_number,
                win_odds: runner.win_odds,
                place_odds_min: runner.place_odds_min,
                place_odds_max: runner.place_odds_max,
              }))}
            />
            <BettingPanel
              raceId={analysis.race_id}
              runners={analysis.runners.map((runner) => ({
                horse_number: runner.horse_number, horse_name: runner.horse_name,
              }))}
            />
          </div>
        )}
      </section></>}

      {activeArea === "evaluation" && <><section className="panel analysis-panel" aria-label="分析タグ管理">
        <AnalysisTagsPanel />
      </section>

      <EvaluationPanel /></>}

      {activeArea === "settings" && <DataMaintenancePanel />}

      <footer>
          <span>個人運用MVP / チケット19</span>
          <span>開催週から注目レースを絞り、固定した判断材料を確認</span>
      </footer>
    </main>
  );
}

const formatPercent = (value: number) => `${(value * 100).toFixed(2)}%`;

export default App;
