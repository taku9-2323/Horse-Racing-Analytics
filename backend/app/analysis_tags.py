import json
from datetime import datetime
from typing import Annotated, Any, Literal, cast

from pydantic import BaseModel, StringConstraints, field_validator


INITIAL_ANALYSIS_TAGS: list[dict[str, Any]] = [
    {
        "rule_key": "market_odds_level", "title": "単勝オッズ水準",
        "source_url": "https://hiroakiyusheng.github.io/papers/hanyu2025final.pdf",
        "evidence_summary": "オッズと実勝率、実現収益には記述的関連があるが、保存時点での期間外利益は未検証。",
        "study_period": "2004–2023年", "population": "JRA 63,372レース・895,090頭",
        "evidence_quality": "B", "conditions": {"filters": [{"field": "win_odds", "operator": "gt", "value": 0}]},
    },
    {
        "rule_key": "odds_movement_snapshot", "title": "保存時点間の単勝オッズ変動",
        "source_url": "https://hiroakiyusheng.github.io/papers/hanyu2025final.pdf",
        "evidence_summary": "最終5分のオッズ低下との関連は事後分析であり、事前の収益予測を示さない。",
        "study_period": "2004–2023年", "population": "JRA時系列単勝オッズ",
        "evidence_quality": "B", "conditions": {"filters": [
            {"field": "snapshot_count", "operator": "gte", "value": 2},
            {"field": "observed_before_start", "operator": "eq", "value": True},
        ]},
    },
    {
        "rule_key": "relative_draw_context", "title": "相対馬番とコース文脈",
        "source_url": "https://doi.org/10.1111/jbg.12822",
        "evidence_summary": "馬番効果は競馬場・距離別の能力関連で、勝率や馬券収益の証拠ではない。",
        "study_period": "1986–2021年", "population": "JRA主要4場・良馬場芝・3歳以上",
        "evidence_quality": "A", "conditions": {"filters": [{"field": "field_size", "operator": "gte", "value": 2}]},
    },
    {
        "rule_key": "surface_going_course_distance", "title": "競馬場・馬場・距離文脈",
        "source_url": "https://doi.org/10.1016/j.jevs.2012.02.012",
        "evidence_summary": "馬場状態は走破時計と関連するが、目的変数は時計であり収益ではない。",
        "study_period": "2000–2004年", "population": "JRA全10競馬場の平地競走",
        "evidence_quality": "A", "conditions": {"filters": [{"field": "surface", "operator": "in", "value": ["芝", "ダート"]}]},
    },
    {
        "rule_key": "age_sex_context", "title": "年齢・性別の文脈",
        "source_url": "https://doi.org/10.1111/jbg.12822",
        "evidence_summary": "年齢・性別は限定母集団の能力関連で、単独の有利不利や利益を示さない。",
        "study_period": "1986–2021年", "population": "JRA主要4場・良馬場芝・3歳以上",
        "evidence_quality": "A", "conditions": {"filters": [{"field": "age", "operator": "gte", "value": 2}]},
    },
    {
        "rule_key": "assigned_weight_context", "title": "負担重量とレース内中央値差",
        "source_url": "https://www.jra.go.jp/keiba/rules/weight.html",
        "evidence_summary": "負担重量には能力・年齢・性別等の交絡があり、方向性の補正には使えない。",
        "study_period": "現行規則（2026-08-15閲覧）", "population": "JRA競走の負担重量制度",
        "evidence_quality": "A", "conditions": {"filters": [{"field": "assigned_weight", "operator": "gt", "value": 0}]},
    },
    {
        "rule_key": "field_size_place_rule", "title": "複勝の頭数規則",
        "source_url": "https://www.jra.go.jp/kouza/yougo/c10050_list.html",
        "evidence_summary": "複勝の的中順位を定める公式券種規則であり、予測上の優位性ではない。",
        "study_period": "現行規則（2026-08-15閲覧）", "population": "JRA複勝発売レース",
        "evidence_quality": "A", "conditions": {"filters": [{"field": "field_size", "operator": "gt", "value": 0}]},
    },
]


class AnalysisTagConditionError(ValueError):
    pass


FILTER_FIELDS = {
    "market_odds_level": {"win_odds", "horse_number"},
    "odds_movement_snapshot": {"snapshot_count", "observed_before_start"},
    "relative_draw_context": {"field_size", "racecourse", "surface", "distance_m", "horse_number", "gate"},
    "surface_going_course_distance": {"racecourse", "surface", "distance_m", "going"},
    "age_sex_context": {"age", "sex", "racecourse", "surface", "distance_m"},
    "assigned_weight_context": {"assigned_weight", "horse_number"},
    "field_size_place_rule": {"field_size"},
}
RUNNER_FILTER_FIELDS = {"win_odds", "horse_number", "gate", "age", "sex", "assigned_weight"}
FILTER_OPERATORS = {"eq", "neq", "gt", "gte", "lt", "lte", "in", "range"}
NUMERIC_FILTER_FIELDS = {"win_odds", "horse_number", "snapshot_count", "field_size", "distance_m", "gate", "age", "assigned_weight"}
TEXT_FILTER_FIELDS = {"racecourse", "surface", "going", "sex"}


