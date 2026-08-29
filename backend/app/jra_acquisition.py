from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from html.parser import HTMLParser
from pathlib import Path
import os
import re
from typing import Any
from urllib.parse import parse_qs, urlparse
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.robotparser import RobotFileParser

from pydantic import BaseModel


ALLOWED_HOST = "www.jra.go.jp"
ROBOTS_URL = f"https://{ALLOWED_HOST}/robots.txt"
PARSER_VERSION = "jra-race-card/1"
USER_AGENT = "HorseRacingAnalyticsLocalPrototype/0.1"
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
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
    fragments: list[str] = field(default_factory=list)

    def text(self) -> str:
        return " ".join(" ".join([*self.fragments, *(child.text() for child in self.children)]).split())

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
        if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}:
            self._stack.append(element)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if self._stack[-1].tag == tag:
            self._stack.pop()

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self._stack) - 1, 0, -1):
            if self._stack[index].tag == tag:
                del self._stack[index:]
                return

    def handle_data(self, data: str) -> None:
        if data.strip():
            self._stack[-1].fragments.append(data)


def default_fetcher(url: str) -> FetchResponse:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,text/plain;q=0.9"})
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

    def acquire(self, url: str, received_at: datetime) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any], bytes]:
        validate_race_card_url(url)
        self._purge_expired_cache(received_at)
        cached = self._load_recent_cache(url, received_at)
        if cached is not None:
            return self._normalize(url, cached, "text/html", received_at)
        robots = self._fetcher(ROBOTS_URL)
        if robots.status != 200 or robots.final_url != ROBOTS_URL or not robots_allows(robots.body, url):
            raise audited_error(
                "acquisition_stopped", "JRAからの取得を停止しました。CSV取込を使用してください。",
                503, url, robots.body, received_at,
            )
        response = self._fetcher(url)
        if response.status != 200 or response.final_url != url:
            raise audited_error(
                "acquisition_stopped", "JRAからの取得を停止しました。CSV取込を使用してください。",
                503, url, response.body, received_at,
            )
        content_type = response.headers.get("content-type", "").lower()
        if not content_type.startswith("text/html") or len(response.body) > MAX_RESPONSE_BYTES:
            raise audited_error(
                "unexpected_response", "想定外の応答です。CSV取込を使用してください。",
                503, url, response.body, received_at,
            )
        return self._normalize(url, response.body, content_type, received_at)

    def _normalize(
        self, url: str, body: bytes, content_type: str, received_at: datetime,
    ) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any], bytes]:
        digest = sha256(body).hexdigest()
        received_utc = received_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        try:
            race, runners, source_updated_at = parse_race_card(body, content_type, url)
        except AcquisitionError as error:
            error.observation = {
                "url": url, "received_at": received_utc, "parser_version": PARSER_VERSION,
                "response_sha256": digest, "validation_status": "invalid", "error_code": error.code,
            }
            raise
        observation = {
            "url": url, "source_race_id": parse_qs(urlparse(url).query)["CNAME"][0],
            "received_at": received_utc, "source_updated_at": source_updated_at,
            "parser_version": PARSER_VERSION, "response_sha256": digest,
            "validation_status": "valid",
        }
        self._cache(url, body, digest, received_at)
        return race, runners, observation, body

    def _cache(self, url: str, body: bytes, digest: str, now: datetime) -> None:
        self._cache_directory.mkdir(parents=True, exist_ok=True)
        url_digest = sha256(url.encode("utf-8")).hexdigest()
        path = self._cache_directory / f"{url_digest}-{digest}.html"
        if not path.exists():
            path.write_bytes(body)
            os.utime(path, (now.timestamp(), now.timestamp()))

    def _purge_expired_cache(self, now: datetime) -> None:
        if not self._cache_directory.is_dir():
            return
        cutoff = now.timestamp() - timedelta(days=7).total_seconds()
        for path in self._cache_directory.glob("*.html"):
            if path.stat().st_mtime < cutoff:
                path.unlink()

    def _load_recent_cache(self, url: str, now: datetime) -> bytes | None:
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


def validate_race_card_url(url: str) -> None:
    parsed = urlparse(url)
    query = parse_qs(parsed.query, strict_parsing=True)
    cname = query.get("CNAME", [])
    valid_cname = len(cname) == 1 and re.fullmatch(r"pw01dde01[0-9]{18,30}/[A-Za-z0-9]{2}", cname[0])
    if (parsed.scheme != "https" or parsed.hostname != ALLOWED_HOST or parsed.port not in {None, 443}
            or parsed.username is not None or parsed.password is not None
            or parsed.path != "/JRADB/accessD.html" or parsed.fragment or set(query) != {"CNAME"}
            or valid_cname is None):
        raise AcquisitionError("url_not_allowed", "許可されたJRA出馬表URLを指定してください。", 422)


