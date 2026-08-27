import { useState } from "react";

type AnalysisTag = {
  id: number;
  rule_key: string;
  version: number;
  title: string;
  source_url: string;
  evidence_summary: string;
  study_period: string;
  population: string;
  evidence_quality: "A" | "B";
  conditions: Record<string, unknown>;
  enabled: boolean;
  probability_multiplier: number | null;
};

type AuditEvent = {
  id: number;
  rule_version_id: number;
  action: "enabled" | "disabled" | "version_created";
  reason: string;
  occurred_at: string;
};

const errorMessage = async (response: Response) => {
  const payload = (await response.json()) as { detail?: { message?: string } | string };
  return (typeof payload.detail === "object" ? payload.detail.message : payload.detail) ?? `HTTP ${response.status}`;
};

const getJson = async <T,>(url: string): Promise<T> => {
  const response = await fetch(url);
  if (!response.ok) throw new Error(await errorMessage(response));
  return (await response.json()) as T;
};

export default function AnalysisTagsPanel() {
  const [tags, setTags] = useState<AnalysisTag[] | null>(null);
  const [reasonById, setReasonById] = useState<Record<number, string>>({});
  const [conditionsById, setConditionsById] = useState<Record<number, string>>({});
  const [auditByKey, setAuditByKey] = useState<Record<string, AuditEvent[]>>({});
  const [versionsByKey, setVersionsByKey] = useState<Record<string, AnalysisTag[]>>({});
  const [message, setMessage] = useState("");

  const loadTags = async () => {
    try {
      const loaded = await getJson<AnalysisTag[]>("/api/analysis-tags");
      setTags(loaded);
      setConditionsById(Object.fromEntries(loaded.map((tag) => [tag.id, JSON.stringify(tag.conditions, null, 2)])));
      setMessage("7種類の分析タグを読み込みました。");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "分析タグを読み込めませんでした。");
    }
  };

  const changeState = async (tag: AnalysisTag) => {
    const reason = reasonById[tag.id]?.trim();
    if (!reason) return;
    try {
      const response = await fetch(`/api/analysis-tags/${tag.id}/state`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: !tag.enabled, reason }),
      });
      if (!response.ok) throw new Error(await errorMessage(response));
      const updated = (await response.json()) as AnalysisTag;
      setTags((current) => current?.map((item) => item.id === updated.id ? updated : item) ?? null);
      setReasonById((current) => ({ ...current, [tag.id]: "" }));
      setMessage(updated.enabled ? "分析タグを有効化しました。確率補正は行いません。" : "分析タグを無効化しました。");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "状態を変更できませんでした。");
    }
  };

  const createVersion = async (tag: AnalysisTag) => {
    const reason = reasonById[tag.id]?.trim();
    if (!reason) return;
    try {
      const conditions = JSON.parse(conditionsById[tag.id] ?? "{}") as Record<string, unknown>;
      const response = await fetch(`/api/analysis-tags/${tag.id}/versions`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ conditions, reason }),
      });
      if (!response.ok) throw new Error(await errorMessage(response));
      const replacement = (await response.json()) as AnalysisTag;
      setTags((current) => current?.map((item) => item.id === tag.id ? replacement : item) ?? null);
      setConditionsById((current) => ({ ...current, [replacement.id]: JSON.stringify(replacement.conditions, null, 2) }));
      setReasonById((current) => ({ ...current, [tag.id]: "" }));
      setMessage(`新版 v${replacement.version} を無効状態で作成しました。`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "条件JSONを確認してください。");
    }
  };

  const loadAudit = async (tag: AnalysisTag) => {
    try {
      const audit = await getJson<AuditEvent[]>(`/api/analysis-tags/${tag.rule_key}/audit`);
      setAuditByKey((current) => ({ ...current, [tag.rule_key]: audit }));
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "監査履歴を読み込めませんでした。");
    }
  };

  const loadVersions = async (tag: AnalysisTag) => {
    try {
      const versions = await getJson<AnalysisTag[]>(`/api/analysis-tags/${tag.rule_key}/versions`);
      setVersionsByKey((current) => ({ ...current, [tag.rule_key]: versions }));
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "版履歴を読み込めませんでした。");
    }
  };

  return (
    <section className="analysis-tags" aria-labelledby="analysis-tags-heading">
      <div className="tags-heading">
        <div>
          <h3 id="analysis-tags-heading">文献由来の分析タグ</h3>
          <p>前向き検証の層別用です。有効化しても予測確率や期待値候補は変更しません。</p>
        </div>
        <button type="button" onClick={() => void loadTags()}>分析タグを表示</button>
      </div>
      {message && <p className="workflow-message" role="status">{message}</p>}
      {tags?.map((tag) => (
        <details className="tag-card" key={tag.id}>
          <summary>
            <strong>{tag.title} v{tag.version}</strong>
            <span>{tag.enabled ? "有効（タグのみ）" : "無効"} / 証拠品質 {tag.evidence_quality} / 倍率なし</span>
          </summary>
          <p>{tag.evidence_summary}</p>
          <dl>
            <div><dt>対象期間</dt><dd>{tag.study_period}</dd></div>
            <div><dt>母集団</dt><dd>{tag.population}</dd></div>
            <div><dt>出典</dt><dd><a href={tag.source_url} target="_blank" rel="noreferrer">原資料を確認</a></dd></div>
          </dl>
          <label htmlFor={`conditions-${tag.id}`}>適用条件（JSON）</label>
          <textarea id={`conditions-${tag.id}`} rows={5} value={conditionsById[tag.id] ?? JSON.stringify(tag.conditions, null, 2)}
            onChange={(event) => setConditionsById((current) => ({ ...current, [tag.id]: event.target.value }))} />
          <label htmlFor={`tag-reason-${tag.id}`}>変更理由</label>
          <input id={`tag-reason-${tag.id}`} value={reasonById[tag.id] ?? ""}
            onChange={(event) => setReasonById((current) => ({ ...current, [tag.id]: event.target.value }))} />
          <div className="tag-actions">
            <button type="button" disabled={!reasonById[tag.id]?.trim()} onClick={() => void changeState(tag)}>{tag.enabled ? "無効化" : "有効化"}</button>
            <button type="button" disabled={!reasonById[tag.id]?.trim()} onClick={() => void createVersion(tag)}>条件を新版として保存</button>
            <button type="button" onClick={() => void loadVersions(tag)}>版履歴を表示</button>
            <button type="button" onClick={() => void loadAudit(tag)}>監査履歴を表示</button>
          </div>
          {versionsByKey[tag.rule_key] && <p className="tag-history">版履歴: {versionsByKey[tag.rule_key].map((version) => `v${version.version}`).join(" → ")}</p>}
          {auditByKey[tag.rule_key] && (
            <ul className="tag-history">{auditByKey[tag.rule_key].map((event) => (
              <li key={event.id}>{event.action} / {event.reason} / {event.occurred_at}</li>
            ))}</ul>
          )}
        </details>
      ))}
    </section>
  );
}