def validate_tag_conditions(rule_key: str, conditions: dict[str, Any]) -> None:
    filters = conditions.get("filters")
    if not isinstance(filters, list) or not filters:
        raise AnalysisTagConditionError("filtersには1件以上の条件を指定してください。")
    allowed_fields = FILTER_FIELDS.get(rule_key)
    if allowed_fields is None:
        raise AnalysisTagConditionError("未対応の分析タグです。")
    for condition in filters:
        if not isinstance(condition, dict):
            raise AnalysisTagConditionError("各条件はJSONオブジェクトで指定してください。")
        field = condition.get("field")
        operator = condition.get("operator")
        if field not in allowed_fields or operator not in FILTER_OPERATORS or "value" not in condition:
            raise AnalysisTagConditionError("未対応のフィールド、演算子、または値です。")
        value = condition["value"]
        if operator == "in" and not isinstance(value, list):
            raise AnalysisTagConditionError("inの値は配列で指定してください。")
        if operator == "range" and (not isinstance(value, list) or len(value) != 2):
            raise AnalysisTagConditionError("rangeの値は下限・上限の2要素で指定してください。")
        values = value if isinstance(value, list) else [value]
        if field in NUMERIC_FILTER_FIELDS:
            if operator in {"in", "range"} and not isinstance(value, list):
                raise AnalysisTagConditionError("inとrangeの数値は配列で指定してください。")
            if operator not in {"in", "range"} and isinstance(value, list):
                raise AnalysisTagConditionError("数値比較の値は単一の数値で指定してください。")
            if any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in values):
                raise AnalysisTagConditionError("数値フィールドの値は数値で指定してください。")
        if field in TEXT_FILTER_FIELDS:
            if operator not in {"eq", "neq", "in"}:
                raise AnalysisTagConditionError("文字列フィールドはeq、neq、inで指定してください。")
            if operator == "in" and not isinstance(value, list):
                raise AnalysisTagConditionError("文字列のin条件は配列で指定してください。")
            if operator != "in" and isinstance(value, list):
                raise AnalysisTagConditionError("文字列の等価条件は単一値で指定してください。")
            if any(not isinstance(item, str) for item in values):
                raise AnalysisTagConditionError("文字列フィールドの値は文字列で指定してください。")
        if field == "observed_before_start" and (
            operator not in {"eq", "neq"} or not isinstance(value, bool)
        ):
            raise AnalysisTagConditionError("observed_before_startは真偽値で指定してください。")


def _matches(actual: Any, operator: str, expected: Any) -> bool:
    if operator == "eq": return bool(actual == expected)
    if operator == "neq": return bool(actual != expected)
    if operator == "gt": return bool(actual > expected)
    if operator == "gte": return bool(actual >= expected)
    if operator == "lt": return bool(actual < expected)
    if operator == "lte": return bool(actual <= expected)
    if operator == "in": return actual in expected
    if operator == "range": return bool(expected[0] <= actual <= expected[1])
    return False


class AnalysisTag(BaseModel):
    id: int
    rule_key: str
    version: int
    title: str
    source_url: str
    evidence_summary: str
    study_period: str
    population: str
    evidence_quality: str
    conditions: dict[str, Any]
    enabled: bool
    probability_multiplier: float | None
    created_at: str
    supersedes_rule_version_id: int | None


NonBlankReason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class TagStateChange(BaseModel):
    enabled: bool
    reason: NonBlankReason


class TagVersionCreate(BaseModel):
    conditions: dict[str, Any]
    reason: NonBlankReason

    @field_validator("conditions")
    @classmethod
    def conditions_must_not_be_empty(cls, value: dict[str, Any]) -> dict[str, Any]:
        if not value:
            raise ValueError("適用条件を入力してください。")
        return value

class TagAuditEvent(BaseModel):
    id: int
    rule_key: str
    rule_version_id: int
    action: Literal["enabled", "disabled", "version_created"]
    reason: str
    occurred_at: str


class AppliedAnalysisTag(BaseModel):
    rule_key: str
    version: int
    context: dict[str, Any]


def analysis_tag_response(row: Any) -> AnalysisTag:
    return AnalysisTag(
        id=int(row["id"]), rule_key=str(row["rule_key"]), version=int(row["version"]),
        title=str(row["title"]), source_url=str(row["source_url"]),
        evidence_summary=str(row["evidence_summary"]), study_period=str(row["study_period"]),
        population=str(row["population"]), evidence_quality=str(row["evidence_quality"]),
        conditions=json.loads(str(row["conditions_json"])), enabled=bool(row["enabled"]),
        probability_multiplier=row["probability_multiplier"], created_at=str(row["created_at"]),
        supersedes_rule_version_id=row["supersedes_rule_version_id"],
    )


