from datetime import date, datetime, timezone
import sqlite3
from typing import Any, Literal

from pydantic import BaseModel


class RulePerformanceFilters(BaseModel):
    race_date_from: str | None
    race_date_to: str | None
    frozen_at_from: str | None
    frozen_at_to: str | None
    rule_version_id: int | None


class RulePerformanceVersion(BaseModel):
    id: int
    rule_key: str
    version: int
    title: str


class RulePerformanceCounts(BaseModel):
    considered_races: int
    candidate_runs: int
    selected_runs: int
    duplicate_runs_excluded: int
    ineligible_runs_excluded: int
    invalidated_runs_excluded: int
    invalidated_without_active_pre_race_run: int
    candidate_races: int
    selected_races: int
    selected_snapshots: int
    selected_runner_observations: int
    attention_runners: int
    dismissed_runners: int
    undecidable_runners: int
    selected_race_date_from: str | None
    selected_race_date_to: str | None
    selected_frozen_at_from: str | None
    selected_frozen_at_to: str | None


class RulePerformanceBetType(BaseModel):
    status: Literal["available", "no_evaluated_tickets", "market_unavailable"]
    candidate_tickets: int
    evaluated_tickets: int
    hit_tickets: int
    hit_rate: float | None
    stake_yen: int | None
    payout_yen: int | None
    return_rate: float | None
    refund_unverified_tickets: int
    refund_yen: None
    refund_state: Literal["unverified"]
    exclusions: dict[str, int]


class RulePerformanceSelectedRun(BaseModel):
    race_id: int
    race_date: str
    racecourse: str
    race_number: int
    judgement_run_id: int
    input_snapshot_id: int
    frozen_at: str
    active_result_version_id: int | None
    active_result_version_number: int | None


class RulePerformanceGroup(BaseModel):
    rule_version: RulePerformanceVersion
    status: Literal["available", "no_runs"]
    counts: RulePerformanceCounts
    selected_runs: list[RulePerformanceSelectedRun]
    result_exclusion_races: dict[str, int]
    corrected_result_races: int
    bet_types: dict[Literal["win", "place"], RulePerformanceBetType]


class RulePerformanceReport(BaseModel):
    filters: RulePerformanceFilters
    groups: list[RulePerformanceGroup]
    disclaimer: str = (
        "保存済みの事前判定と公式払戻の記述集計です。期待値、予測確率、購入推奨、"
        "将来の収益性、利益保証を示すものではありません。"
    )


def _as_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _timestamp_in_range(
    value: str, lower: datetime | None, upper: datetime | None,
) -> bool:
    parsed = _as_datetime(value)
    return (lower is None or parsed >= lower) and (upper is None or parsed <= upper)


def _filters_as_strings(
    race_date_from: date | None,
    race_date_to: date | None,
    frozen_at_from: datetime | None,
    frozen_at_to: datetime | None,
    rule_version_id: int | None,
) -> RulePerformanceFilters:
    def timestamp(value: datetime | None) -> str | None:
        if value is None:
            return None
        utc_value = value.astimezone(timezone.utc)
        return utc_value.isoformat().replace("+00:00", "Z")

    return RulePerformanceFilters(
        race_date_from=None if race_date_from is None else race_date_from.isoformat(),
        race_date_to=None if race_date_to is None else race_date_to.isoformat(),
        frozen_at_from=timestamp(frozen_at_from), frozen_at_to=timestamp(frozen_at_to),
        rule_version_id=rule_version_id,
    )


def _run_records(
    rows: list[sqlite3.Row],
    race_date_from: date | None,
    race_date_to: date | None,
    frozen_at_from: datetime | None,
    frozen_at_to: datetime | None,
) -> dict[int, dict[int, dict[str, Any]]]:
    groups: dict[int, dict[int, dict[str, Any]]] = {}
    for source in rows:
        row = dict(source)
        race_day = date.fromisoformat(str(row["race_date"]))
        if race_date_from is not None and race_day < race_date_from:
            continue
        if race_date_to is not None and race_day > race_date_to:
            continue
        if not _timestamp_in_range(str(row["frozen_at"]), frozen_at_from, frozen_at_to):
            continue
        group = groups.setdefault(int(row["rule_version_id"]), {})
        run_id = int(row["judgement_run_id"])
        record = group.get(run_id)
        if record is None:
            record = row.copy()
            record["runners"] = []
            group[run_id] = record
        if row["horse_number"] is not None:
            record["runners"].append({
                "horse_number": int(row["horse_number"]),
                "horse_name": str(row["horse_name"]),
                "judgement": str(row["judgement"]),
                "result_status": row["result_runner_status"],
                "finish_position": row["finish_position"],
                "win_payout": row["win_payout_per_100"],
                "place_payout": row["place_payout_per_100"],
            })
    return groups


