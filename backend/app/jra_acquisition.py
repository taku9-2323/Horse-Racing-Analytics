from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from html.parser import HTMLParser
from pathlib import Path
import os
import re
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.robotparser import RobotFileParser

from pydantic import BaseModel


ALLOWED_HOST = "www.jra.go.jp"
ROBOTS_URL = f"https://{ALLOWED_HOST}/robots.txt"
PARSER_VERSION = "jra-race-entry/2"
ODDS_PARSER_VERSION = "jra-odds/2"
RESULT_PARSER_VERSION = "jra-result/1"
USER_AGENT = "HorseRacingAnalyticsLocalPrototype/0.1"
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
SOURCE_RACE_ID_PATTERN = re.compile(
    r"pw01(?P<format>dde01|dde10|sde01|sde10)(?P<course_code>\d{2})(?P<year>\d{4})(?P<meeting>\d{2})"
    r"(?P<day>\d{2})(?P<race>\d{2})(?P<date>\d{8})/[A-Za-z0-9]{2}",
)
ODDS_CNAME_PATTERN = re.compile(
    r"pw151ouS3(?P<course_code>\d{2})(?P<year>\d{4})(?P<meeting>\d{2})"
    r"(?P<day>\d{2})(?P<race>\d{2})(?P<date>\d{8})Z/[A-F0-9]{2}",
)
RACECOURSES_BY_CODE = {
    "01": "札幌", "02": "函館", "03": "福島", "04": "新潟", "05": "東京",
    "06": "中山", "07": "中京", "08": "京都", "09": "阪神", "10": "小倉",
    "99": "架空",
}


@dataclass(frozen=True)
class FetchResponse:
    status: int
    final_url: str
    headers: dict[str, str]
    body: bytes


class AcquisitionError(Exception):
    def __init__(self, code: str, message: str, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.observation: dict[str, Any] | None = None


class AcquiredRaceSummary(BaseModel):
    organizer: str
    country: str
    racecourse: str
    race_date: str
    race_number: int
    start_time: str
    timezone: str
    start_utc: str
    surface: str
    distance_m: int
    going: str
    field_size: int


class AcquiredRunner(BaseModel):
    gate: int
    horse_number: int
    horse_name: str
    age: int
    sex: str
    assigned_weight: float
    status: str


class SourceObservation(BaseModel):
    url: str
    source_race_id: str
    received_at: str
    source_updated_at: str | None
    parser_version: str
    response_sha256: str
    validation_status: str


class AcquiredRaceCard(BaseModel):
    card_id: int
    version: int
    status: str
    supersedes_card_id: int | None
    race: AcquiredRaceSummary
    runners: list[AcquiredRunner]
    source: SourceObservation


class RaceCardRequest(BaseModel):
    url: str


class OddsPageRequest(BaseModel):
    url: str


class ResultPageRequest(BaseModel):
    url: str


class AcquiredOddsRunner(BaseModel):
    horse_number: int
    win_odds: float
    place_odds_min: float
    place_odds_max: float


class AcquiredOddsSnapshot(BaseModel):
    race_id: int
    snapshot_id: int
    observed_at: str | None
    received_at: str
    runners: list[AcquiredOddsRunner]
    source: SourceObservation


class AcquisitionObservation(BaseModel):
    url: str
    received_at: str
    parser_version: str
    response_sha256: str
    validation_status: str
    error_code: str | None


@dataclass
class Element:
    tag: str
    attrs: dict[str, str]
    children: list["Element"] = field(default_factory=list)
    content: list["Element | str"] = field(default_factory=list)
    closed: bool = False

    def text(self) -> str:
        parts = [item.text() if isinstance(item, Element) else item for item in self.content]
        return " ".join(" ".join(parts).split())

    def find(self, *, tag: str | None = None, class_name: str | None = None, element_id: str | None = None) -> "Element | None":
        if self._matches(tag, class_name, element_id):
            return self
        for child in self.children:
            found = child.find(tag=tag, class_name=class_name, element_id=element_id)
            if found is not None:
                return found
        return None

    def find_all(self, *, tag: str | None = None, class_name: str | None = None) -> list["Element"]:
        found = [self] if self._matches(tag, class_name, None) else []
        for child in self.children:
            found.extend(child.find_all(tag=tag, class_name=class_name))
        return found

    def _matches(self, tag: str | None, class_name: str | None, element_id: str | None) -> bool:
        classes = self.attrs.get("class", "").split()
        return ((tag is None or self.tag == tag) and
                (class_name is None or class_name in classes) and
                (element_id is None or self.attrs.get("id") == element_id))


class _TreeParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Element("document", {})
        self._stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        element = Element(tag, {key: value or "" for key, value in attrs})
        self._stack[-1].children.append(element)
        self._stack[-1].content.append(element)
        if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}:
            self._stack.append(element)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if self._stack[-1].tag == tag:
            self._stack.pop()

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self._stack) - 1, 0, -1):
            if self._stack[index].tag == tag:
                self._stack[index].closed = True
                del self._stack[index:]
                return

    def handle_data(self, data: str) -> None:
        if data.strip():
            self._stack[-1].content.append(data)


