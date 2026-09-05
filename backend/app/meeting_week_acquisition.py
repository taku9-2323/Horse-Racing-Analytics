from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from html.parser import HTMLParser
from pathlib import Path
import os
import re
from typing import Any, Literal, cast
from urllib.parse import parse_qs, unquote, urljoin, urlparse

from pydantic import BaseModel

from app.database import RaceImportConflictError, SqliteDatabase
from app.jra_acquisition import (
    ALLOWED_HOST, MAX_RESPONSE_BYTES, ROBOTS_URL, AcquisitionError, FetchResponse,
    JraOddsAcquirer, JraRaceCardAcquirer, SOURCE_RACE_ID_PATTERN,
    audited_error, decode_html, parse_odds_source_identity, parse_source_race_identity,
    robots_allows,
)


PARSER_VERSION = "jra-meeting-week/1"
SELECTION_URL = f"https://{ALLOWED_HOST}/JRADB/accessD.html?CNAME=pw01dli00/F3"
SELECTION_FINAL_URL = f"https://{ALLOWED_HOST}/JRADB/accessD.html"
CALENDAR_INDEX_PATTERN = re.compile(r"/keiba/calendar(?P<year>\d{4})/index\.html")
PROGRAM_PATTERN = re.compile(
    r"/keiba/calendar(?P<year>\d{4})/(?P=year)/(?P<month>\d{1,2})/(?P<monthday>\d{4})\.html"
)
MEETING_PATTERN = re.compile(r"(?P<meeting>\d+)回(?P<racecourse>札幌|函館|福島|新潟|東京|中山|中京|京都|阪神|小倉)(?P<day>\d+)日")
COURSE_PATTERN = re.compile(r"(?P<distance>[\d,]+)（(?P<surface>芝|芝・外|ダ|ダート|障害?)[^）]*）")
RACE_NUMBER_PATTERN = re.compile(r"(?P<number>\d+)レース")
TIME_PATTERN = re.compile(r"(?P<hour>\d{1,2})時(?P<minute>\d{2})分")
DATE_PATTERN = re.compile(r"(?P<year>\d{4})年(?P<month>\d{1,2})月(?P<day>\d{1,2})日")


class ResourceNotPublished(Exception):
    pass


class MeetingWeekRace(BaseModel):
    race_date: str
    racecourse: str
    meeting_number: int
    meeting_day: int
    race_number: int
    race_name: str
    start_time: str
    surface: str
    distance_m: int
    condition_text: str
    state: Literal[
        "schedule_only", "entries_waiting", "odds_waiting", "judgement_waiting", "ready", "stopped"
    ]
    race_id: int | None
    card_id: int | None
    snapshot_id: int | None
    judgement_id: int | None
    error_code: str | None
    updated_at: str


class MeetingWeekSummary(BaseModel):
    run_id: int
    week_start: str
    week_end: str
    started_at: str
    completed_at: str | None
    status: Literal["running", "completed", "stopped"]
    target_count: int
    processed_count: int
    ready_count: int
    waiting_count: int
    failed_count: int
    stop_reason: str | None
    last_target: str | None
    last_updated_at: str
    races: list[MeetingWeekRace]


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): value or "" for key, value in attrs}
        if tag.lower() == "a" and values.get("href"):
            self.links.append(values["href"])


class _ProgramParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.heading_tag: str | None = None
        self.heading_parts: list[str] = []
        self.current_meeting: tuple[int, str, int] | None = None
        self.cell_tag: str | None = None
        self.cell_parts: list[str] = []
        self.row_cells: list[str] | None = None
        self.rows: list[tuple[tuple[int, str, int], list[str]]] = []
        self.all_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        lowered = tag.lower()
        if lowered in {"h1", "h2", "h3", "h4"}:
            self.heading_tag = lowered
            self.heading_parts = []
        elif lowered == "tr":
            self.row_cells = []
        elif lowered in {"th", "td"} and self.row_cells is not None:
            self.cell_tag = lowered
            self.cell_parts = []

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.all_text.append(data)
            if self.heading_tag is not None:
                self.heading_parts.append(data)
            if self.cell_tag is not None:
                self.cell_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if self.cell_tag == lowered and self.row_cells is not None:
            self.row_cells.append(" ".join("".join(self.cell_parts).split()))
            self.cell_tag = None
            self.cell_parts = []
        if self.heading_tag == lowered:
            heading = " ".join("".join(self.heading_parts).split())
            match = MEETING_PATTERN.search(heading)
            if match is not None:
                self.current_meeting = (
                    int(match.group("meeting")), match.group("racecourse"), int(match.group("day")),
                )
            self.heading_tag = None
            self.heading_parts = []
        if lowered == "tr" and self.row_cells is not None:
            if self.current_meeting is not None and len(self.row_cells) >= 3:
                self.rows.append((self.current_meeting, self.row_cells))
            self.row_cells = None


def utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def current_meeting_week(now: datetime) -> tuple[date, date]:
    local_date = now.astimezone(timezone(timedelta(hours=9))).date()
    start = local_date - timedelta(days=local_date.weekday())
    return start, start + timedelta(days=6)


def calendar_index_url(year: int) -> str:
    return f"https://{ALLOWED_HOST}/keiba/calendar{year}/index.html"


def validate_calendar_url(url: str) -> None:
    parsed = urlparse(url)
    if (parsed.scheme != "https" or parsed.hostname != ALLOWED_HOST or parsed.port not in {None, 443}
            or parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment
            or not (CALENDAR_INDEX_PATTERN.fullmatch(parsed.path) or PROGRAM_PATTERN.fullmatch(parsed.path))):
        raise AcquisitionError("url_not_allowed", "許可されたJRA開催日程URLではありません。", 422)


def discover_program_urls(body: bytes, content_type: str, index_url: str,
                          week_start: date, week_end: date) -> list[str]:
    validate_calendar_url(index_url)
    parser = _LinkParser()
    parser.feed(decode_html(body, content_type))
    urls: set[str] = set()
    recognized_program_link = False
    for link in parser.links:
        candidate = urljoin(index_url, link)
        parsed = urlparse(candidate)
        match = PROGRAM_PATTERN.fullmatch(parsed.path)
        if (parsed.scheme != "https" or parsed.hostname != ALLOWED_HOST or parsed.query or parsed.fragment
                or match is None):
            continue
        recognized_program_link = True
        monthday = match.group("monthday")
        try:
            candidate_date = date(int(match.group("year")), int(monthday[:2]), int(monthday[2:]))
        except ValueError:
            continue
        if week_start <= candidate_date <= week_end:
            urls.add(candidate)
    if not recognized_program_link:
        raise AcquisitionError(
            "calendar_validation_failed", "JRA開催日程を検証できませんでした。CSV取込を使用してください。", 503,
        )
    return sorted(urls)


def parse_program(body: bytes, content_type: str, source_url: str) -> list[dict[str, Any]]:
    validate_calendar_url(source_url)
    parser = _ProgramParser()
    parser.feed(decode_html(body, content_type))
    date_match = DATE_PATTERN.search(" ".join(parser.all_text))
    if date_match is None:
        raise AcquisitionError("program_validation_failed", "JRA日別番組表を検証できませんでした。", 503)
    race_date = date(
        int(date_match.group("year")), int(date_match.group("month")), int(date_match.group("day")),
    ).isoformat()
    rows: list[dict[str, Any]] = []
    for (meeting_number, racecourse, meeting_day), cells in parser.rows:
        number_match = RACE_NUMBER_PATTERN.fullmatch(cells[0].replace(" ", ""))
        course_match = COURSE_PATTERN.search(cells[1])
        time_match = TIME_PATTERN.fullmatch(cells[2].replace(" ", ""))
        if number_match is None or course_match is None or time_match is None:
            continue
        raw_surface = course_match.group("surface")
        surface = "芝" if raw_surface.startswith("芝") else "障害" if raw_surface.startswith("障") else "ダート"
        race_name = cells[1][:course_match.start()].strip()
        if not race_name:
            raise AcquisitionError("program_validation_failed", "JRA日別番組表を検証できませんでした。", 503)
        rows.append({
            "race_date": race_date, "racecourse": racecourse,
            "meeting_number": meeting_number, "meeting_day": meeting_day,
            "race_number": int(number_match.group("number")), "race_name": race_name,
            "start_time": f"{int(time_match.group('hour')):02d}:{int(time_match.group('minute')):02d}",
            "surface": surface, "distance_m": int(course_match.group("distance").replace(",", "")),
            "condition_text": cells[1], "source_url": source_url,
        })
    if not rows:
        raise AcquisitionError("program_validation_failed", "JRA日別番組表を検証できませんでした。", 503)
    identities = {(row["racecourse"], row["race_number"]) for row in rows}
    if len(identities) != len(rows):
        raise AcquisitionError("program_validation_failed", "JRA日別番組表を検証できませんでした。", 503)
    return rows