def _result_exclusion_reason(run: dict[str, Any]) -> str | None:
    roster_count = int(run["roster_runner_count"])
    if roster_count != int(run["field_size"]):
        return "result_incomplete"
    if run["active_result_version_id"] is None:
        return "result_pending"
    if (
        int(run["active_result_version_count"]) != 1
        or int(run["result_runner_count"]) != roster_count
        or int(run["matched_result_runner_count"]) != roster_count
    ):
        return "result_incomplete"
    if not bool(run["has_valid_result_observation"]):
        return "result_source_unverified"
    return None


def _empty_bet_accumulator() -> dict[str, Any]:
    return {
        "candidate_tickets": 0, "evaluated_tickets": 0, "hit_tickets": 0,
        "stake_yen": 0, "payout_yen": 0, "refund_unverified_tickets": 0,
        "exclusions": {},
    }


def _exclude_ticket(accumulator: dict[str, Any], reason: str) -> None:
    exclusions: dict[str, int] = accumulator["exclusions"]
    exclusions[reason] = exclusions.get(reason, 0) + 1
    if reason == "refund_unverified":
        accumulator["refund_unverified_tickets"] += 1


def _ticket_response(
    accumulator: dict[str, Any], *, market_unavailable_tickets: int,
) -> RulePerformanceBetType:
    evaluated = int(accumulator["evaluated_tickets"])
    candidates = int(accumulator["candidate_tickets"])
    status: Literal["available", "no_evaluated_tickets", "market_unavailable"]
    if evaluated:
        status = "available"
    elif candidates > 0 and market_unavailable_tickets == candidates:
        status = "market_unavailable"
    else:
        status = "no_evaluated_tickets"
    stake: int | None = int(accumulator["stake_yen"]) if evaluated else None
    payout: int | None = int(accumulator["payout_yen"]) if evaluated else None
    return RulePerformanceBetType(
        status=status,
        candidate_tickets=candidates,
        evaluated_tickets=evaluated,
        hit_tickets=int(accumulator["hit_tickets"]),
        hit_rate=(int(accumulator["hit_tickets"]) / evaluated) if evaluated else None,
        stake_yen=stake,
        payout_yen=payout,
        return_rate=(payout / stake) if payout is not None and stake else None,
        refund_unverified_tickets=int(accumulator["refund_unverified_tickets"]),
        refund_yen=None,
        refund_state="unverified",
        exclusions=dict(sorted(accumulator["exclusions"].items())),
    )


