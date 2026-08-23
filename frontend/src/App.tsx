import { useEffect, useState } from "react";

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

const statusLabel = (status: HealthStatus) => (status === "ok" ? "稼働中" : "停止中");

function App() {
  const [loadState, setLoadState] = useState<LoadState>({ kind: "loading" });

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
    return () => controller.abort();
  }, []);

  return (
    <main className="shell">
      <header className="hero">
        <div className="eyebrow">LOCAL-FIRST / JRA ANALYTICS</div>
        <h1>Horse Racing Analytics</h1>
        <p>
          予測を結果より前に固定し、市場基準と実績を同じ条件で検証するための分析基盤です。
        </p>
      </header>

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

      <footer>
        <span>チケット01</span>
        <span>ローカルアプリを起動する</span>
      </footer>
    </main>
  );
}

export default App;
