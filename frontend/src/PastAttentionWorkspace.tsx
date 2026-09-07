import { useEffect, useMemo, useRef, useState } from "react";

import RaceDecisionDetail, { type DecisionView } from "./RaceDecisionDetail";


type PastAttentionHorse = {
  race_id: number; race_date: string; racecourse: string; race_number: number; start_time: string;
  horse_number: number; horse_name: string; pre_race_attention: boolean; post_start_attention: boolean;
  pre_race_snapshot_id: number | null; pre_race_judgement_id: number | null;
  post_start_snapshot_id: number | null; post_start_judgement_id: number | null;
  result_status: "確定" | "取消" | "除外" | "競走中止" | null;
  finish_position: number | null; has_result_correction: boolean;
};

type PastAttentionWeek = {
  week_start: string; week_end: string; horses: PastAttentionHorse[];
};

type PastAttentionPage = {
  page: number; weeks_per_page: number; total_week_count: number;
  has_newer: boolean; has_older: boolean; weeks: PastAttentionWeek[];
};

type BulkResultRun = {
  run_id: number; page: number; status: "running" | "completed" | "stopped";
  started_at: string; completed_at: string | null;
  target_count: number; processed_count: number; succeeded_count: number; missing_count: number;
  failed_count: number;
  stop_reason: string | null;
  targets: Array<{ race_id: number; race_date: string; racecourse: string; race_number: number;
    status: "pending" | "running" | "succeeded" | "missing" | "failed" | "stopped";
    error_code: string | null; error_message: string | null }>;
};

type ResultFilter = "all" | "first" | "placed" | "other" | "withdrawn" | "missing";
type TimingFilter = "all" | "pre" | "post";

const dateParts = (value: string) => {
  const [year, month, day] = value.split("-").map(Number);
  return { year, month, day };
};

const weekLabel = (start: string, end: string) => {
  const from = dateParts(start);
  const to = dateParts(end);
  return `${from.year}年${from.month}月${from.day}日〜${to.month}月${to.day}日`;
};

const resultLabel = (horse: PastAttentionHorse) => {
  if (horse.result_status === null) return "結果未取得";
  if (horse.result_status !== "確定") return horse.result_status;
  return horse.finish_position === null ? "着順不明" : `${horse.finish_position}着`;
};

const matchesResult = (horse: PastAttentionHorse, filter: ResultFilter) => {
  if (filter === "all") return true;
  if (filter === "missing") return horse.result_status === null;
  if (filter === "withdrawn") return horse.result_status !== null && horse.result_status !== "確定";
  if (horse.result_status !== "確定" || horse.finish_position === null) return false;
  if (filter === "first") return horse.finish_position === 1;
  if (filter === "placed") return horse.finish_position >= 2 && horse.finish_position <= 3;
  return horse.finish_position >= 4;
};

type Props = { onNavigate?: (area: "evaluation" | "import" | "settings") => void };