def extract_race_card_urls(body: bytes, content_type: str, base_url: str,
                           week_start: date, week_end: date) -> list[str]:
    del content_type
    parser = _LinkParser()
    parser.feed(body.decode("ascii", errors="ignore"))
    urls: set[str] = set()
    for link in parser.links:
        candidate = unquote(urljoin(base_url, link))
        parsed = urlparse(candidate)
        cname = parse_qs(parsed.query).get("CNAME", [])
        match = SOURCE_RACE_ID_PATTERN.fullmatch(cname[0]) if len(cname) == 1 else None
        if (parsed.scheme != "https" or parsed.hostname != ALLOWED_HOST or parsed.path != "/JRADB/accessD.html"
                or match is None or not match.group("format").startswith("dde")):
            continue
        identity = parse_source_race_identity(candidate)
        race_date = date.fromisoformat(str(identity["race_date"]))
        if week_start <= race_date <= week_end:
            urls.add(candidate)
    return sorted(urls)


def extract_odds_url(body: bytes, content_type: str, card_url: str) -> str | None:
    del content_type
    text = body.decode("ascii", errors="ignore")
    match = re.search(r"(pw151ouS3\d{20}Z/[A-F0-9]{2})", text)
    if match is None:
        return None
    odds_url = f"https://{ALLOWED_HOST}/JRADB/accessO.html?CNAME={match.group(1)}"
    if parse_odds_source_identity(odds_url)["source_race_id"] != parse_source_race_identity(card_url)["source_race_id"]:
        raise AcquisitionError("odds_race_mismatch", "JRAオッズリンクが出馬表と一致しません。", 503)
    return odds_url


class JraMeetingWeekAcquirer:
    def __init__(self, fetcher: Callable[[str], FetchResponse], cache_directory: Path) -> None:
        self._fetcher = fetcher
        self._cache_directory = cache_directory
        self._memory_cache: dict[str, tuple[datetime, bytes, str]] = {}

    def acquire(self, url: str, received_at: datetime, *, optional: bool = False) -> tuple[bytes, str, dict[str, Any]]:
        if url != SELECTION_URL:
            validate_calendar_url(url)
        cached = self._load_cache(url, received_at)
        if cached is not None:
            body, content_type, cached_at = cached
            return body, content_type, self._observation(url, body, cached_at)
        robots = self._fetcher(ROBOTS_URL)
        if robots.status != 200 or robots.final_url != ROBOTS_URL or not robots_allows(robots.body, url):
            raise audited_error(
                "acquisition_stopped", "JRAからの取得を停止しました。CSV取込を使用してください。",
                503, url, robots.body, received_at, PARSER_VERSION,
            )
        response = self._fetcher(url)
        allowed_final = {url} if url != SELECTION_URL else {url, SELECTION_FINAL_URL}
        if optional and response.status in {404, 410}:
            raise ResourceNotPublished
        if response.status != 200 or unquote(response.final_url) not in {unquote(value) for value in allowed_final}:
            raise audited_error(
                "acquisition_stopped", "JRAからの取得を停止しました。CSV取込を使用してください。",
                503, url, response.body, received_at, PARSER_VERSION,
            )
        content_type = response.headers.get("content-type", "").lower()
        if not content_type.startswith("text/html") or len(response.body) > MAX_RESPONSE_BYTES:
            raise audited_error(
                "unexpected_response", "想定外の応答です。CSV取込を使用してください。",
                503, url, response.body, received_at, PARSER_VERSION,
            )
        self._cache(url, response.body, content_type, received_at)
        return response.body, content_type, self._observation(url, response.body, received_at)

    def _observation(self, url: str, body: bytes, received_at: datetime) -> dict[str, Any]:
        return {
            "url": url, "received_at": utc_iso(received_at), "parser_version": PARSER_VERSION,
            "response_sha256": sha256(body).hexdigest(), "validation_status": "valid",
        }

    def _cache(self, url: str, body: bytes, content_type: str, now: datetime) -> None:
        self._memory_cache[url] = (now, body, content_type)
        self._cache_directory.mkdir(parents=True, exist_ok=True)
        url_digest = sha256(url.encode()).hexdigest()
        body_digest = sha256(body).hexdigest()
        path = self._cache_directory / f"week-{url_digest}-{body_digest}.html"
        if not path.exists():
            path.write_bytes(body)
            os.utime(path, (now.timestamp(), now.timestamp()))

    def _load_cache(self, url: str, now: datetime) -> tuple[bytes, str, datetime] | None:
        memory = self._memory_cache.get(url)
        if memory is not None and now - memory[0] <= timedelta(minutes=15):
            return memory[1], memory[2], memory[0]
        cutoff = now.timestamp() - timedelta(days=7).total_seconds()
        if not self._cache_directory.is_dir():
            return None
        for path in self._cache_directory.glob("week-*.html"):
            if path.stat().st_mtime < cutoff:
                path.unlink()
        url_digest = sha256(url.encode()).hexdigest()
        matches = sorted(self._cache_directory.glob(f"week-{url_digest}-*.html"), key=lambda item: item.stat().st_mtime, reverse=True)
        if not matches:
            return None
        path = matches[0]
        cached_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        if now - cached_at > timedelta(minutes=15):
            return None
        return path.read_bytes(), "text/html", cached_at