def elements_are_adjacent_siblings(root: Element, earlier: Element, later: Element) -> bool:
    for index, child in enumerate(root.children[:-1]):
        if child is earlier and root.children[index + 1] is later:
            return True
    for child in root.children:
        if elements_are_adjacent_siblings(child, earlier, later):
            return True
    return False


def default_fetcher(url: str) -> FetchResponse:
    parsed = urlparse(url)
    post_data = None
    request_url = url
    if parsed.path == "/JRADB/accessO.html" and parsed.query:
        cname = parse_qs(parsed.query).get("CNAME", [""])[0]
        post_data = urlencode({"cname": cname}).encode("ascii")
        request_url = f"https://{ALLOWED_HOST}/JRADB/accessO.html"
    elif parsed.path == "/JRADB/accessD.html" and parse_qs(parsed.query).get("CNAME") == ["pw01dli00/F3"]:
        post_data = urlencode({"cname": "pw01dli00/F3"}).encode("ascii")
        request_url = f"https://{ALLOWED_HOST}/JRADB/accessD.html"
    request = Request(request_url, data=post_data, headers={"User-Agent": USER_AGENT, "Accept": "text/html,text/plain;q=0.9"})
    try:
        with urlopen(request, timeout=10) as response:  # noqa: S310 - caller only supplies validated fixed JRA URLs
            body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                raise AcquisitionError("response_too_large", "応答サイズが上限を超えました。", 503)
            return FetchResponse(
                status=response.status, final_url=response.url,
                headers={key.lower(): value for key, value in response.headers.items()}, body=body,
            )
    except HTTPError as error:
        return FetchResponse(
            status=error.code, final_url=error.url,
            headers={key.lower(): value for key, value in error.headers.items()}, body=b"",
        )
    except (OSError, URLError) as error:
        raise AcquisitionError(
            "acquisition_stopped", "JRAからの取得を停止しました。CSV取込を使用してください。", 503,
        ) from error


class JraRaceCardAcquirer:
    def __init__(self, fetcher: Callable[[str], FetchResponse], cache_directory: Path) -> None:
        self._fetcher = fetcher
        self._cache_directory = cache_directory
        self._memory_cache: dict[str, tuple[datetime, bytes]] = {}

    def acquire(self, url: str, received_at: datetime) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any], bytes]:
        validate_race_card_url(url)
        resource = str(parse_source_race_identity(url)["resource"])
        self._purge_expired_cache(received_at)
        cached = self._load_recent_cache(url, resource, received_at)
        if cached is not None:
            return self._normalize(url, resource, cached, "text/html", received_at, cache_response=False)
        robots = self._fetcher(ROBOTS_URL)
        if robots.status != 200 or robots.final_url != ROBOTS_URL or not robots_allows(robots.body, url):
            raise audited_error(
                "acquisition_stopped", "JRAからの取得を停止しました。CSV取込を使用してください。",
                503, url, robots.body, received_at, PARSER_VERSION,
            )
        response = self._fetcher(url)
        if response.status != 200 or not same_allowed_race_card_url(response.final_url, url):
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
        return self._normalize(url, resource, response.body, content_type, received_at)

    def _normalize(
        self, url: str, resource: str, body: bytes, content_type: str, received_at: datetime,
        *, cache_response: bool = True,
    ) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any], bytes]:
        digest = sha256(body).hexdigest()
        received_utc = received_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        try:
            race, runners, source_updated_at = parse_race_card(body, content_type, url)
            source_identity = parse_source_race_identity(url)
            if (resource == "race_card"
                    and str(source_identity["race_date"])
                    < received_at.astimezone(timezone(timedelta(hours=9))).date().isoformat()):
                raise AcquisitionError(
                    "past_race_requires_result",
                    "過去レースはJRAのレース結果ページ（accessS.html）から取得してください。",
                    422,
                )
        except AcquisitionError as error:
            error.observation = {
                "url": url, "received_at": received_utc, "parser_version": PARSER_VERSION,
                "response_sha256": digest, "validation_status": "invalid", "error_code": error.code,
            }
            raise
        observation = {
            "url": url, "source_race_id": str(parse_source_race_identity(url)["source_race_id"]),
            "received_at": received_utc, "source_updated_at": source_updated_at,
            "parser_version": PARSER_VERSION, "response_sha256": digest,
            "validation_status": "valid",
        }
        if cache_response:
            self._cache(url, resource, body, digest, received_at)
        return race, runners, observation, body

    def _cache(self, url: str, resource: str, body: bytes, digest: str, now: datetime) -> None:
        if resource == "result":
            self._memory_cache[url] = (now, body)
            return
        self._cache_directory.mkdir(parents=True, exist_ok=True)
        url_digest = sha256(url.encode("utf-8")).hexdigest()
        path = self._cache_directory / f"{url_digest}-{digest}.html"
        if not path.exists():
            path.write_bytes(body)
            os.utime(path, (now.timestamp(), now.timestamp()))

    def _purge_expired_cache(self, now: datetime) -> None:
        memory_cutoff = now - timedelta(minutes=15)
        self._memory_cache = {
            url: cached for url, cached in self._memory_cache.items() if cached[0] >= memory_cutoff
        }
        if not self._cache_directory.is_dir():
            return
        cutoff = now.timestamp() - timedelta(days=7).total_seconds()
        for path in self._cache_directory.glob("*.html"):
            if path.stat().st_mtime < cutoff:
                path.unlink()

    def _load_recent_cache(self, url: str, resource: str, now: datetime) -> bytes | None:
        if resource == "result":
            cached = self._memory_cache.get(url)
            if cached is None or now - cached[0] > timedelta(minutes=15):
                return None
            return cached[1]
        if not self._cache_directory.is_dir():
            return None
        url_digest = sha256(url.encode("utf-8")).hexdigest()
        minimum_mtime = now.timestamp() - timedelta(minutes=15).total_seconds()
        candidates = sorted(
            self._cache_directory.glob(f"{url_digest}-*.html"),
            key=lambda path: path.stat().st_mtime, reverse=True,
        )
        if not candidates or candidates[0].stat().st_mtime < minimum_mtime:
            return None
        return candidates[0].read_bytes()