export default function PastAttentionWorkspace({ onNavigate }: Props) {
  const [pageNumber, setPageNumber] = useState(1);
  const [page, setPage] = useState<PastAttentionPage | null>(null);
  const [loadState, setLoadState] = useState<"loading" | "ready" | "error">("loading");
  const [resultFilter, setResultFilter] = useState<ResultFilter>("all");
  const [selectedDate, setSelectedDate] = useState("all");
  const [selectedCourse, setSelectedCourse] = useState("all");
  const [timingFilter, setTimingFilter] = useState<TimingFilter>("all");
  const [detail, setDetail] = useState<DecisionView | null>(null);
  const [selectedHorseNumber, setSelectedHorseNumber] = useState<number>();
  const [detailState, setDetailState] = useState<"idle" | "loading" | "error">("idle");
  const [bulkRun, setBulkRun] = useState<BulkResultRun | null>(null);
  const [bulkState, setBulkState] = useState<"idle" | "starting" | "error">("idle");
  const [refreshVersion, setRefreshVersion] = useState(0);
  const [pollAttempt, setPollAttempt] = useState(0);
  const [pollDelay, setPollDelay] = useState(400);
  const listScroll = useRef(0);

  useEffect(() => {
    const controller = new AbortController();
    const load = async () => {
      setLoadState("loading");
      try {
        const response = await fetch(`/api/past-attention?page=${pageNumber}`, { signal: controller.signal });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        setPage(await response.json() as PastAttentionPage);
        setLoadState("ready");
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") return;
        setLoadState("error");
      }
    };
    void load();
    return () => controller.abort();
  }, [pageNumber, refreshVersion]);

  useEffect(() => {
    if (bulkRun?.status !== "running") return;
    const timeout = window.setTimeout(() => {
      const poll = async () => {
        try {
          const response = await fetch(`/api/past-attention/result-runs/${bulkRun.run_id}`);
          if (!response.ok) throw new Error();
          const next = await response.json() as BulkResultRun;
          setBulkRun(next);
          setBulkState("idle");
          setPollDelay(400);
          if (next.status !== "running") setRefreshVersion((value) => value + 1);
        } catch {
          setBulkState("error");
          setPollDelay((value) => Math.min(value * 2, 5000));
          setPollAttempt((value) => value + 1);
        }
      };
      void poll();
    }, pollDelay);
    return () => window.clearTimeout(timeout);
  }, [bulkRun, pollAttempt, pollDelay]);

  const pageHorses = useMemo(
    () => (page?.weeks ?? []).flatMap((week) => week.horses),
    [page],
  );
  const dates = useMemo(
    () => [...new Set(pageHorses.map((horse) => horse.race_date))].sort().reverse(),
    [pageHorses],
  );
  const courses = useMemo(
    () => [...new Set(pageHorses.map((horse) => horse.racecourse))].sort(),
    [pageHorses],
  );
  const visibleWeeks = useMemo(() => (page?.weeks ?? []).map((week) => ({
    ...week, horses: week.horses.filter((horse) => (
      (selectedDate === "all" || horse.race_date === selectedDate)
      && (selectedCourse === "all" || horse.racecourse === selectedCourse)
      && (timingFilter === "all"
        || (timingFilter === "pre" && horse.pre_race_attention)
        || (timingFilter === "post" && horse.post_start_attention))
      && matchesResult(horse, resultFilter)
    )),
  })), [page, resultFilter, selectedCourse, selectedDate, timingFilter]);

  const openDetail = async (horse: PastAttentionHorse) => {
    const snapshotId = horse.pre_race_snapshot_id ?? horse.post_start_snapshot_id;
    const judgementId = horse.pre_race_judgement_id ?? horse.post_start_judgement_id;
    if (snapshotId === null || judgementId === null) return;
    listScroll.current = window.scrollY;
    setDetailState("loading");
    try {
      const query = new URLSearchParams({
        snapshot_id: String(snapshotId), judgement_id: String(judgementId),
      });
      const response = await fetch(`/api/races/${horse.race_id}/weekly-decision-view?${query}`);
      if (!response.ok) throw new Error();
      setDetail(await response.json() as DecisionView);
      setSelectedHorseNumber(horse.horse_number);
      setDetailState("idle");
      window.scrollTo({ top: 0 });
    } catch {
      setDetailState("error");
    }
  };

  const back = () => {
    setDetail(null);
    setSelectedHorseNumber(undefined);
    setDetailState("idle");
    window.setTimeout(() => window.scrollTo({ top: listScroll.current }), 0);
  };

  const startBulkResultRun = async () => {
    setBulkState("starting");
    setPollAttempt(0);
    setPollDelay(400);
    try {
      const response = await fetch(`/api/past-attention/result-runs?page=${pageNumber}`, { method: "POST" });
      if (!response.ok) throw new Error();
      const run = await response.json() as BulkResultRun;
      setBulkRun(run);
      setBulkState("idle");
      if (run.status !== "running") setRefreshVersion((value) => value + 1);
    } catch {
      setBulkState("error");
    }
  };

  if (detail) {
    return <RaceDecisionDetail detail={detail} dataState="判定済み / 発走後"
      backLabel="過去レースへ戻る" onBack={back} selectedHorseNumber={selectedHorseNumber}
      onNavigate={onNavigate} />;
  }

  return <section className="past-attention-workspace" aria-labelledby="past-attention-heading">
    <div className="panel past-attention-header">
      <div className="panel-heading"><div><span className="section-number">03</span>
        <h2 id="past-attention-heading">過去レースの注目馬</h2></div>
        <span className="local-badge">先週以前</span>
      </div>
      <p>注目馬の着順を4開催週ずつ振り返ります。事後判定はレース前の判断と区別して表示します。</p>
      <div className="past-scope-filters">
        <label>開催日
          <select value={selectedDate} onChange={(event) => setSelectedDate(event.target.value)}>
            <option value="all">すべて</option>
            {dates.map((date) => <option key={date} value={date}>{date}</option>)}
          </select>
        </label>
        <label>競馬場
          <select value={selectedCourse} onChange={(event) => setSelectedCourse(event.target.value)}>
            <option value="all">すべて</option>
            {courses.map((course) => <option key={course} value={course}>{course}</option>)}
          </select>
        </label>
      </div>
      <div className="past-timing-filters" aria-label="判定時点">
        {([[
          "all", "すべての判定",
        ], ["pre", "事前判定あり"], ["post", "事後判定あり"]] as const).map(([value, label]) => (
          <button key={value} type="button" aria-pressed={timingFilter === value}
            onClick={() => setTimingFilter(value)}>{label}</button>
        ))}
      </div>
      <div className="past-result-filters" aria-label="結果区分">
        {([
          ["all", "すべて"], ["first", "1着"], ["placed", "2〜3着"],
          ["other", "4着以下"], ["withdrawn", "取消・除外・競走中止"],
          ["missing", "結果未取得"],
        ] as const).map(([value, label]) => <button key={value} type="button"
          aria-pressed={resultFilter === value} onClick={() => setResultFilter(value)}>{label}</button>)}
      </div>
      <div className="bulk-result-actions">
        <button type="button" disabled={bulkState === "starting" || bulkRun?.status === "running"}
          onClick={() => void startBulkResultRun()}>
          {bulkRun && (bulkRun.missing_count > 0 || bulkRun.failed_count > 0)
            ? "未取得・失敗を再試行" : "表示中の未取得結果を一括取得"}
        </button>
        <span>表示中の4開催週だけを、JRAから直列・低頻度で取得します。</span>
      </div>
      {bulkState === "starting" && <p className="bulk-result-message" role="status">一括取得を開始しています…</p>}
      {bulkState === "error" && <div className="bulk-result-message error" role="alert">
        一括取得の状態を確認できませんでした。自動で再確認します。
        <button type="button" onClick={() => setPollAttempt((value) => value + 1)}>今すぐ状態を再確認</button>
      </div>}
      {bulkRun && <div className={`bulk-result-progress ${bulkRun.status}`} aria-live="polite">
        <strong>{bulkRun.processed_count} / {bulkRun.target_count} レース処理済み</strong>
        <progress max={Math.max(1, bulkRun.target_count)} value={bulkRun.processed_count} />
        <span>{bulkRun.succeeded_count}件取得 / {bulkRun.missing_count}件未取得 / {bulkRun.failed_count}件失敗</span>
        {bulkRun.status === "stopped" && <div className="bulk-result-fallback">
          <p>JRAからの取得を停止しました。失敗したレースは保存済み結果を変更していません。</p>
          {onNavigate && <button type="button" onClick={() => onNavigate("import")}>個別URL・CSV取込へ</button>}
        </div>}
        {bulkRun.targets.filter((target) => ["missing", "failed", "stopped"].includes(target.status))
          .map((target) => <small key={target.race_id}>{target.race_date} {target.racecourse} {target.race_number}R: {target.error_message ?? "取得できませんでした。"}</small>)}
      </div>}
    </div>

    {detailState === "loading" && <p className="message" role="status">レース詳細を読み込んでいます…</p>}
    {detailState === "error" && <p className="message error" role="alert">レース詳細を読み込めませんでした。</p>}
    {loadState === "loading" && <p className="message" role="status">過去レースを読み込んでいます…</p>}
    {loadState === "error" && <p className="message error" role="alert">過去レースを読み込めませんでした。</p>}
    {loadState === "ready" && visibleWeeks.map((week) => <section key={week.week_start}
      className="panel past-attention-week" aria-label={weekLabel(week.week_start, week.week_end)}>
      <h3>{weekLabel(week.week_start, week.week_end)}</h3>
      {week.horses.length === 0 ? <p className="empty-note">条件に合う注目馬はいません。</p>
        : <ul className="past-attention-list">{week.horses.map((horse) => <li
          key={`${horse.race_id}-${horse.horse_number}`}>
          <button type="button" onClick={() => void openDetail(horse)}
            aria-label={`${horse.race_date} ${horse.racecourse} ${horse.race_number}R ${horse.horse_number}番 ${horse.horse_name} ${resultLabel(horse)}`}>
            <span className="past-race"><time>{horse.race_date}</time><strong>{horse.racecourse} {horse.race_number}R</strong></span>
            <span className="past-horse"><b>{horse.horse_number}</b><strong>{horse.horse_name}</strong></span>
            <span className="past-result">{resultLabel(horse)}</span>
            <span className="past-badges">
              {horse.pre_race_attention && <small>事前判定</small>}
              {horse.post_start_attention && <small>事後判定</small>}
              {horse.has_result_correction && <small>訂正あり</small>}
            </span>
          </button>
        </li>)}</ul>}
    </section>)}
    {loadState === "ready" && page?.weeks.length === 0 && <p className="empty-note">過去の注目馬はまだありません。</p>}
    {loadState === "ready" && page && <nav className="past-pagination" aria-label="過去レースページ">
      <button type="button" disabled={!page.has_newer} onClick={() => {
        setSelectedDate("all"); setSelectedCourse("all"); setBulkRun(null); setPageNumber((value) => value - 1);
      }}>新しい4開催週</button>
      <span>{page.page}ページ目</span>
      <button type="button" disabled={!page.has_older} onClick={() => {
        setSelectedDate("all"); setSelectedCourse("all"); setBulkRun(null); setPageNumber((value) => value + 1);
      }}>古い4開催週</button>
    </nav>}
  </section>;
}
