import { useCallback, useEffect, useRef, useState } from "react";

import JstTimestamp from "./JstTimestamp";

type Opinion = {
  id: number; race_id: number; horse_number: number; mark: string | null;
  memo: string | null; skip_reason: string | null; recorded_at: string;
};
type Reflection = {
  id: number; race_id: number; horse_number: number; reflection: string; recorded_at: string;
};
type RunnerRecord = {
  horse_number: number; current_revision: Opinion | null;
  revisions: Opinion[]; reflections: Reflection[];
};
type PersonalRecords = { race_id: number; result_saved: boolean; runners: RunnerRecord[] };
type Draft = { mark: string; memo: string; skip_reason: string; reflection: string };

type Props = {
  raceId: number; refreshVersion: number;
  runners: Array<{ horse_number: number; horse_name: string }>;
};
type Feedback = { kind: "success" | "error"; message: string };
type TimedRecord = { id: number; recorded_at: string };

const emptyDraft = (): Draft => ({ mark: "", memo: "", skip_reason: "", reflection: "" });
const codePointLength = (value: string) => Array.from(value).length;

function upsertHistory<T extends TimedRecord>(history: T[], record: T): T[] {
  const byId = new Map(history.map((item) => [item.id, item]));
  byId.set(record.id, record);
  return [...byId.values()].sort((left, right) => {
    if (left.recorded_at !== right.recorded_at) return left.recorded_at < right.recorded_at ? -1 : 1;
    return left.id - right.id;
  });
}

function latestByAppendId<T extends { id: number }>(records: T[]): T | null {
  return records.reduce<T | null>((latest, item) => latest === null || item.id > latest.id ? item : latest, null);
}

function hasCurrentOpinion(record: RunnerRecord): boolean {
  return record.current_revision !== null && (
    record.current_revision.mark !== null || record.current_revision.memo !== null
    || record.current_revision.skip_reason !== null
  );
}

async function errorMessage(response: Response): Promise<{ message: string; code?: string }> {
  try {
    const payload = await response.json() as { detail?: { message?: string; code?: string } | string };
    return typeof payload.detail === "object" && payload.detail?.message
      ? { message: payload.detail.message, code: payload.detail.code }
      : { message: "保存できませんでした。入力内容を確認してください。" };
  } catch {
    return { message: "保存できませんでした。入力内容を確認してください。" };
  }
}

