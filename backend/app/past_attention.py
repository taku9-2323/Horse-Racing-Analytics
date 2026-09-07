from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel

from app.database import SqliteDatabase


class PastAttentionHorse(BaseModel):
    race_id: int
    race_date: str
    racecourse: str
    race_number: int
    start_time: str
    horse_number: int
    horse_name: str
    pre_race_attention: bool
    post_start_attention: bool
    pre_race_snapshot_id: int | None
    pre_race_judgement_id: int | None
    post_start_snapshot_id: int | None
    post_start_judgement_id: int | None
    result_status: Literal["確定", "取消", "除外", "競走中止"] | None
    finish_position: int | None
    has_result_correction: bool


class PastAttentionWeek(BaseModel):
    week_start: str
    week_end: str
    horses: list[PastAttentionHorse]


class PastAttentionPage(BaseModel):
    page: int
    weeks_per_page: int = 4
    total_week_count: int
    has_newer: bool
    has_older: bool
    weeks: list[PastAttentionWeek]


def past_attention_page(
    database: SqliteDatabase, current_week_start: str, page: int,
) -> PastAttentionPage:
    total_week_count, week_starts, horses_by_week = database.list_past_attention(
        current_week_start, page, 4,
    )
    return PastAttentionPage(
        page=page, total_week_count=total_week_count, has_newer=page > 1,
        has_older=page * 4 < total_week_count,
        weeks=[PastAttentionWeek(
            week_start=week_start,
            week_end=(date.fromisoformat(week_start) + timedelta(days=6)).isoformat(),
            horses=[PastAttentionHorse(**row) for row in horses_by_week.get(week_start, [])],
        ) for week_start in week_starts],
    )