def meeting_week_response(database: SqliteDatabase, week_start: str) -> MeetingWeekSummary | None:
    stored = database.get_meeting_week(week_start)
    if stored is None:
        return None
    run, races = stored
    last_updated = max([
        str(run["started_at"]),
        *(str(row["updated_at"]) for row in races),
        *([] if run["completed_at"] is None else [str(run["completed_at"])]),
    ])
    return MeetingWeekSummary(
        run_id=int(run["id"]), week_start=str(run["week_start"]), week_end=str(run["week_end"]),
        started_at=str(run["started_at"]), completed_at=run["completed_at"],
        status=cast(Literal["running", "completed", "stopped"], str(run["status"])),
        target_count=int(run["target_count"]), processed_count=int(run["processed_count"]),
        ready_count=int(run["ready_count"]), waiting_count=int(run["waiting_count"]),
        failed_count=int(run["failed_count"]), stop_reason=run["stop_reason"], last_target=run["last_target"],
        last_updated_at=last_updated,
        races=[MeetingWeekRace(**dict(row)) for row in races],
    )


class MeetingWeekAcquisitionService:
    def __init__(
        self, database: SqliteDatabase, discovery: JraMeetingWeekAcquirer,
        race_cards: JraRaceCardAcquirer, odds: JraOddsAcquirer,
        now_provider: Callable[[], datetime],
    ) -> None:
        self._database = database
        self._discovery = discovery
        self._race_cards = race_cards
        self._odds = odds
        self._now = now_provider

    def run(self, run_id: int, week_start: date, week_end: date) -> None:
        current_target: str | None = None
        current_key: tuple[str, str, int] | None = None
        processed_count = 0
        try:
            program_urls: set[str] = set()
            for year in range(week_start.year, week_end.year + 1):
                index_url = calendar_index_url(year)
                current_target = index_url
                body, content_type, observation = self._discovery.acquire(index_url, self._now())
                self._database.save_meeting_week_observation(run_id, observation)
                program_urls.update(
                    discover_program_urls(body, content_type, index_url, week_start, week_end),
                )
            for program_url in sorted(program_urls):
                current_target = program_url
                program_body, program_content_type, program_observation = self._discovery.acquire(program_url, self._now())
                rows = parse_program(program_body, program_content_type, program_url)
                self._database.save_meeting_week_observation(run_id, program_observation)
                for row in rows:
                    self._database.upsert_meeting_week_race(
                        run_id, week_start.isoformat(), row, utc_iso(self._now()),
                    )
                processed_count += len(rows)
                self._database.refresh_meeting_week_run_progress(
                    run_id, processed_count, current_target,
                )

            card_urls: dict[tuple[str, str, int], str] = {}
            try:
                current_target = SELECTION_URL
                selection_body, selection_type, selection_observation = self._discovery.acquire(
                    SELECTION_URL, self._now(), optional=True,
                )
                self._database.save_meeting_week_observation(run_id, selection_observation)
                seeds = extract_race_card_urls(selection_body, selection_type, SELECTION_URL, week_start, week_end)
            except ResourceNotPublished:
                seeds = []
            for seed in seeds:
                current_target = seed
                _, _, _, seed_body = self._race_cards.acquire(seed, self._now())
                for discovered_card_url in extract_race_card_urls(
                    seed_body, "text/html", seed, week_start, week_end,
                ):
                    identity = parse_source_race_identity(discovered_card_url)
                    card_urls[(
                        str(identity["race_date"]), str(identity["racecourse"]),
                        int(identity["race_number"]),
                    )] = discovered_card_url

            for stored in self._database.list_meeting_week_races(run_id):
                key = (str(stored["race_date"]), str(stored["racecourse"]), int(stored["race_number"]))
                current_key = key
                selected_card_url = card_urls.get(key)
                if selected_card_url is None:
                    self._database.refresh_meeting_week_run_progress(
                        run_id, processed_count, current_target,
                    )
                    current_key = None
                    continue
                current_target = selected_card_url
                race, runners, card_observation, card_body = self._race_cards.acquire(
                    selected_card_url, self._now(),
                )
                card_id = self._database.save_acquired_race_card(race, runners, card_observation)
                self._database.update_meeting_week_race(
                    run_id, *key, state="odds_waiting", updated_at=utc_iso(self._now()), card_id=card_id,
                )
                odds_url = extract_odds_url(card_body, "text/html", selected_card_url)
                if odds_url is None:
                    self._database.refresh_meeting_week_run_progress(
                        run_id, processed_count, current_target,
                    )
                    current_key = None
                    continue
                current_target = odds_url
                odds, odds_observation = self._odds.acquire(odds_url, self._now())
                existing_snapshot = self._database.find_jra_odds_snapshot(
                    card_id, str(odds_observation["url"]), str(odds_observation["response_sha256"]),
                )
                if existing_snapshot is None:
                    race_id, snapshot_id = self._database.register_jra_race_with_odds(
                        card_id, odds, odds_observation,
                    )
                else:
                    race_id, snapshot_id = existing_snapshot
                state = "judgement_waiting"
                judgement_id: int | None = None
                observed_at = odds_observation["source_updated_at"]
                if observed_at is not None:
                    rule_rows = self._database.list_rule_versions()
                    rule_id = int(rule_rows[-1]["id"])
                    existing_judgement = self._database.find_active_rule_judgement(
                        race_id, snapshot_id, rule_id,
                    )
                    if existing_judgement is not None:
                        judgement_id = existing_judgement
                        state = "ready"
                    else:
                        try:
                            judgement_id = self._database.create_rule_judgement(
                                race_id, snapshot_id, rule_id, str(observed_at), utc_iso(self._now()),
                            )
                            state = "ready"
                        except ValueError:
                            state = "judgement_waiting"
                self._database.update_meeting_week_race(
                    run_id, *key, state=state, updated_at=utc_iso(self._now()),
                    race_id=race_id, snapshot_id=snapshot_id, judgement_id=judgement_id,
                )
                self._database.refresh_meeting_week_run_progress(
                    run_id, processed_count, current_target,
                )
                current_key = None
            self._database.finish_meeting_week_run(run_id, "completed", utc_iso(self._now()))
        except (AcquisitionError, RaceImportConflictError, LookupError, ValueError) as error:
            if isinstance(error, AcquisitionError) and error.observation is not None:
                self._database.save_acquisition_failure(error.observation)
            if current_key is not None:
                self._database.update_meeting_week_race(
                    run_id, *current_key, state="stopped", updated_at=utc_iso(self._now()),
                    error_code=error.code if isinstance(error, AcquisitionError) else type(error).__name__,
                )
            message = error.message if isinstance(error, AcquisitionError) else "開催週の取得を停止しました。CSV取込を使用してください。"
            self._database.finish_meeting_week_run(
                run_id, "stopped", utc_iso(self._now()), message, current_target,
            )
        except Exception:
            if current_key is not None:
                self._database.update_meeting_week_race(
                    run_id, *current_key, state="stopped", updated_at=utc_iso(self._now()),
                    error_code="unexpected_error",
                )
            self._database.finish_meeting_week_run(
                run_id, "stopped", utc_iso(self._now()),
                "開催週の取得を停止しました。CSV取込を使用してください。", current_target,
            )