function createOperationId(): string {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export default function PersonalRunnerRecords({ raceId, refreshVersion, runners }: Props) {
  const [records, setRecords] = useState<PersonalRecords | null>(null);
  const [drafts, setDrafts] = useState<Record<number, Draft>>({});
  const [loadState, setLoadState] = useState<"loading" | "ready" | "error">("loading");
  const [savingHorses, setSavingHorses] = useState<Set<number>>(() => new Set());
  const [refreshing, setRefreshing] = useState(false);
  const [feedback, setFeedback] = useState<Record<number, Feedback>>({});
  const [refreshError, setRefreshError] = useState("");
  const activeRaceId = useRef<number | null>(null);
  const hasLoadedRecords = useRef(false);
  const savingHorsesRef = useRef<Set<number>>(new Set());
  const refreshingRef = useRef(false);
  const operationIds = useRef(new Map<string, { payload: string; id: string }>());

  const loadRecords = useCallback(async (signal?: AbortSignal) => {
    const response = await fetch(`/api/races/${raceId}/personal-runner-records`,
      signal ? { signal } : undefined);
    if (!response.ok) throw new Error("個人の記録を読み込めませんでした。");
    const payload = await response.json() as PersonalRecords;
    hasLoadedRecords.current = true;
    setRecords(payload);
    setDrafts((current) => Object.fromEntries(payload.runners.map((record) => [
      record.horse_number,
      current[record.horse_number] ?? {
        ...emptyDraft(),
        mark: record.current_revision?.mark ?? "",
        memo: record.current_revision?.memo ?? "",
        skip_reason: record.current_revision?.skip_reason ?? "",
      },
    ])));
    setLoadState("ready");
    setRefreshError("");
  }, [raceId]);

  useEffect(() => {
    const controller = new AbortController();
    if (activeRaceId.current !== raceId) {
      activeRaceId.current = raceId;
      operationIds.current.clear();
      hasLoadedRecords.current = false;
      setRecords(null);
      setDrafts({});
      setFeedback({});
      setLoadState("loading");
    }
    void loadRecords(controller.signal).catch((error: unknown) => {
      if (error instanceof DOMException && error.name === "AbortError") return;
      if (!hasLoadedRecords.current) setLoadState("error");
      else setRefreshError("個人の記録を再読み込みできませんでした。入力内容は保持しています。");
    });
    return () => controller.abort();
  }, [raceId, refreshVersion, loadRecords]);

  const refreshRecords = async () => {
    if (refreshingRef.current || loadState === "loading") return;
    refreshingRef.current = true;
    setRefreshing(true);
    try {
      await loadRecords();
    } catch {
      setRefreshError("個人の記録を再読み込みできませんでした。入力内容は保持しています。");
    } finally {
      refreshingRef.current = false;
      setRefreshing(false);
    }
  };

  const updateDraft = (horseNumber: number, update: Partial<Draft>) => {
    setDrafts((current) => ({ ...current, [horseNumber]: { ...emptyDraft(), ...current[horseNumber], ...update } }));
    if ("mark" in update || "memo" in update || "skip_reason" in update) {
      operationIds.current.delete(`${raceId}:${horseNumber}:opinion`);
    }
    if ("reflection" in update) operationIds.current.delete(`${raceId}:${horseNumber}:reflection`);
    setFeedback((current) => {
      const next = { ...current };
      delete next[horseNumber];
      return next;
    });
  };

  const beginSaving = (horseNumber: number): boolean => {
    if (savingHorsesRef.current.has(horseNumber)) return false;
    savingHorsesRef.current.add(horseNumber);
    setSavingHorses(new Set(savingHorsesRef.current));
    return true;
  };

  const finishSaving = (horseNumber: number) => {
    savingHorsesRef.current.delete(horseNumber);
    setSavingHorses(new Set(savingHorsesRef.current));
  };

  const operationFor = (key: string, payload: string) => {
    const current = operationIds.current.get(key);
    if (current?.payload === payload) return current.id;
    const id = createOperationId();
    operationIds.current.set(key, { payload, id });
    return id;
  };

  const setFeedbackFor = (horseNumber: number, kind: Feedback["kind"], message: string) => {
    setFeedback((current) => ({ ...current, [horseNumber]: { kind, message } }));
  };

  const saveOpinion = async (
    horseNumber: number, value: Pick<Draft, "mark" | "memo" | "skip_reason">,
    mode: "save" | "clear" = "save",
  ) => {
    const record = records?.runners.find((item) => item.horse_number === horseNumber);
    if (!record) return;
    const currentHasOpinion = hasCurrentOpinion(record);
    const hasOpinion = value.mark !== "" || value.memo !== "" || value.skip_reason !== "";
    if (mode === "clear" ? !currentHasOpinion
      : codePointLength(value.memo) + codePointLength(value.skip_reason) > 2000
        || (!hasOpinion && record.current_revision === null)) return;
    if (!beginSaving(horseNumber)) return;
    const draftAtSubmit = drafts[horseNumber] ?? emptyDraft();
    const payload = { mark: value.mark || null, memo: value.memo || null, skip_reason: value.skip_reason || null };
    const key = `${raceId}:${horseNumber}:opinion`;
    const payloadText = JSON.stringify(payload);
    const id = operationFor(key, payloadText);
    setFeedback((current) => { const next = { ...current }; delete next[horseNumber]; return next; });
    try {
      const response = await fetch(`/api/races/${raceId}/runners/${horseNumber}/personal-opinion-revisions`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ operation_id: id, ...payload }),
      });
      if (!response.ok) {
        const error = await errorMessage(response);
        if (response.status === 409) await loadRecords().catch(() => {
          setRefreshError("結果状態を再読み込みできませんでした。個人記録を再読込してください。");
        });
        setFeedbackFor(horseNumber, "error", error.message);
        return;
      }
      const revision = await response.json() as Opinion;
      if (operationIds.current.get(key)?.id === id) operationIds.current.delete(key);
      const knownCurrentRevision = records?.runners.find((runner) => runner.horse_number === horseNumber)
        ?.current_revision ?? null;
      const latestRevision = knownCurrentRevision === null || revision.id > knownCurrentRevision.id
        ? revision : knownCurrentRevision;
      setRecords((current) => current === null ? current : ({
        ...current,
        runners: current.runners.map((runner) => runner.horse_number !== horseNumber ? runner : ({
          ...runner,
          current_revision: latestByAppendId(upsertHistory(runner.revisions, revision)),
          revisions: upsertHistory(runner.revisions, revision),
        })),
      }));
      setDrafts((current) => ({
        ...current,
        [horseNumber]: {
          ...emptyDraft(),
          ...current[horseNumber],
          ...(current[horseNumber]?.mark === draftAtSubmit.mark
            && current[horseNumber]?.memo === draftAtSubmit.memo
            && current[horseNumber]?.skip_reason === draftAtSubmit.skip_reason
            ? { mark: latestRevision.mark ?? "", memo: latestRevision.memo ?? "", skip_reason: latestRevision.skip_reason ?? "" }
            : {}),
        },
      }));
      setFeedbackFor(horseNumber, "success", "保存しました。記録の履歴に追加しました。");
    } catch {
      setFeedbackFor(horseNumber, "error", "通信できませんでした。入力内容を保ちました。同じ内容で再試行できます。");
    } finally {
      finishSaving(horseNumber);
    }
  };

  const saveReflection = async (horseNumber: number, reflection: string) => {
    if (!records?.result_saved || !records.runners.some((item) => item.horse_number === horseNumber)
      || !reflection.trim() || codePointLength(reflection) > 2000) return;
    if (!beginSaving(horseNumber)) return;
    const draftAtSubmit = drafts[horseNumber] ?? emptyDraft();
    const key = `${raceId}:${horseNumber}:reflection`;
    const payloadText = JSON.stringify({ reflection });
    const id = operationFor(key, payloadText);
    setFeedback((current) => { const next = { ...current }; delete next[horseNumber]; return next; });
    try {
      const response = await fetch(`/api/races/${raceId}/runners/${horseNumber}/personal-reflections`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ operation_id: id, reflection }),
      });
      if (!response.ok) {
        const error = await errorMessage(response);
        if (response.status === 409) await loadRecords().catch(() => {
          setRefreshError("結果状態を再読み込みできませんでした。個人記録を再読込してください。");
        });
        setFeedbackFor(horseNumber, "error", error.message);
        return;
      }
      const saved = await response.json() as Reflection;
      if (operationIds.current.get(key)?.id === id) operationIds.current.delete(key);
      setRecords((current) => current === null ? current : ({
        ...current,
        runners: current.runners.map((runner) => runner.horse_number !== horseNumber ? runner : ({
          ...runner, reflections: upsertHistory(runner.reflections, saved),
        })),
      }));
      setDrafts((current) => ({
        ...current,
        [horseNumber]: {
          ...emptyDraft(),
          ...current[horseNumber],
          ...(current[horseNumber]?.reflection === draftAtSubmit.reflection ? { reflection: "" } : {}),
        },
      }));
      setFeedbackFor(horseNumber, "success", "振り返りを追加しました。");
    } catch {
      setFeedbackFor(horseNumber, "error", "通信できませんでした。入力内容を保ちました。同じ内容で再試行できます。");
    } finally {
      finishSaving(horseNumber);
    }
  };

  return <section className="personal-records" aria-labelledby="personal-records-heading">
    <div className="personal-records-heading">
      <div><h3 id="personal-records-heading">自分の記録</h3>
        <p>個人の印やメモは、システム判定・購入記録とは別に保存します。</p>
      </div>
      <button type="button" aria-disabled={refreshing || loadState === "loading"}
        aria-busy={refreshing || loadState === "loading"}
        onClick={() => void refreshRecords()}>
        {refreshing ? "個人記録を再読込中…" : "個人記録を再読込"}
      </button>
      {loadState === "ready" && records?.result_saved && <strong>結果保存後</strong>}
    </div>
    {refreshError && <p role="alert">{refreshError}</p>}
    {loadState === "loading" && <p role="status">個人の記録を読み込んでいます…</p>}
    {loadState === "error" && <p role="alert">個人の記録を読み込めませんでした。</p>}
    {loadState === "ready" && records && <div className="personal-runner-list">
      {runners.map((runner) => {
        const record = records.runners.find((item) => item.horse_number === runner.horse_number);
        if (!record) return null;
        const draft = drafts[runner.horse_number] ?? emptyDraft();
        const opinionLength = codePointLength(draft.memo) + codePointLength(draft.skip_reason);
        const hasOpinion = draft.mark !== "" || draft.memo !== "" || draft.skip_reason !== "";
        const isSaving = savingHorses.has(runner.horse_number);
        const canSaveOpinion = opinionLength <= 2000 && (hasOpinion || record.current_revision !== null);
        const canClearOpinion = hasCurrentOpinion(record);
        const canSaveReflection = records.result_saved && Boolean(draft.reflection.trim())
          && codePointLength(draft.reflection) <= 2000;
        return <article key={runner.horse_number} className="personal-runner-record">
          <h4>{runner.horse_number}番 {runner.horse_name}</h4>
          <div className="personal-opinion-form">
            <label>自分の印
              <select aria-label={`${runner.horse_number}番 自分の印`} value={draft.mark}
                onChange={(event) => updateDraft(runner.horse_number, { mark: event.currentTarget.value })}>
                <option value="">未設定</option>
                {["◎", "○", "▲", "△", "☆", "消"].map((mark) => <option key={mark} value={mark}>{mark}</option>)}
              </select>
            </label>
            <label>自分のメモ
              <textarea aria-label={`${runner.horse_number}番 自分のメモ`} value={draft.memo}
                aria-describedby={`opinion-count-${runner.horse_number}`}
                onChange={(event) => updateDraft(runner.horse_number, { memo: event.currentTarget.value })} />
            </label>
            <label>自分の見送り理由
              <textarea aria-label={`${runner.horse_number}番 自分の見送り理由`} value={draft.skip_reason}
                aria-describedby={`opinion-count-${runner.horse_number}`}
                onChange={(event) => updateDraft(runner.horse_number, { skip_reason: event.currentTarget.value })} />
            </label>
            <p id={`opinion-count-${runner.horse_number}`} className="personal-character-count">
              メモ・見送り理由 {opinionLength}/2000文字
            </p>
            <div className="personal-record-actions">
              <button type="button" aria-disabled={isSaving || !canSaveOpinion} aria-busy={isSaving}
                onClick={() => void saveOpinion(runner.horse_number, draft)}>
                {isSaving ? `${runner.horse_number}番の保存中…` : `${runner.horse_number}番の意見を履歴に保存`}
              </button>
              <button type="button" aria-disabled={isSaving || !canClearOpinion} aria-busy={isSaving}
                onClick={() => void saveOpinion(runner.horse_number,
                  { mark: "", memo: "", skip_reason: "" }, "clear")}>
                {runner.horse_number}番の記録をクリア
              </button>
            </div>
          </div>
          <section className="personal-record-history" aria-label={`${runner.horse_number}番 記録の履歴`}>
            <h5>記録の履歴</h5>
            {record.revisions.length === 0 ? <p>まだ記録はありません。</p> : <ol>
              {record.revisions.map((revision) => <li key={revision.id}>
                <JstTimestamp value={revision.recorded_at} />
                <p>自分の印: {revision.mark ?? "未設定"}</p>
                <p>自分のメモ: {revision.memo ?? "—"}</p>
                <p>自分の見送り理由: {revision.skip_reason ?? "—"}</p>
              </li>)}
            </ol>}
          </section>
          <section className="personal-reflections" aria-label={`${runner.horse_number}番 自分の振り返り`}>
            <h5>自分の振り返り</h5>
            {!records.result_saved && <p>振り返りは結果保存後に記録できます。</p>}
            <label>振り返りを追記
              <textarea aria-label={`${runner.horse_number}番 振り返りを追記`}
                aria-describedby={`reflection-count-${runner.horse_number}`} value={draft.reflection}
                disabled={!records.result_saved}
                onChange={(event) => updateDraft(runner.horse_number, { reflection: event.currentTarget.value })} />
            </label>
            <p id={`reflection-count-${runner.horse_number}`} className="personal-character-count">
              {codePointLength(draft.reflection)}/2000文字
            </p>
            <button type="button" aria-disabled={isSaving || !canSaveReflection} aria-busy={isSaving}
              onClick={() => void saveReflection(runner.horse_number, draft.reflection)}>
              {runner.horse_number}番の振り返りを追記
            </button>
            {record.reflections.length === 0 ? <p>振り返りはまだありません。</p> : <ol>
              {record.reflections.map((reflection) => <li key={reflection.id}>
                <JstTimestamp value={reflection.recorded_at} />
                <p>{reflection.reflection}</p>
              </li>)}
            </ol>}
          </section>
          {isSaving && <p className="personal-save-status" role="status">
            {runner.horse_number}番の記録を保存しています。
          </p>}
          {feedback[runner.horse_number] && <p className="personal-record-feedback"
            role={feedback[runner.horse_number].kind === "success" ? "status" : "alert"}>
            {feedback[runner.horse_number].message}
          </p>}
        </article>;
      })}
    </div>}
  </section>;
}
