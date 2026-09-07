import { useCallback, useMemo, useRef, useState } from "react";

import MeetingWeekAcquisitionPanel, {
  type MeetingWeekRace, type MeetingWeekSummary,
} from "./MeetingWeekAcquisitionPanel";
import RaceDecisionDetail, { type DecisionView } from "./RaceDecisionDetail";

type Filter = "all" | "attention" | "waiting";

const statusLabels: Record<MeetingWeekRace["display_state"], string> = {
  entries_waiting: "出馬表待ち", odds_waiting: "オッズ待ち", judgement_waiting: "判定待ち",
  no_attention: "注目なし", attention: "注目あり", acquisition_failed: "取得失敗",
  post_start: "発走後",
};
const acquisitionLabels: Record<MeetingWeekRace["state"], string> = {
  schedule_only: "日程のみ", entries_waiting: "出馬表待ち", odds_waiting: "オッズ待ち",
  judgement_waiting: "判定待ち", ready: "判定済み", stopped: "取得失敗",
};
const levelLabels = { none: "なし", low: "低", medium: "中", high: "高" } as const;
const waitingStates = new Set<MeetingWeekRace["display_state"]>([
  "entries_waiting", "odds_waiting", "judgement_waiting", "acquisition_failed",
]);

const dateLabel = (value: string) => {
  const [, month, day] = value.split("-").map(Number);
  const weekday = new Intl.DateTimeFormat("ja-JP", { weekday: "short", timeZone: "Asia/Tokyo" })
    .format(new Date(`${value}T12:00:00+09:00`));
  return `${month}月${day}日（${weekday}）`;
};
type Props = { onNavigate?: (area: "evaluation" | "import" | "settings") => void };