class JraOddsAcquirer:
    def __init__(self, fetcher: Callable[[str], FetchResponse], cache_directory: Path | None = None) -> None:
        self._fetcher = fetcher
        self._cache_directory = cache_directory
        self._memory_cache: dict[str, tuple[datetime, bytes, str]] = {}

    def acquire(self, url: str, received_at: datetime) -> tuple[list[dict[str, float | int]], dict[str, Any]]:
        validate_odds_url(url)
        if self._cache_directory is not None:
            self._purge_expired_cache(received_at)
        cached = self._memory_cache.get(url)
        if cached is not None and received_at - cached[0] > timedelta(minutes=15):
            del self._memory_cache[url]
            cached = None
        if cached is None and self._cache_directory is not None:
            cached_body = self._load_recent_cache(url, received_at)
            if cached_body is not None:
                cached = (received_at, cached_body, "text/html")
        if cached is not None:
            body = cached[1]
            content_type = cached[2]
            response_received_at = cached[0]
        else:
            robots = self._fetcher(ROBOTS_URL)
            if robots.status != 200 or robots.final_url != ROBOTS_URL or not robots_allows(robots.body, url):
                raise audited_error("acquisition_stopped", "JRAからの取得を停止しました。CSV取込を使用してください。", 503, url, robots.body, received_at, ODDS_PARSER_VERSION)
            response = self._fetcher(url)
            allowed_final_urls = {url, f"https://{ALLOWED_HOST}/JRADB/accessO.html"}
            if response.status != 200 or response.final_url not in allowed_final_urls or not response.headers.get("content-type", "").lower().startswith("text/html"):
                raise audited_error("acquisition_stopped", "JRAからの取得を停止しました。CSV取込を使用してください。", 503, url, response.body, received_at, ODDS_PARSER_VERSION)
            body = response.body
            content_type = response.headers.get("content-type", "")
            self._memory_cache[url] = (received_at, body, content_type)
            if self._cache_directory is not None:
                self._cache_directory.mkdir(parents=True, exist_ok=True)
                url_digest = sha256(url.encode("utf-8")).hexdigest()
                body_digest = sha256(body).hexdigest()
                path = self._cache_directory / f"odds-{url_digest}-{body_digest}.html"
                if not path.exists():
                    path.write_bytes(body)
                    os.utime(path, (received_at.timestamp(), received_at.timestamp()))
            response_received_at = received_at
        digest = sha256(body).hexdigest()
        try:
            odds = parse_jra_odds_page(body, content_type)
            validate_odds_page_identity(body, content_type, url)
            source_updated_at = parse_jra_odds_update_time(body, content_type, url)
        except AcquisitionError as error:
            error.observation = {"url": url, "received_at": response_received_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"), "parser_version": ODDS_PARSER_VERSION, "response_sha256": digest, "validation_status": "invalid", "error_code": error.code}
            raise
        identity = parse_odds_source_identity(url)
        return odds, {"url": url, "source_race_id": identity["source_race_id"], "received_at": response_received_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"), "source_updated_at": source_updated_at, "parser_version": ODDS_PARSER_VERSION, "response_sha256": digest, "validation_status": "valid"}

    def _purge_expired_cache(self, now: datetime) -> None:
        if self._cache_directory is None or not self._cache_directory.is_dir():
            return
        cutoff = now.timestamp() - timedelta(days=7).total_seconds()
        for path in self._cache_directory.glob("odds-*.html"):
            if path.stat().st_mtime < cutoff:
                path.unlink()

    def _load_recent_cache(self, url: str, now: datetime) -> bytes | None:
        if self._cache_directory is None or not self._cache_directory.is_dir():
            return None
        url_digest = sha256(url.encode("utf-8")).hexdigest()
        minimum_mtime = now.timestamp() - timedelta(minutes=15).total_seconds()
        candidates = sorted(
            self._cache_directory.glob(f"odds-{url_digest}-*.html"),
            key=lambda path: path.stat().st_mtime, reverse=True,
        )
        if not candidates or candidates[0].stat().st_mtime < minimum_mtime:
            return None
        return candidates[0].read_bytes()


class JraResultAcquirer:
    def __init__(self, fetcher: Callable[[str], FetchResponse]) -> None:
        self._fetcher = fetcher
        self._memory_cache: dict[str, tuple[datetime, bytes, str]] = {}

    def acquire(self, url: str, received_at: datetime) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        validate_result_url(url)
        cached = self._memory_cache.get(url)
        if cached is not None and received_at - cached[0] > timedelta(minutes=15):
            del self._memory_cache[url]
            cached = None
        if cached is None:
            robots = self._fetcher(ROBOTS_URL)
            if robots.status != 200 or robots.final_url != ROBOTS_URL or not robots_allows(robots.body, url):
                raise audited_error(
                    "acquisition_stopped", "JRAからの取得を停止しました。CSV取込を使用してください。",
                    503, url, robots.body, received_at, RESULT_PARSER_VERSION,
                )
            response = self._fetcher(url)
            if (response.status != 200 or not same_allowed_race_card_url(response.final_url, url)
                    or not response.headers.get("content-type", "").lower().startswith("text/html")
                    or len(response.body) > MAX_RESPONSE_BYTES):
                raise audited_error(
                    "acquisition_stopped", "JRAからの取得を停止しました。CSV取込を使用してください。",
                    503, url, response.body, received_at, RESULT_PARSER_VERSION,
                )
            body = response.body
            content_type = response.headers.get("content-type", "")
            response_received_at = received_at
            self._memory_cache[url] = (response_received_at, body, content_type)
        else:
            response_received_at, body, content_type = cached
        digest = sha256(body).hexdigest()
        try:
            results, source_updated_at = parse_jra_result_page(body, content_type, url)
        except AcquisitionError as error:
            error.observation = {
                "url": url,
                "received_at": response_received_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                "parser_version": RESULT_PARSER_VERSION,
                "response_sha256": digest, "validation_status": "invalid", "error_code": error.code,
            }
            raise
        identity = parse_source_race_identity(url)
        return results, {
            "url": url, "source_race_id": identity["source_race_id"],
            "received_at": response_received_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "source_updated_at": source_updated_at, "parser_version": RESULT_PARSER_VERSION,
            "response_sha256": digest, "validation_status": "valid",
        }


def validate_race_card_url(url: str) -> None:
    parsed = urlparse(url)
    query = parse_qs(parsed.query, strict_parsing=True)
    cname = query.get("CNAME", [])
    valid_cname = SOURCE_RACE_ID_PATTERN.fullmatch(cname[0]) if len(cname) == 1 else None
    expected_path = None
    if valid_cname is not None:
        expected_path = (
            "/JRADB/accessS.html"
            if valid_cname.group("format").startswith("s") else "/JRADB/accessD.html"
        )
    if (parsed.scheme != "https" or parsed.hostname != ALLOWED_HOST or parsed.port not in {None, 443}
            or parsed.username is not None or parsed.password is not None
            or parsed.path != expected_path or parsed.fragment or set(query) != {"CNAME"}
            or valid_cname is None):
        raise AcquisitionError("url_not_allowed", "許可されたJRAレースページURLを指定してください。", 422)


def validate_result_url(url: str) -> None:
    validate_race_card_url(url)
    try:
        identity = parse_source_race_identity(url)
    except ValueError as error:
        raise AcquisitionError("url_not_allowed", "許可されたJRA結果ページURLを指定してください。", 422) from error
    if identity["resource"] != "result":
        raise AcquisitionError("url_not_allowed", "許可されたJRA結果ページURLを指定してください。", 422)


def same_allowed_race_card_url(left: str, right: str) -> bool:
    try:
        validate_race_card_url(left)
        validate_race_card_url(right)
    except (AcquisitionError, ValueError):
        return False
    left_parsed, right_parsed = urlparse(left), urlparse(right)
    return (
        left_parsed.scheme, left_parsed.hostname, left_parsed.port, left_parsed.path,
        parse_qs(left_parsed.query).get("CNAME"),
    ) == (
        right_parsed.scheme, right_parsed.hostname, right_parsed.port, right_parsed.path,
        parse_qs(right_parsed.query).get("CNAME"),
    )


def audited_error(
    code: str, message: str, status_code: int, url: str, body: bytes, received_at: datetime,
    parser_version: str,
) -> AcquisitionError:
    error = AcquisitionError(code, message, status_code)
    error.observation = {
        "url": url,
        "received_at": received_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "parser_version": parser_version, "response_sha256": sha256(body).hexdigest(),
        "validation_status": "invalid", "error_code": code,
    }
    return error


def robots_allows(body: bytes, url: str) -> bool:
    try:
        text = body.decode("utf-8-sig")
    except UnicodeDecodeError:
        return False
    parser = RobotFileParser()
    parser.set_url(ROBOTS_URL)
    parser.parse(text.splitlines())
    return parser.can_fetch(USER_AGENT, url)


def parse_race_card(
    body: bytes, content_type: str, source_url: str,
) -> tuple[dict[str, Any], list[dict[str, Any]], str | None]:
    try:
        text = decode_html(body, content_type)
    except (LookupError, UnicodeDecodeError) as error:
        raise AcquisitionError("race_card_validation_failed", "JRAレースページを検証できませんでした。", 422) from error
    parser = _TreeParser()
    parser.feed(text)
    entry_container = parser.root.find(element_id="syutsuba")
    result_page = entry_container is None
    terminal_marker = None if result_page else parser.root.find(element_id="odds_area")
    if result_page:
        result_root = parser.root.find(element_id="race_result")
        table = result_root.find(tag="table") if result_root else None
    else:
        table = entry_container.find(tag="table") if entry_container else None
    header = table.find(class_name="race_header") if table else None
    date_element = table.find(class_name="date") if table else None
    rows = table.find_all(tag="tr") if table else []
    try:
        if (table is None or not table.closed
                or (not result_page and (entry_container is None
                                          or not entry_container.closed
                                          or terminal_marker is None
                                          or not terminal_marker.closed
                                          or not elements_are_adjacent_siblings(
                                              parser.root, entry_container, terminal_marker,
                                          )))):
            raise ValueError("incomplete_table")
        date_text = required_text(date_element)
        header_text = required_text(header)
        date_match = required_match(r"(\d{4})年(\d{1,2})月(\d{1,2})日.*?\d+回(.+?)\d+日", date_text)
        race_number_element = header.find(class_name="race_number") if header else None
        race_number_image = require_element(race_number_element.find(tag="img") if race_number_element else None)
        race_number = int(required_match(r"(\d+)レース", race_number_image.attrs.get("alt", ""))[0])
        start = required_match(r"発走時刻[：:]\s*(\d{1,2})時(\d{2})分", header_text)
        course = required_match(
            r"コース[：:]\s*([\d,]+)\s*メートル\s*（\s*(芝|ダート)[^）]*）",
            header_text,
        )
        declared_field_match = None if result_page else re.search(r"(\d+)頭", header_text)
        declared_field_size = int(declared_field_match.group(1)) if declared_field_match else None
        header_element = require_element(header)
        going_node = header_element.find(class_name="turf") or header_element.find(class_name="durt")
        going_text = required_text(going_node.find(class_name="txt") if going_node else None)
        race_date = f"{int(date_match[0]):04d}-{int(date_match[1]):02d}-{int(date_match[2]):02d}"
        start_time = f"{int(start[0]):02d}:{int(start[1]):02d}"
        local_start = datetime.fromisoformat(f"{race_date}T{start_time}:00").replace(
            tzinfo=timezone(timedelta(hours=9)),
        )
        runners = [
            parse_runner(row) for row in rows
            if row.find(tag="td", class_name="num") is not None
        ]
        horse_numbers = [runner["horse_number"] for runner in runners]
        identity = parse_source_race_identity(source_url)
        if ((identity["resource"] == "result") != result_page
                or identity["race_date"] != race_date or identity["race_number"] != race_number
                or identity["racecourse"] != date_match[3]):
            raise ValueError("race_identity")
        if ((declared_field_size is not None and declared_field_size != len(runners))
                or not 1 <= race_number <= 12
                or not 1 <= len(runners) <= 18
                or len(horse_numbers) != len(set(horse_numbers))
                or set(horse_numbers) != set(range(1, len(runners) + 1))):
            raise ValueError("runner_set")
        race = {
            "organizer": "JRA", "country": "JP", "racecourse": date_match[3],
            "race_date": race_date, "race_number": race_number, "start_time": start_time,
            "timezone": "Asia/Tokyo", "start_utc": local_start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "surface": course[1], "distance_m": int(course[0].replace(",", "")),
            "going": going_text, "field_size": len(runners),
        }
        update_element = table.find(class_name="update_time") if table else None
        source_updated_at = None
        if update_element is not None:
            update_value = update_element.attrs.get("datetime", "") or update_element.text()
            parsed_update = datetime.fromisoformat(update_value.replace("Z", "+00:00"))
            if parsed_update.utcoffset() is None:
                raise ValueError("source_update_time")
            source_updated_at = parsed_update.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        return race, runners, source_updated_at
    except (AttributeError, IndexError, TypeError, ValueError) as error:
        raise AcquisitionError("race_card_validation_failed", "JRAレースページを検証できませんでした。", 422) from error


def parse_runner(row: Element) -> dict[str, Any]:
    gate_cell = require_element(row.find(class_name="waku"))
    gate_image = require_element(gate_cell.find(tag="img"))
    age_text = required_text(row.find(class_name="age"))
    age = required_match(r"(牡|牝|セン|せん|騸)(\d+)", age_text)
    status_text = " ".join(filter(None, (
        required_text(row.find(class_name="status"), optional=True),
        required_text(row.find(tag="td", class_name="place"), optional=True),
    )))
    status = "取消" if "取消" in status_text else "除外" if "除外" in status_text else "出走"
    gate = int(required_match(r"枠(\d+)(?:番|\D|$)", gate_image.attrs.get("alt", ""))[0])
    runner_age = int(age[1])
    jockey_cell = row.find(tag="td", class_name="jockey")
    assigned_weight_element = jockey_cell.find(class_name="weight") if jockey_cell else None
    if assigned_weight_element is None:
        assigned_weight_element = row.find(class_name="weight")
    assigned_weight = float(required_match(
        r"([\d.]+)(?:\s*kg)?", required_text(assigned_weight_element), re.I,
    )[0])
    horse_name_element = row.find(class_name="name") or row.find(tag="td", class_name="horse")
    runner = {
        "gate": gate,
        "horse_number": int(required_text(row.find(class_name="num"))),
        "horse_name": required_text(horse_name_element),
        "age": runner_age, "sex": "セン" if age[0] in {"せん", "騸"} else age[0],
        "assigned_weight": assigned_weight,
        "status": status,
    }
    if (not 1 <= gate <= 8 or not 1 <= runner_age <= 20
            or not 40 <= assigned_weight <= 70):
        raise ValueError("runner_bounds")
    return runner


def parse_source_race_identity(url: str) -> dict[str, str | int]:
    cname = parse_qs(urlparse(url).query)["CNAME"][0]
    match = SOURCE_RACE_ID_PATTERN.fullmatch(cname)
    if match is None:
        raise ValueError("race_identity")
    date_value = match.group("date")
    racecourse = RACECOURSES_BY_CODE.get(match.group("course_code"))
    if racecourse is None:
        raise ValueError("race_identity")
    return {
        "source_race_id": (
            f"JRA-{date_value}-{match.group('course_code')}-"
            f"{match.group('meeting')}-{match.group('day')}-{int(match.group('race')):02d}"
        ),
        "resource": "result" if match.group("format").startswith("s") else "race_card",
        "racecourse": racecourse,
        "race_number": int(match.group("race")),
        "race_date": f"{date_value[:4]}-{date_value[4:6]}-{date_value[6:]}",
    }


def parse_jra_result_page(
    body: bytes, content_type: str, source_url: str,
) -> tuple[list[dict[str, Any]], str | None]:
    try:
        _, validated_runners, source_updated_at = parse_race_card(body, content_type, source_url)
        text = decode_html(body, content_type)
        parser = _TreeParser()
        parser.feed(text)
        result_root = require_element(parser.root.find(element_id="race_result"))
        result_table = next(
            table for table in result_root.find_all(tag="table")
            if table.find(tag="th", class_name="place") is not None
            and table.find(tag="th", class_name="num") is not None
            and table.closed
        )
        refund_area = require_element(result_root.find(class_name="refund_area"))
        if not result_root.closed or not refund_area.closed:
            raise ValueError("incomplete_result")

        results: list[dict[str, Any]] = []
        for row in result_table.find_all(tag="tr"):
            number_cell = row.find(tag="td", class_name="num")
            if number_cell is None:
                continue
            horse_number = int(required_text(number_cell))
            place_text = required_text(row.find(tag="td", class_name="place"))
            if place_text in {"取消", "除外"}:
                status = place_text
                finish_position = None
            else:
                status = "確定"
                finish_position = int(place_text)
            results.append({
                "horse_number": horse_number, "finish_position": finish_position,
                "status": status, "win_payout_per_100": 0, "place_payout_per_100": 0,
            })

        by_horse = {int(result["horse_number"]): result for result in results}
        if len(by_horse) != len(results):
            raise ValueError("duplicate_runner")
        win_payouts = parse_result_payouts(refund_area.find(tag="li", class_name="win"))
        place_payouts = parse_result_payouts(refund_area.find(tag="li", class_name="place"))
        refund_numbers = parse_result_numbers(refund_area.find(tag="li", class_name="refund"))
        for horse_number, payout in win_payouts.items():
            by_horse[horse_number]["win_payout_per_100"] = payout
        for horse_number, payout in place_payouts.items():
            by_horse[horse_number]["place_payout_per_100"] = payout

        expected_numbers = {int(runner["horse_number"]) for runner in validated_runners}
        if set(by_horse) != expected_numbers:
            raise ValueError("runner_set")
        confirmed = [result for result in results if result["status"] == "確定"]
        finish_counts: dict[int, int] = {}
        for result in confirmed:
            position = int(result["finish_position"])
            finish_counts[position] = finish_counts.get(position, 0) + 1
        expected_position = 1
        for position in sorted(finish_counts):
            if position != expected_position:
                raise ValueError("finish_sequence")
            expected_position += finish_counts[position]
        field_size = len(results)
        place_positions = 0 if field_size <= 4 else 2 if field_size <= 7 else 3
        expected_win = {int(result["horse_number"]) for result in confirmed if result["finish_position"] == 1}
        expected_place = {
            int(result["horse_number"]) for result in confirmed
            if place_positions > 0 and int(result["finish_position"]) <= place_positions
        }
        expected_refunds = {
            int(result["horse_number"]) for result in results if result["status"] in {"取消", "除外"}
        }
        if (set(win_payouts) != expected_win or set(place_payouts) != expected_place
                or refund_numbers != expected_refunds):
            raise ValueError("result_payout_mismatch")
        return sorted(results, key=lambda item: int(item["horse_number"])), source_updated_at
    except (AttributeError, IndexError, KeyError, LookupError, TypeError, UnicodeDecodeError, ValueError) as error:
        raise AcquisitionError(
            "result_validation_failed", "JRA結果・払戻ページを検証できませんでした。", 422,
        ) from error


def parse_result_payouts(section: Element | None) -> dict[int, int]:
    if section is None:
        return {}
    payouts: dict[int, int] = {}
    for line in section.find_all(class_name="line"):
        horse_number = int(required_text(line.find(class_name="num")))
        payout_text = required_text(line.find(class_name="yen"))
        payout = int(required_match(r"([\d,]+)\s*円", payout_text)[0].replace(",", ""))
        if horse_number in payouts or payout <= 0:
            raise ValueError("payout")
        payouts[horse_number] = payout
    return payouts


def parse_result_numbers(section: Element | None) -> set[int]:
    if section is None:
        return set()
    numbers = {
        int(required_text(line.find(class_name="num")))
        for line in section.find_all(class_name="line")
    }
    if len(numbers) != len(section.find_all(class_name="line")):
        raise ValueError("refund_numbers")
    return numbers


def validate_odds_url(url: str) -> None:
    parsed = urlparse(url)
    query = parse_qs(parsed.query, strict_parsing=True)
    cname = query.get("CNAME", [])
    if (parsed.scheme != "https" or parsed.hostname != ALLOWED_HOST or parsed.port not in {None, 443}
            or parsed.path != "/JRADB/accessO.html" or parsed.fragment or set(query) != {"CNAME"}
            or len(cname) != 1 or ODDS_CNAME_PATTERN.fullmatch(cname[0]) is None):
        raise AcquisitionError("url_not_allowed", "許可されたJRAオッズURLを指定してください。", 422)


def parse_odds_source_identity(url: str) -> dict[str, str | int]:
    cname = parse_qs(urlparse(url).query)["CNAME"][0]
    match = ODDS_CNAME_PATTERN.fullmatch(cname)
    if match is None:
        raise ValueError("odds_identity")
    date_value = match.group("date")
    return {"source_race_id": f"JRA-{date_value}-{match.group('course_code')}-{match.group('meeting')}-{match.group('day')}-{int(match.group('race')):02d}"}


def parse_jra_odds_page(body: bytes, content_type: str) -> list[dict[str, float | int]]:
    try:
        text = decode_html(body, content_type)
        parser = _TreeParser()
        parser.feed(text)
        table = next(
            item for item in parser.root.find_all(tag="table")
            if "単勝・複勝オッズ（馬番順）" in item.text() and item.closed
        )
        rows = [item for item in table.find_all(tag="tr") if len(item.find_all(tag="td")) >= 5]
        odds: list[dict[str, float | int]] = []
        for row in rows:
            cells = row.find_all(tag="td")
            number_cell = row.find(tag="td", class_name="num")
            win_cell = row.find(tag="td", class_name="odds_tan")
            place_cell = row.find(tag="td", class_name="odds_fuku")
            if number_cell is not None and win_cell is not None and place_cell is not None:
                horse_number = int(required_text(number_cell))
                win_odds = float(required_text(win_cell))
                place_text = required_text(place_cell)
            else:
                offset = 1 if required_text(cells[0]).startswith("枠") else 0
                horse_number = int(required_text(cells[offset]))
                win_odds = float(required_text(cells[offset + 2]))
                place_text = required_text(cells[offset + 3])
            place_match = re.fullmatch(r"([\d.]+)\s*-\s*([\d.]+)", place_text)
            if place_match is None:
                raise ValueError("place_range")
            minimum, maximum = float(place_match.group(1)), float(place_match.group(2))
            if not (0 < win_odds <= 100000 and 0 < minimum <= maximum <= 100000):
                raise ValueError("odds_bounds")
            odds.append({"horse_number": horse_number, "win_odds": win_odds,
                         "place_odds_min": minimum, "place_odds_max": maximum})
        numbers = {int(item["horse_number"]) for item in odds}
        if not odds or len(numbers) != len(odds) or numbers != set(range(1, len(odds) + 1)):
            raise ValueError("odds_runner_set")
        return odds
    except (AttributeError, IndexError, LookupError, TypeError, UnicodeDecodeError, ValueError) as error:
        raise AcquisitionError("odds_validation_failed", "JRAオッズページを検証できませんでした。", 422) from error


def parse_jra_odds_update_time(body: bytes, content_type: str, source_url: str) -> str | None:
    text = decode_html(body, content_type)
    match = re.search(r"(?:オッズ)?更新(?:時刻|日時)?\s*[：:]\s*(\d{1,2})時(\d{2})分", text)
    if match is None:
        return None
    cname = parse_qs(urlparse(source_url).query)["CNAME"][0]
    identity = ODDS_CNAME_PATTERN.fullmatch(cname)
    if identity is None:
        raise AcquisitionError("odds_validation_failed", "JRAオッズページを検証できませんでした。", 422)
    date_value = identity.group("date")
    local = datetime(int(date_value[:4]), int(date_value[4:6]), int(date_value[6:]),
                     int(match.group(1)), int(match.group(2)), tzinfo=timezone(timedelta(hours=9)))
    return local.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def validate_odds_page_identity(body: bytes, content_type: str, source_url: str) -> None:
    text = decode_html(body, content_type)
    page = re.search(
        r"(\d{4})年(\d{1,2})月(\d{1,2})日.*?(\d+)回(札幌|函館|福島|新潟|東京|中山|中京|京都|阪神|小倉|架空)(\d+)日.*?(\d+)レース",
        text, re.S,
    )
    cname = parse_qs(urlparse(source_url).query)["CNAME"][0]
    expected = ODDS_CNAME_PATTERN.fullmatch(cname)
    if page is None or expected is None:
        raise AcquisitionError("odds_validation_failed", "JRAオッズページを検証できませんでした。", 422)
    date_value = f"{int(page.group(1)):04d}{int(page.group(2)):02d}{int(page.group(3)):02d}"
    if (date_value != expected.group("date") or int(page.group(4)) != int(expected.group("meeting"))
            or page.group(5) != RACECOURSES_BY_CODE.get(expected.group("course_code"))
            or int(page.group(6)) != int(expected.group("day")) or int(page.group(7)) != int(expected.group("race"))):
        raise AcquisitionError("odds_race_mismatch", "JRAオッズページが選択したレースと一致しません。", 422)


def required_text(element: Element | None, optional: bool = False) -> str:
    value = element.text().strip() if element is not None else ""
    if not value and not optional:
        raise ValueError("required")
    return value


def decode_html(body: bytes, content_type: str) -> str:
    charset_match = re.search(r"charset=([\w-]+)", content_type)
    meta_match = re.search(br"charset=[\"']?([\w-]+)", body[:4096], re.I)
    charset = charset_match.group(1) if charset_match else (
        meta_match.group(1).decode("ascii") if meta_match else "cp932"
    )
    if charset.lower().replace("_", "-") in {"shift-jis", "shiftjis", "sjis", "x-sjis"}:
        charset = "cp932"
    return body.decode(charset)


def require_element(element: Element | None) -> Element:
    if element is None:
        raise ValueError("required")
    return element


def required_match(pattern: str, value: str, flags: int = 0) -> tuple[str, ...]:
    match = re.search(pattern, value, flags)
    if match is None:
        raise ValueError("pattern")
    return match.groups()
