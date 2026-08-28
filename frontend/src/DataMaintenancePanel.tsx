import { useState } from "react";


type BackupSummary = {
  id: string;
  created_at: string;
  kind: "manual" | "pre_restore";
  size_bytes: number;
  verified: boolean;
};

type RestoreSummary = {
  restored_backup_id: string;
  safety_backup: BackupSummary;
};

const errorMessage = async (response: Response) => {
  const payload = (await response.json()) as { detail?: { message?: string } | string };
  if (payload.detail && typeof payload.detail === "object" && payload.detail.message) {
    return payload.detail.message;
  }
  return typeof payload.detail === "string" ? payload.detail : `HTTP ${response.status}`;
};

function DataMaintenancePanel() {
  const [backups, setBackups] = useState<BackupSummary[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const run = async (key: string, action: () => Promise<void>) => {
    setBusy(key);
    setMessage(null);
    setError(null);
    try {
      await action();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "処理に失敗しました。");
    } finally {
      setBusy(null);
    }
  };

  const createBackup = () => run("create", async () => {
    const response = await fetch("/api/data/backups", { method: "POST" });
    if (!response.ok) throw new Error(await errorMessage(response));
    const created = (await response.json()) as BackupSummary;
    setBackups((current) => [created, ...current]);
    setLoaded(true);
    setMessage("バックアップを作成し、整合性を検証しました。");
  });

  const loadBackups = () => run("load", async () => {
    const response = await fetch("/api/data/backups");
    if (!response.ok) throw new Error(await errorMessage(response));
    setBackups((await response.json()) as BackupSummary[]);
    setLoaded(true);
  });

  const verifyBackup = (backup: BackupSummary) => run(`verify-${backup.id}`, async () => {
    const response = await fetch(`/api/data/backups/${backup.id}/verify`, { method: "POST" });
    if (!response.ok) throw new Error(await errorMessage(response));
    const verified = (await response.json()) as BackupSummary;
    setBackups((current) => current.map((item) => item.id === verified.id ? verified : item));
    setMessage("バックアップを検証しました。");
  });

  const restoreBackup = (backup: BackupSummary) => {
    if (!window.confirm("現在のデータを保全してから、このバックアップを復元します。続行しますか？")) {
      return Promise.resolve();
    }
    return run(`restore-${backup.id}`, async () => {
      const response = await fetch(`/api/data/backups/${backup.id}/restore`, { method: "POST" });
      if (!response.ok) throw new Error(await errorMessage(response));
      const restored = (await response.json()) as RestoreSummary;
      setBackups((current) => [restored.safety_backup, ...current]);
      setMessage("復元しました。復元前のデータも保全されています。");
    });
  };

  return (
    <section className="panel maintenance-panel" aria-labelledby="data-maintenance-heading">
      <div className="panel-heading">
        <div>
          <span className="section-number">03</span>
          <h2 id="data-maintenance-heading">バックアップ・データ出力</h2>
        </div>
        <span className="local-badge">このPC内</span>
      </div>

      <div className="maintenance-actions">
        <button type="button" disabled={busy !== null} onClick={() => void loadBackups()}>
          {busy === "load" ? "読込中…" : "バックアップ一覧を表示"}
        </button>
        <button type="button" disabled={busy !== null} onClick={() => void createBackup()}>
          {busy === "create" ? "作成中…" : "バックアップを作成"}
        </button>
        <a href="/api/data/export?format=json" download>JSONをダウンロード</a>
        <a href="/api/data/export?format=csv" download>CSV ZIPをダウンロード</a>
      </div>

      {message && <p className="message" role="status">{message}</p>}
      {error && <p className="message error" role="alert">{error}</p>}

      <ul className="backup-list">
        {backups.map((backup) => (
          <li key={backup.id} aria-label={`バックアップ ${backup.id}`}>
            <div>
              <strong>{backup.kind === "manual" ? "手動バックアップ" : "復元前の保全"}</strong>
              <span>{backup.created_at}</span>
              <small>{(backup.size_bytes / 1024).toFixed(1)} KB / {backup.verified ? "検証済み" : "要確認"}</small>
            </div>
            <div className="backup-row-actions">
              <button
                type="button"
                disabled={busy !== null}
                onClick={() => void verifyBackup(backup)}
              >再検証</button>
              <button
                type="button"
                disabled={busy !== null || !backup.verified}
                onClick={() => void restoreBackup(backup)}
              >このバックアップを復元</button>
            </div>
          </li>
        ))}
      </ul>
      {loaded && backups.length === 0 && !error && <p className="message">バックアップはまだありません。</p>}
    </section>
  );
}

export default DataMaintenancePanel;