export default function WeeklyRaceWorkspace({ onNavigate }: Props) {
  const [summary, setSummary] = useState<MeetingWeekSummary | null>(null);
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const [course, setCourse] = useState("all");
  const [filter, setFilter] = useState<Filter>("all");
  const [showCompleted, setShowCompleted] = useState(false);
  const [detail, setDetail] = useState<DecisionView | null>(null);
  const [detailSource, setDetailSource] = useState<MeetingWeekRace | null>(null);
  const [detailState, setDetailState] = useState<"idle" | "loading" | "error">("idle");
  const [detailMessage, setDetailMessage] = useState("");
  const listScroll = useRef(0);

  const receiveSummary = useCallback((loaded: MeetingWeekSummary | null) => {
    setSummary(loaded);
    if (loaded?.races.length) {
      setSelectedDate((current) => current && loaded.races.some((race) => race.race_date === current)
        ? current : [...new Set(loaded.races.map((race) => race.race_date))].sort()[0]);
    }
  }, []);

  const dates = useMemo(() => [...new Set(summary?.races.map((race) => race.race_date) ?? [])].sort(), [summary]);
  const courses = useMemo(() => [...new Set((summary?.races ?? [])
    .filter((race) => race.race_date === selectedDate).map((race) => race.racecourse))].sort(), [summary, selectedDate]);
  const visible = useMemo(() => (summary?.races ?? [])
    .filter((race) => race.race_date === selectedDate)
    .filter((race) => course === "all" || race.racecourse === course)
    .filter((race) => showCompleted || !race.has_started)
    .filter((race) => filter === "all"
      || (filter === "attention" && (race.attention_horse_count ?? 0) > 0)
      || (filter === "waiting" && waitingStates.has(race.display_state)))
    .sort((left, right) => left.start_time.localeCompare(right.start_time)
      || left.racecourse.localeCompare(right.racecourse) || left.race_number - right.race_number),
  [summary, selectedDate, course, showCompleted, filter]);

  const openDetail = async (race: MeetingWeekRace) => {
    if (race.race_id === null || race.snapshot_id === null || race.judgement_id === null) return;
    listScroll.current = window.scrollY;
    setDetailState("loading");
    setDetailMessage("");
    try {
      const query = new URLSearchParams({
        snapshot_id: String(race.snapshot_id), judgement_id: String(race.judgement_id),
      });
      const response = await fetch(`/api/races/${race.race_id}/weekly-decision-view?${query}`);
      if (!response.ok) throw new Error("レース詳細を読み込めませんでした。");
      setDetail(await response.json() as DecisionView);
      setDetailSource(race);
      setDetailState("idle");
      window.scrollTo({ top: 0 });
    } catch (error) {
      setDetailMessage(error instanceof Error ? error.message : "レース詳細を読み込めませんでした。");
      setDetailState("error");
    }
  };

  const back = () => {
    setDetail(null);
    setDetailState("idle");
    window.setTimeout(() => window.scrollTo({ top: listScroll.current }), 0);
  };

  if (detail) {
    const dataState = `${detailSource ? acquisitionLabels[detailSource.state] : "判定済み"}${detailSource?.has_started ? " / 発走後" : ""}`;
    return <RaceDecisionDetail detail={detail} dataState={dataState}
      backLabel="レース一覧へ戻る" onBack={back} onNavigate={onNavigate} />;
  }

  return <div className="weekly-workspace">
    <MeetingWeekAcquisitionPanel onSummaryChange={receiveSummary}
      onNavigateToImport={() => onNavigate?.("import")} showRaceList={false} />
    {detailState === "loading" && <p role="status" className="message">レース詳細を読み込んでいます…</p>}
    {detailState === "error" && <p role="alert" className="message error">{detailMessage}</p>}
    {summary && dates.length > 0 && <section className="weekly-list-panel" aria-labelledby="weekly-list-heading">
      <div className="weekly-list-heading"><div><span>取得済みレース</span><h2 id="weekly-list-heading">レース一覧</h2></div>
        <label><input type="checkbox" checked={showCompleted} onChange={(event) => setShowCompleted(event.target.checked)} /> 発走後も表示</label></div>
      <div className="date-tabs" role="tablist" aria-label="開催日">
        {dates.map((date) => <button type="button" role="tab" aria-selected={selectedDate === date}
          key={date} onClick={() => { setSelectedDate(date); setCourse("all"); }}>{dateLabel(date)}</button>)}
      </div>
      <div className="course-tabs" role="tablist" aria-label="競馬場">
        <button type="button" role="tab" aria-selected={course === "all"} onClick={() => setCourse("all")}>全競馬場</button>
        {courses.map((item) => <button type="button" role="tab" aria-selected={course === item}
          key={item} onClick={() => setCourse(item)}>{item}</button>)}
      </div>
      <div className="race-filters" aria-label="レースの絞り込み">
        {(["all", "attention", "waiting"] as const).map((value) => <button type="button"
          aria-pressed={filter === value} key={value} onClick={() => setFilter(value)}>
          {{ all: "全レース", attention: "注目あり", waiting: "データ待ち・不足" }[value]}
        </button>)}
      </div>
      {visible.length === 0 ? <p className="empty-note weekly-empty">該当するレースはありません。</p>
        : <ul className="weekly-race-list" aria-label="レース一覧">{visible.map((race) => {
          const canOpen = race.race_id !== null && race.snapshot_id !== null && race.judgement_id !== null;
          const attention = race.attention_horse_count !== null && race.judged_runner_count !== null
            ? `注目 ${race.attention_horse_count}頭 / ${race.judged_runner_count}頭` : statusLabels[race.display_state];
          const level = race.attention_level ? levelLabels[race.attention_level] : null;
          const attentionStatus = race.attention_horse_count === null ? "未判定"
            : race.attention_horse_count > 0 ? "注目馬あり" : "注目馬なし";
          return <li key={`${race.race_date}-${race.racecourse}-${race.race_number}`}>
            <button type="button" disabled={!canOpen} onClick={() => void openDetail(race)}
              aria-label={`${race.start_time} ${race.racecourse} ${race.race_number}R ${race.race_name} ${attentionStatus} ${attention}${level ? ` 注目度 ${level}` : ""} ${race.has_started ? "発走後" : acquisitionLabels[race.state]}`}>
              <time dateTime={`${race.race_date}T${race.start_time}`}>{race.start_time}</time>
              <strong>{race.racecourse} {race.race_number}R</strong>
              <span className="race-name">{race.race_name}</span>
              <span className="race-meta desktop-only">{race.surface}{race.distance_m}m / {race.condition_text}</span>
              <span className="race-meta mobile-only">{race.distance_m}m</span>
              <span className={`attention-band level-${race.attention_level ?? "pending"}`}>{attentionStatus}</span>
              <span className="attention-count desktop-only">{race.attention_horse_count === null ? "—" : attention}</span>
              <span className="attention-count mobile-only">{race.attention_horse_count === null || race.judged_runner_count === null ? "—" : `${race.attention_horse_count}/${race.judged_runner_count}`}</span>
              <span className="acquisition-state">{race.has_started ? "発走後" : acquisitionLabels[race.state]}</span>
            </button>
          </li>;
        })}</ul>}
      <aside className="attention-disclaimer">注目度は「注目」の馬が1頭以上いるレースを、判定対象馬に占める割合で段階表示しています。期待値、回収率、購入推奨、利益優位性を示しません。</aside>
    </section>}
  </div>;
}
