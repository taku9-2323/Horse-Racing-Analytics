import { useCallback, useEffect, useRef, useState } from "react";

export type RaceState = "schedule_only" | "entries_waiting" | "odds_waiting" | "judgement_waiting" | "ready" | "stopped";

export type MeetingWeekRace = {
  race_date: string; racecourse: string; meeting_number: number; meeting_day: number;
  race_number: number; race_name: string; start_time: string; surface: string;
  distance_m: number; condition_text: string; state: RaceState; race_id: number | null;
  card_id: number | null; snapshot_id: number | null; judgement_id: number | null;
  rule_version_id: number | null; judgement_as_of: string | null;
  judgement_frozen_at: string | null; odds_observed_at: string | null;
  attention_horse_count: number | null; judged_runner_count: number | null;
  attention_ratio: number | null; attention_level: "none" | "low" | "medium" | "high" | null;
  display_state: "entries_waiting" | "odds_waiting" | "judgement_waiting" | "no_attention" | "attention" | "acquisition_failed" | "post_start";
  has_started: boolean;
  error_code: string | null; updated_at: string;
};

export type MeetingWeekSummary = {
  run_id: number; week_start: string; week_end: string; started_at: string;
  completed_at: string | null; status: "running" | "completed" | "stopped";
  target_count: number; processed_count: number; ready_count: number;
  waiting_count: number; failed_count: number; stop_reason: string | null;
  last_target: string | null; last_updated_at: string; races: MeetingWeekRace[];
};

const stateLabels: Record<RaceState, string> = {
  schedule_only: "日程のみ",
  entries_waiting: "出馬表待ち",
  odds_waiting: "オッズ待ち",
  judgement_waiting: "判定待ち",
  ready: "判定まで準備済み",
  stopped: "取得停止",
};

const parseError = async (response: Response, fallback: string) => {
  try {
    const payload = (await response.json()) as { detail?: { message?: string } | string };
    if (typeof payload.detail === "string") return payload.detail;
    return payload.detail?.message ?? fallback;
  } catch {
    return fallback;
  }
};

type Props = {
  onSummaryChange?: (summary: MeetingWeekSummary | null) => void;
  onNavigateToImport?: () => void;
  showRaceList?: boolean;
};

export default function MeetingWeekAcquisitionPanel({
  onSummaryChange, onNavigateToImport, showRaceList = true,
}: Props) {
  const [summary, setSummary] = useState<MeetingWeekSummary | null>(null);
  const [state, setState] = useState<"loading" | "idle" | "starting" | "error">("loading");
  const [message, setMessage] = useState("");
  const timer = useRef<number | null>(null);

  const clearPoll = () => {
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = null;
  };

  const loadCurrent = useCallback(async (signal?: AbortSignal): Promise<MeetingWeekSummary | null> => {
    const response = await fetch("/api/acquisition/jra/meeting-weeks/current", { signal });
    if (response.status === 404) {
      setSummary(null);
      onSummaryChange?.(null);
      setState("idle");
      return null;
    }
    if (!response.ok) throw new Error(await parseError(response, "開催週の取得状況を読み込めませんでした。"));
    const loaded = (await response.json()) as MeetingWeekSummary;
    setSummary(loaded);
    onSummaryChange?.(loaded);
    setState("idle");
    return loaded;
  }, [onSummaryChange]);

  const poll = useCallback(async () => {
    try {
      const loaded = await loadCurrent();
      if (loaded?.status === "running") {
        timer.current = window.setTimeout(() => void poll(), 1500);
      }
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "開催週の取得状況を読み込めませんでした。");
      setState("error");
    }
  }, [loadCurrent]);

  useEffect(() => {
    const controller = new AbortController();
    void loadCurrent(controller.signal).then((loaded) => {
      if (loaded?.status === "running") timer.current = window.setTimeout(() => void poll(), 1500);
    }).catch((error: unknown) => {
      if (error instanceof DOMException && error.name === "AbortError") return;
      setMessage(error instanceof Error ? error.message : "開催週の取得状況を読み込めませんでした。");
      setState("error");
    });
    return () => { controller.abort(); clearPoll(); };
  }, [loadCurrent, poll]);

  const start = async () => {
    clearPoll();
    setState("starting");
    setMessage("");
    try {
      const response = await fetch("/api/acquisition/jra/meeting-weeks/current/runs", { method: "POST" });
      if (!response.ok && response.status !== 409) {
        throw new Error(await parseError(response, "開催週の取得を開始できませんでした。"));
      }
      if (response.ok) {
        const started = (await response.json()) as MeetingWeekSummary;
        setSummary(started);
        onSummaryChange?.(started);
      }
      await poll();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "開催週の取得を開始できませんでした。");
      setState("error");
    }
  };

  const isRunning = summary?.status === "running" || state === "starting";
  const buttonLabel = summary === null ? "開催週のレースを取得"
    : summary.status === "stopped" ? "未取得・失敗分を再試行" : "開催週のレースを更新";

  return <section className="panel meeting-week-panel" aria-labelledby="meeting-week-heading">
    <div className="panel-heading">
      <div><span className="section-number">02</span><h2 id="meeting-week-heading">開催週のレース取得</h2></div>
      <span className="local-badge">利用者操作のみ</span>
    </div>
    <div className="meeting-week-actions">
      <div>
        <p>開いた日の週に開催されるJRAレースを、取得できた情報まで保存します。</p>
        {summary && <strong>{summary.week_start} — {summary.week_end}</strong>}
        {summary && <small className="last-acquired-at">最終取得 {summary.last_updated_at}</small>}
      </div>
      <button type="button" disabled={isRunning} onClick={() => void start()}>
        {isRunning ? "開催週を取得中…" : buttonLabel}
      </button>
    </div>
    {state === "loading" && <p className="message" role="status">開催週の取得状況を確認しています…</p>}
    {state === "error" && <div className="import-error" role="alert"><strong>{message}</strong></div>}
    {summary && <div className="meeting-week-result">
      <div className="meeting-week-progress" aria-live="polite">
        <strong>{summary.processed_count} / {summary.target_count} レース処理済み</strong>
        <progress value={summary.processed_count} max={Math.max(summary.target_count, 1)} />
        <span>準備済み {summary.ready_count} / 待機 {summary.waiting_count} / 失敗 {summary.failed_count}</span>
      </div>
      {summary.status === "stopped" && <div className="import-error" role="alert">
        <strong>{summary.stop_reason ?? "開催週の取得を停止しました。"}</strong>
        {onNavigateToImport
          ? <button type="button" onClick={onNavigateToImport}>データ取込へ移動</button>
          : <a href="#race-csv">CSV取込へ移動</a>}
      </div>}
      {showRaceList && summary.races.length > 0 && <ul className="meeting-week-races" aria-label="取得済みレース">
        {summary.races.map((race) => <li key={`${race.race_date}-${race.racecourse}-${race.race_number}`}>
          <time dateTime={`${race.race_date}T${race.start_time}`}>{race.race_date} {race.start_time}</time>
          <strong>{race.racecourse} {race.race_number}R</strong>
          <span>{race.race_name}</span>
          <span className={`race-state ${race.state}`}>{stateLabels[race.state]}</span>
        </li>)}
      </ul>}
    </div>}
  </section>;
}
