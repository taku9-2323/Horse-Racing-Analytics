from collections.abc import Callable
from datetime import datetime
from typing import Any, Literal, cast
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from pydantic import BaseModel

from app.database import SqliteDatabase
from app.jra_acquisition import AcquisitionError, JraResultAcquirer, parse_source_race_identity


class BulkResultTarget(BaseModel):
    race_id: int
    race_date: str
    racecourse: str
    race_number: int
    status: Literal["pending", "running", "succeeded", "missing", "failed", "stopped"]
    error_code: str | None
    error_message: str | None


class BulkResultRun(BaseModel):
    run_id: int
    page: int
    status: Literal["running", "completed", "stopped"]
    started_at: str
    completed_at: str | None
    target_count: int
    processed_count: int
    succeeded_count: int
    missing_count: int
    failed_count: int
    stop_reason: str | None
    targets: list[BulkResultTarget]


def result_url_from_source(source_url: str) -> str:
    identity = parse_source_race_identity(source_url)
    parsed = urlparse(source_url)
    cname = parse_qs(parsed.query, strict_parsing=True)["CNAME"][0]
    if identity["resource"] == "result":
        result_cname = cname
    else:
        result_cname = cname.replace("dde", "sde", 1)
    return urlunparse(parsed._replace(
        path="/JRADB/accessS.html", query=urlencode({"CNAME": result_cname}, safe="/"),
    ))


def bulk_result_response(database: SqliteDatabase, run_id: int) -> BulkResultRun | None:
    stored = database.get_bulk_result_run(run_id)
    if stored is None:
        return None
    run, targets = stored
    return BulkResultRun(
        run_id=int(run["id"]), page=int(run["page"]),
        status=cast(Literal["running", "completed", "stopped"], str(run["status"])),
        started_at=str(run["started_at"]), completed_at=run["completed_at"],
        target_count=int(run["target_count"]), processed_count=int(run["processed_count"]),
        succeeded_count=int(run["succeeded_count"]), missing_count=int(run["missing_count"]),
        failed_count=int(run["failed_count"]),
        stop_reason=run["stop_reason"],
        targets=[BulkResultTarget(
            race_id=int(target["race_id"]), race_date=str(target["race_date"]),
            racecourse=str(target["racecourse"]), race_number=int(target["race_number"]),
            status=cast(
                Literal["pending", "running", "succeeded", "missing", "failed", "stopped"],
                str(target["status"]),
            ), error_code=target["error_code"], error_message=target["error_message"],
        ) for target in targets],
    )


class BulkResultAcquisitionService:
    def __init__(
        self, database: SqliteDatabase, acquirer: JraResultAcquirer,
        now_provider: Callable[[], datetime], utc_iso: Callable[[datetime], str],
    ) -> None:
        self._database = database
        self._acquirer = acquirer
        self._now = now_provider
        self._utc_iso = utc_iso

    def run(self, run_id: int) -> None:
        stored = self._database.get_bulk_result_run(run_id)
        if stored is None:
            return
        _, targets = stored
        for target in targets:
            if str(target["status"]) != "pending":
                continue
            race_id = int(target["race_id"])
            self._database.update_bulk_result_target(
                run_id, race_id, "running", None, None, self._utc_iso(self._now()),
            )
            source_url = target["source_url"]
            if source_url is None:
                self._database.update_bulk_result_target(
                    run_id, race_id, "failed", "source_identity_missing",
                    "保存済みデータからJRA結果ページを特定できません。", self._utc_iso(self._now()),
                )
                self._database.refresh_bulk_result_run(run_id)
                continue
            observation: dict[str, Any] | None = None
            try:
                result_url = result_url_from_source(str(source_url))
                results, observation = self._acquirer.acquire(result_url, self._now())
                self._database.create_result_version(
                    race_id, results, self._utc_iso(self._now()), source_observation=observation,
                )
                self._database.update_bulk_result_target(
                    run_id, race_id, "succeeded", None, None, self._utc_iso(self._now()),
                )
            except AcquisitionError as error:
                if error.observation is not None:
                    self._database.save_acquisition_failure(error.observation)
                status = "stopped" if error.code == "acquisition_stopped" else (
                    "missing" if error.code == "result_not_published" else "failed"
                )
                self._database.update_bulk_result_target(
                    run_id, race_id, status, error.code, error.message, self._utc_iso(self._now()),
                )
                self._database.refresh_bulk_result_run(
                    run_id, stop_reason=error.code if status == "stopped" else None,
                )
                if status == "stopped":
                    return
            except (LookupError, ValueError) as error:
                self._database.update_bulk_result_target(
                    run_id, race_id, "failed", str(error),
                    "取得した結果を保存済みレースへ登録できません。", self._utc_iso(self._now()),
                )
            self._database.refresh_bulk_result_run(run_id)
        self._database.complete_bulk_result_run(run_id, self._utc_iso(self._now()))