def audit_event_response(row: Any) -> TagAuditEvent:
    return TagAuditEvent(
        id=int(row["id"]), rule_key=str(row["rule_key"]),
        rule_version_id=int(row["rule_version_id"]),
        action=cast(Literal["enabled", "disabled", "version_created"], str(row["action"])),
        reason=str(row["reason"]), occurred_at=str(row["occurred_at"]),
    )


def applied_tag_response(row: Any) -> AppliedAnalysisTag:
    return AppliedAnalysisTag(
        rule_key=str(row["rule_key"]), version=int(row["version"]),
        context=json.loads(str(row["context_json"])),
    )


def build_tag_context(
    rule_key: str,
    conditions: dict[str, Any],
    race: dict[str, Any],
    runners: list[dict[str, Any]],
    odds: list[dict[str, Any]],
    current_snapshot: dict[str, Any],
    previous_odds: list[dict[str, Any]] | None,
    previous_snapshot_id: int | None,
) -> dict[str, Any] | None:
    validate_tag_conditions(rule_key, conditions)
    snapshot_count = 1 if previous_snapshot_id is None else 2
    observed_before_start = str(current_snapshot["observed_at"]) < str(race["start_utc"])
    global_facts = {**race, "snapshot_count": snapshot_count, "observed_before_start": observed_before_start}
    filters = conditions["filters"]
    global_filters = [item for item in filters if item["field"] not in RUNNER_FILTER_FIELDS]
    if not all(_matches(global_facts[item["field"]], item["operator"], item["value"]) for item in global_filters):
        return None
    odds_by_number = {int(item["horse_number"]): item for item in odds}
    combined_runners = [{**item, **odds_by_number.get(int(item["horse_number"]), {})} for item in runners]
    runner_filters = [item for item in filters if item["field"] in RUNNER_FILTER_FIELDS]
    matched_numbers = {
        int(item["horse_number"]) for item in combined_runners
        if all(_matches(item[condition["field"]], condition["operator"], condition["value"])
               for condition in runner_filters)
    }
    if runner_filters and not matched_numbers:
        return None
    matched_runners = [item for item in runners if int(item["horse_number"]) in matched_numbers]
    matched_odds = [item for item in odds if int(item["horse_number"]) in matched_numbers]
    if rule_key == "market_odds_level":
        total = sum(1 / float(item["win_odds"]) for item in odds)
        return {"runners": [{
            "horse_number": item["horse_number"], "win_odds": item["win_odds"],
            "market_share": (1 / float(item["win_odds"])) / total,
        } for item in matched_odds]}
    if rule_key == "odds_movement_snapshot":
        if previous_odds is None or previous_snapshot_id is None:
            return None
        previous_by_number = {int(item["horse_number"]): float(item["win_odds"]) for item in previous_odds}
        movements = []
        for item in matched_odds:
            horse_number = int(item["horse_number"])
            if horse_number not in previous_by_number:
                return None
            old = previous_by_number[horse_number]
            new = float(item["win_odds"])
            movements.append({"horse_number": horse_number, "change_rate": (new - old) / old})
        start = datetime.fromisoformat(str(race["start_utc"]).replace("Z", "+00:00"))
        observed = datetime.fromisoformat(str(current_snapshot["observed_at"]).replace("Z", "+00:00"))
        return {"previous_snapshot_id": previous_snapshot_id,
                "seconds_until_start": (start - observed).total_seconds(), "movements": movements}
    if rule_key == "relative_draw_context":
        field_size = int(race["field_size"])
        return {"racecourse": race["racecourse"], "surface": race["surface"],
                "distance_m": race["distance_m"], "field_size": field_size,
                "runners": [{"horse_number": item["horse_number"], "gate": item["gate"],
                             "relative_horse_number": (int(item["horse_number"]) - 1) / max(field_size - 1, 1)}
                            for item in matched_runners]}
    if rule_key == "surface_going_course_distance":
        return {key: race[key] for key in ("racecourse", "surface", "distance_m", "going")}
    if rule_key == "age_sex_context":
        return {"racecourse": race["racecourse"], "surface": race["surface"], "distance_m": race["distance_m"],
                "runners": [{"horse_number": item["horse_number"], "age": item["age"], "sex": item["sex"]}
                            for item in matched_runners]}
    if rule_key == "assigned_weight_context":
        weights = sorted(float(item["assigned_weight"]) for item in runners)
        middle = len(weights) // 2
        median = weights[middle] if len(weights) % 2 else (weights[middle - 1] + weights[middle]) / 2
        return {"race_median": median, "runners": [{
            "horse_number": item["horse_number"], "assigned_weight": item["assigned_weight"],
            "difference_from_median": float(item["assigned_weight"]) - median,
        } for item in matched_runners]}
    if rule_key == "field_size_place_rule":
        field_size = int(race["field_size"])
        return {"field_size": field_size, "place_hits": 0 if field_size <= 4 else 2 if field_size <= 7 else 3}
    return None