def build_rule_performance_report(
    rule_versions: list[sqlite3.Row],
    rows: list[sqlite3.Row],
    *,
    race_date_from: date | None = None,
    race_date_to: date | None = None,
    frozen_at_from: datetime | None = None,
    frozen_at_to: datetime | None = None,
    rule_version_id: int | None = None,
) -> RulePerformanceReport:
    grouped_runs = _run_records(
        rows, race_date_from, race_date_to, frozen_at_from, frozen_at_to,
    )
    selected_versions = [
        version for version in rule_versions
        if rule_version_id is None or int(version["id"]) == rule_version_id
    ]
    report_groups: list[RulePerformanceGroup] = []
    for version in selected_versions:
        version_id = int(version["id"])
        runs = list(grouped_runs.get(version_id, {}).values())
        by_race: dict[int, list[dict[str, Any]]] = {}
        for run in runs:
            by_race.setdefault(int(run["race_id"]), []).append(run)

        candidates_by_race: dict[int, list[dict[str, Any]]] = {}
        for race_id, race_runs in by_race.items():
            candidates = [
                run for run in race_runs
                if str(run["run_status"]) == "active"
                and bool(run["official_pre_race_eligible"])
            ]
            if candidates:
                candidates_by_race[race_id] = candidates
        selected_runs = [
            sorted(
                candidates,
                key=lambda run: (_as_datetime(str(run["frozen_at"])), int(run["judgement_run_id"])),
            )[-1]
            for candidates in candidates_by_race.values()
        ]
        selected_runs.sort(key=lambda run: (str(run["race_date"]), int(run["race_id"])))

        candidate_run_count = sum(len(candidates) for candidates in candidates_by_race.values())
        invalidated_without_candidate = sum(
            1 for race_runs in by_race.values()
            if not any(str(run["run_status"]) == "active"
                       and bool(run["official_pre_race_eligible"]) for run in race_runs)
            and any(str(run["run_status"]) == "invalidated" for run in race_runs)
        )
        selected_runner_observations = sum(len(run["runners"]) for run in selected_runs)
        counts = RulePerformanceCounts(
            considered_races=len(by_race),
            candidate_runs=candidate_run_count,
            selected_runs=len(selected_runs),
            duplicate_runs_excluded=candidate_run_count - len(selected_runs),
            ineligible_runs_excluded=sum(
                1 for run in runs if str(run["run_status"]) == "active"
                and not bool(run["official_pre_race_eligible"])
            ),
            invalidated_runs_excluded=sum(
                1 for run in runs if str(run["run_status"]) == "invalidated"
            ),
            invalidated_without_active_pre_race_run=invalidated_without_candidate,
            candidate_races=len(candidates_by_race),
            selected_races=len({int(run["race_id"]) for run in selected_runs}),
            selected_snapshots=len({int(run["input_snapshot_id"]) for run in selected_runs}),
            selected_runner_observations=selected_runner_observations,
            attention_runners=sum(
                1 for run in selected_runs for runner in run["runners"]
                if runner["judgement"] == "注目"
            ),
            dismissed_runners=sum(
                1 for run in selected_runs for runner in run["runners"]
                if runner["judgement"] == "見送り"
            ),
            undecidable_runners=sum(
                1 for run in selected_runs for runner in run["runners"]
                if runner["judgement"] not in {"注目", "見送り"}
            ),
            selected_race_date_from=(
                min((str(run["race_date"]) for run in selected_runs), default=None)
            ),
            selected_race_date_to=(
                max((str(run["race_date"]) for run in selected_runs), default=None)
            ),
            selected_frozen_at_from=(
                min((str(run["frozen_at"]) for run in selected_runs), default=None)
            ),
            selected_frozen_at_to=(
                max((str(run["frozen_at"]) for run in selected_runs), default=None)
            ),
        )

        bet_accumulators = {bet_type: _empty_bet_accumulator() for bet_type in ("win", "place")}
        unavailable_place_candidates = 0
        result_exclusion_races: dict[str, set[int]] = {}
        corrected_result_races: set[int] = set()
        for run in selected_runs:
            race_id = int(run["race_id"])
            result_reason = _result_exclusion_reason(run)
            if result_reason is not None:
                result_exclusion_races.setdefault(result_reason, set()).add(race_id)
            if run["active_result_version_id"] is not None and (
                run["active_result_version"] is not None
                and (int(run["active_result_version"]) > 1
                     or run["result_correction_reason"] is not None)
            ):
                corrected_result_races.add(race_id)
            for runner in run["runners"]:
                if runner["judgement"] != "注目":
                    continue
                for bet_type, payout_field in (
                    ("win", "win_payout"), ("place", "place_payout"),
                ):
                    accumulator = bet_accumulators[bet_type]
                    accumulator["candidate_tickets"] += 1
                    if result_reason == "result_incomplete":
                        _exclude_ticket(accumulator, result_reason)
                        continue
                    if bet_type == "place" and int(run["roster_runner_count"]) <= 4:
                        _exclude_ticket(accumulator, "place_market_unavailable")
                        unavailable_place_candidates += 1
                        continue
                    if result_reason is not None:
                        _exclude_ticket(accumulator, result_reason)
                        continue
                    runner_status = runner["result_status"]
                    if runner_status is None:
                        _exclude_ticket(accumulator, "payout_unavailable")
                        continue
                    if runner_status in {"取消", "除外"}:
                        _exclude_ticket(accumulator, "refund_unverified")
                        continue
                    if runner_status not in {"確定", "競走中止"}:
                        _exclude_ticket(accumulator, "payout_unavailable")
                        continue
                    if runner_status == "確定" and runner["finish_position"] is None:
                        _exclude_ticket(accumulator, "payout_unavailable")
                        continue
                    if runner_status == "競走中止":
                        payout = 0
                    else:
                        payout_value = runner[payout_field]
                        if payout_value is None:
                            _exclude_ticket(accumulator, "payout_unavailable")
                            continue
                        payout = int(payout_value)
                    accumulator["evaluated_tickets"] += 1
                    accumulator["stake_yen"] += 100
                    accumulator["payout_yen"] += payout
                    if payout > 0:
                        accumulator["hit_tickets"] += 1

        report_groups.append(RulePerformanceGroup(
            rule_version=RulePerformanceVersion(
                id=version_id, rule_key=str(version["rule_key"]),
                version=int(version["version"]), title=str(version["title"]),
            ),
            status="available" if selected_runs else "no_runs",
            counts=counts,
            selected_runs=[RulePerformanceSelectedRun(
                race_id=int(run["race_id"]),
                race_date=str(run["race_date"]),
                racecourse=str(run["racecourse"]),
                race_number=int(run["race_number"]),
                judgement_run_id=int(run["judgement_run_id"]),
                input_snapshot_id=int(run["input_snapshot_id"]),
                frozen_at=str(run["frozen_at"]),
                active_result_version_id=(
                    None if run["active_result_version_id"] is None
                    else int(run["active_result_version_id"])
                ),
                active_result_version_number=(
                    None if run["active_result_version"] is None
                    else int(run["active_result_version"])
                ),
            ) for run in selected_runs],
            result_exclusion_races={
                reason: len(race_ids)
                for reason, race_ids in sorted(result_exclusion_races.items())
            },
            corrected_result_races=len(corrected_result_races),
            bet_types={
                "win": _ticket_response(
                    bet_accumulators["win"], market_unavailable_tickets=0,
                ),
                "place": _ticket_response(
                    bet_accumulators["place"],
                    market_unavailable_tickets=unavailable_place_candidates,
                ),
            },
        ))
    return RulePerformanceReport(
        filters=_filters_as_strings(
            race_date_from, race_date_to, frozen_at_from, frozen_at_to, rule_version_id,
        ),
        groups=report_groups,
    )