def audited_error(
    code: str, message: str, status_code: int, url: str, body: bytes, received_at: datetime,
) -> AcquisitionError:
    error = AcquisitionError(code, message, status_code)
    error.observation = {
        "url": url,
        "received_at": received_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "parser_version": PARSER_VERSION, "response_sha256": sha256(body).hexdigest(),
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
    charset_match = re.search(r"charset=([\w-]+)", content_type)
    meta_match = re.search(br"charset=[\"']?([\w-]+)", body[:4096], re.I)
    charset = charset_match.group(1) if charset_match else (
        meta_match.group(1).decode("ascii") if meta_match else "cp932"
    )
    try:
        text = body.decode(charset)
    except (LookupError, UnicodeDecodeError) as error:
        raise AcquisitionError("race_card_validation_failed", "出馬表を検証できませんでした。", 422) from error
    parser = _TreeParser()
    parser.feed(text)
    table = parser.root.find(element_id="syutsuba")
    header = table.find(class_name="race_header") if table else None
    date_element = table.find(class_name="date") if table else None
    rows = table.find_all(tag="tr") if table else []
    try:
        date_text = required_text(date_element)
        header_text = required_text(header)
        date_match = required_match(r"(\d{4})年(\d{1,2})月(\d{1,2})日.*?\d+回(.+?)\d+日", date_text)
        race_number_element = header.find(class_name="race_number") if header else None
        race_number_image = require_element(race_number_element.find(tag="img") if race_number_element else None)
        race_number = int(required_match(r"(\d+)レース", race_number_image.attrs.get("alt", ""))[0])
        start = required_match(r"発走時刻[：:]\s*(\d{1,2})時(\d{2})分", header_text)
        course = required_match(r"コース[：:]\s*([\d,]+)メートル（(芝|ダート)[^）]*）", header_text)
        declared_field_size = int(required_match(r"(\d+)頭", header_text)[0])
        header_element = require_element(header)
        going_node = header_element.find(class_name="turf") or header_element.find(class_name="durt")
        going_text = required_text(going_node.find(class_name="txt") if going_node else None)
        race_date = f"{int(date_match[0]):04d}-{int(date_match[1]):02d}-{int(date_match[2]):02d}"
        start_time = f"{int(start[0]):02d}:{int(start[1]):02d}"
        local_start = datetime.fromisoformat(f"{race_date}T{start_time}:00").replace(
            tzinfo=timezone(timedelta(hours=9)),
        )
        runners = [parse_runner(row) for row in rows if row.find(class_name="num") is not None]
        horse_numbers = [runner["horse_number"] for runner in runners]
        identity = parse_source_race_identity(source_url)
        if (identity["race_date"] != race_date or identity["race_number"] != race_number
                or identity["racecourse"] != date_match[3]):
            raise ValueError("race_identity")
        if (declared_field_size != len(runners) or not 1 <= race_number <= 12
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
        raise AcquisitionError("race_card_validation_failed", "出馬表を検証できませんでした。", 422) from error


def parse_runner(row: Element) -> dict[str, Any]:
    gate_cell = require_element(row.find(class_name="waku"))
    gate_image = require_element(gate_cell.find(tag="img"))
    age_text = required_text(row.find(class_name="age"))
    age = required_match(r"(牡|牝|セン)(\d+)", age_text)
    status_text = required_text(row.find(class_name="status"), optional=True)
    status = "取消" if "取消" in status_text else "除外" if "除外" in status_text else "出走"
    gate = int(required_match(r"枠(\d+)番", gate_image.attrs.get("alt", ""))[0])
    runner_age = int(age[1])
    assigned_weight = float(required_match(r"([\d.]+)\s*kg", required_text(row.find(class_name="weight")), re.I)[0])
    runner = {
        "gate": gate,
        "horse_number": int(required_text(row.find(class_name="num"))),
        "horse_name": required_text(row.find(class_name="name")),
        "age": runner_age, "sex": age[0],
        "assigned_weight": assigned_weight,
        "status": status,
    }
    if (not 1 <= gate <= 8 or not 1 <= runner_age <= 20
            or not 40 <= assigned_weight <= 70):
        raise ValueError("runner_bounds")
    return runner


def parse_source_race_identity(url: str) -> dict[str, str | int]:
    cname = parse_qs(urlparse(url).query)["CNAME"][0]
    match = re.fullmatch(
        r"pw01dde01(?P<course_code>\d{2})(?P<year>\d{4})(?P<meeting>\d{2})"
        r"(?P<day>\d{2})(?P<race>\d{2})(?P<date>\d{8})/[A-Za-z0-9]{2}", cname,
    )
    if match is None:
        raise ValueError("race_identity")
    date_value = match.group("date")
    racecourse = RACECOURSES_BY_CODE.get(match.group("course_code"))
    if racecourse is None:
        raise ValueError("race_identity")
    return {
        "source_race_id": cname,
        "racecourse": racecourse,
        "race_number": int(match.group("race")),
        "race_date": f"{date_value[:4]}-{date_value[4:6]}-{date_value[6:]}",
    }


def required_text(element: Element | None, optional: bool = False) -> str:
    value = element.text().strip() if element is not None else ""
    if not value and not optional:
        raise ValueError("required")
    return value


def require_element(element: Element | None) -> Element:
    if element is None:
        raise ValueError("required")
    return element


def required_match(pattern: str, value: str, flags: int = 0) -> tuple[str, ...]:
    match = re.search(pattern, value, flags)
    if match is None:
        raise ValueError("pattern")
    return match.groups()
