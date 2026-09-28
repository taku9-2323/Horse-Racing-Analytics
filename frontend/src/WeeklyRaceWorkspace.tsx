import { useDecisionRoute } from "./useDecisionRoute";
import { useViewState } from "./ViewState";
import { useCallback, useMemo, useRef, useState } from "react";

import MeetingWeekAcquisitionPanel, {
  type MeetingWeekRace, type MeetingWeekSummary,
} from "./MeetingWeekAcquisitionPanel";
import RaceDecisionDetail from "./RaceDecisionDetail";

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
export function selectMeetingDate(races: MeetingWeekRace[], current: string | null, now = new Date()): string | null {
  const dates = [...new Set(races.map((race) => race.race_date))].sort();
  if (current && dates.includes(current)) return current;
  const today = new Intl.DateTimeFormat("sv-SE", { timeZone: "Asia/Tokyo", year: "numeric", month: "2-digit", day: "2-digit" }).format(now);
  if (dates.includes(today)) return today;
  return dates.find((date) => date > today && races.some((race) => race.race_date === date && !race.has_started))
    ?? dates.at(-1) ?? null;
}

type Props = { onNavigate?: (area: "evaluation" | "import" | "settings", raceId?: number) => void };

export default function WeeklyRaceWorkspace({ onNavigate }: Props) {
  const [summaryLoaded, setSummaryLoaded] = useState(false);
  const [summary, setSummary] = useState<MeetingWeekSummary | null>(null);
  const [selectedDate, setSelectedDate] = useViewState<string | null>("weeklyDate", null);
  const [course, setCourse] = useViewState<string>("weeklyCourse", "all");
  const [filter, setFilter] = useViewState<Filter>("weeklyFilter", "all");
  const [showCompleted, setShowCompleted] = useViewState<boolean>("weeklyCompleted", false);
  const { detail, state: detailState, setReference } = useDecisionRoute("weekly");

  const listScroll = useRef(0);

  const receiveSummary = useCallback((loaded: MeetingWeekSummary | null) => {
    setSummary(loaded);
    setSummaryLoaded(true);
    setSelectedDate((current) => current ?? selectMeetingDate(loaded?.races ?? [], null), true);
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

  const selectedRaces = (summary?.races ?? []).filter((race) => race.race_date === selectedDate);
  const allStartedHidden = !showCompleted && selectedRaces.length > 0 && selectedRaces.every((race) => race.has_started);

  const openDetail = async (race: MeetingWeekRace) => {
    if (race.race_id === null || race.snapshot_id === null || race.judgement_id === null) return;
    listScroll.current = window.scrollY;
    setReference(`${race.race_id},${race.snapshot_id},${race.judgement_id}`);
    window.scrollTo({ top: 0 });
  };
  const back = () => {
    setReference(null);
    window.setTimeout(() => window.scrollTo({ top: listScroll.current }), 0);
  };

  if (detail) {
    const detailSource = summary?.races.find((race) => race.race_id === detail.race_id && race.snapshot_id === detail.snapshot_id && race.judgement_id === detail.judgement_id);
    const dataState = `${detailSource ? acquisitionLabels[detailSource.state] : "判定済み"}${detailSource?.has_started ? " / 発走後" : ""}`;
    return <RaceDecisionDetail detail={detail} dataState={dataState}
      backLabel="レース一覧へ戻る" onBack={back} onNavigate={onNavigate} />;
  }

  return <div className="weekly-workspace">
    <MeetingWeekAcquisitionPanel onSummaryChange={receiveSummary}
      onNavigateToImport={() => onNavigate?.("import")} showRaceList={false} />
    {detailState === "loading" && <p role="status" className="message">レース詳細を読み込んでいます…</p>}
    {detailState === "error" && <div role="alert" className="message error">指定されたレース・固定時点が見つからないか、読み込めませんでした。<button type="button" onClick={back}>一覧へ戻る</button></div>}
    {summaryLoaded && dates.length === 0 && <p className="empty-note">開催週のレースは未取得です。上の取得ボタンから取得してください。</p>}
    {summaryLoaded && selectedDate !== null && dates.length > 0 && !dates.includes(selectedDate) && <p role="alert">指定された開催日は取得済み一覧にありません。<button type="button" onClick={() => setSelectedDate(selectMeetingDate(summary?.races ?? [], null))}>開催日の選択を戻す</button></p>}
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
      {visible.length === 0 ? <div className="empty-note weekly-empty">
        <p>{allStartedHidden ? "選択日のレースはすべて発走済みのため非表示です。" : "絞り込み条件に一致するレースはありません。"}</p>
        {allStartedHidden
          ? <button type="button" onClick={() => setShowCompleted(true)}>発走済みレースを表示</button>
          : <button type="button" onClick={() => { setCourse("all"); setFilter("all"); setShowCompleted(true); }}>絞り込みを解除して全レースを表示</button>}
      </div>
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
              <strong title={`${race.racecourse} ${race.race_number}R`}>{race.racecourse} {race.race_number}R</strong>
              <span className="race-name">{race.race_name}</span>
              <span className="race-meta desktop-only">{race.surface}{race.distance_m}m / {race.condition_text}</span>
              <span className={`attention-band level-${race.attention_level ?? "pending"}`} title={attentionStatus}><span className="desktop-only">{attentionStatus}</span><span className="mobile-only">{race.attention_horse_count === null ? "未判定" : race.attention_horse_count > 0 ? "注目あり" : "注目なし"}</span></span>
              <span className="attention-count desktop-only">{race.attention_horse_count === null ? "—" : attention}</span>
              <span className="attention-count mobile-only">{race.attention_horse_count === null || race.judged_runner_count === null ? "—" : `${race.attention_horse_count}/${race.judged_runner_count}`}</span>
              <span className="acquisition-state" title={race.has_started ? "発走後" : acquisitionLabels[race.state]}>{race.has_started ? "発走後" : race.state === "entries_waiting" ? "出馬待ち" : acquisitionLabels[race.state]}</span>
            </button>
          </li>;
        })}</ul>}
      <aside className="attention-disclaimer">注目度は「注目」の馬が1頭以上いるレースを、判定対象馬に占める割合で段階表示しています。期待値、回収率、購入推奨、利益優位性を示しません。</aside>
    </section>}
  </div>;
}
