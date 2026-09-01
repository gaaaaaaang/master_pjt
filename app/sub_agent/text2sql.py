from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from psycopg import Error as PsycopgError

from app.config import get_settings
from app.db.read_only import ReadOnlyQueryExecutor, SqlValidationError
from app.sub_agent.sql_templates import (
    ALLOWED_FABS,
    SqlTemplate,
    master_route_steps,
    master_toolgroups,
    release_plan_lookup,
)

QueryType = Literal[
    "status",
    "master_data_lookup",
    "release_plan_lookup",
    "unsupported",
]
Text2SQLStatus = Literal[
    "succeeded",
    "needs_clarification",
    "unsupported",
    "data_unavailable",
    "failed",
]


@dataclass(frozen=True)
class QuerySlot:
    value: str
    source: Literal["explicit_user", "request_context", "alias_match", "parser"]
    confidence: float
    raw_text: str


@dataclass(frozen=True)
class QueryPlan:
    query_type: QueryType
    template_id: str | None
    fab_id: str | None = None
    data_source_type: Literal["operational_report", "model_master", "release_plan"] | None = None
    slots: dict[str, QuerySlot] = field(default_factory=dict)
    limitations: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Text2SQLResult:
    status: Text2SQLStatus
    query_type: QueryType
    answer: str
    sql: str | None = None
    rows: list[dict[str, Any]] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    row_count: int = 0
    confidence: float = 0.0
    limitations: list[str] = field(default_factory=list)
    plan: QueryPlan | None = None


FAB_PATTERN = re.compile(r"\b(?:fab|FAB)\s*[-_ ]?(1[0-3])\b|\bfab(1[0-3])\b", re.IGNORECASE)
PRODUCT_PATTERN = re.compile(r"\b(?:product|part|route_product)[-_ ]?([eE]?\d+)\b", re.IGNORECASE)
ROUTE_PATTERN = re.compile(r"\broute[-_ ]?product[-_ ]?([eE]?\d+)\b", re.IGNORECASE)
TOOLGROUP_PATTERN = re.compile(r"\b[A-Z][A-Za-z]{1,8}_[A-Z]{2}_[0-9]{1,3}\b")
LOT_PATTERN = re.compile(r"\b(?:init_)?lot[_-][A-Za-z0-9_-]+\b", re.IGNORECASE)

AREA_ALIASES = {
    "dry etch": "Dry_Etch",
    "dry_etch": "Dry_Etch",
    "드라이에치": "Dry_Etch",
    "wet etch": "Wet_Etch",
    "wet_etch": "Wet_Etch",
    "웻에치": "Wet_Etch",
    "diffusion": "Diffusion",
    "확산": "Diffusion",
    "implant": "Implant",
    "photo": "Photo",
    "litho": "Photo",
    "tf met": "TF_Met",
    "tf_met": "TF_Met",
    "def met": "Def_Met",
    "def_met": "Def_Met",
    "cmp": "CMP",
}

STATUS_TERMS = {
    "wip",
    "현재",
    "상태",
    "가동률",
    "util",
    "utilization",
    "cycle",
    "cycle time",
    "ontime",
    "on-time",
    "queue",
    "큐",
}
ROUTE_TERMS = {"route", "라우트", "공정", "step", "스텝", "단계"}
TOOLGROUP_TERMS = {"toolgroup", "tool group", "툴그룹", "설비군", "area", "영역"}
RELEASE_TERMS = {"release", "lotrelease", "릴리즈", "due", "duedate", "납기"}
PM_BREAKDOWN_TERMS = {"pm", "breakdown", "고장", "장애", "setup", "셋업", "transport", "이송"}


def generate_sql(question: str, schema_context: str | None = None) -> str:
    """Backward-compatible helper returning deterministic SQL only when supported."""
    del schema_context
    result = plan_text2sql(question)
    if not result.sql:
        raise ValueError(result.answer)
    return result.sql


def execute_read_only(sql: str) -> list[dict[str, Any]]:
    """Backward-compatible helper for executing a validated read-only SQL statement."""
    return ReadOnlyQueryExecutor().execute(sql).rows


def answer_question(
    question: str,
    *,
    fab: str | None = None,
    execute: bool | None = None,
) -> Text2SQLResult:
    result = plan_text2sql(question, fab=fab)
    if result.status != "succeeded" or not result.sql:
        return result

    settings = get_settings()
    should_execute = execute if execute is not None else bool(settings.postgres_dsn)
    if not should_execute:
        return result

    try:
        execution = ReadOnlyQueryExecutor().execute(result.sql)
    except (RuntimeError, SqlValidationError, PsycopgError) as exc:
        return Text2SQLResult(
            status="failed",
            query_type=result.query_type,
            answer="SQL은 생성됐지만 데이터베이스 조회를 완료하지 못했습니다.",
            sql=result.sql,
            confidence=0.2,
            limitations=[*result.limitations, str(exc)],
            plan=result.plan,
        )

    return Text2SQLResult(
        status="succeeded",
        query_type=result.query_type,
        answer=_summarize_execution(result, execution.rows),
        sql=result.sql,
        rows=execution.rows,
        columns=execution.columns,
        row_count=execution.row_count,
        confidence=result.confidence,
        limitations=result.limitations,
        plan=result.plan,
    )


def plan_text2sql(question: str, *, fab: str | None = None) -> Text2SQLResult:
    normalized = _normalize_question(question)
    slots = _extract_slots(question, normalized, fab=fab)
    fab_id = slots.get("fab_id").value if "fab_id" in slots else None
    query_type = _classify_query_type(normalized)

    if not fab_id:
        return _clarification(
            query_type=query_type,
            answer="어느 FAB을 조회할까요? 현재 조회 가능한 대상은 fab10, fab11, fab12, fab13입니다.",
            slots=slots,
        )

    if query_type == "status":
        return _operational_data_unavailable(fab_id, slots)

    if query_type == "release_plan_lookup":
        if not _has_selective_release_constraint(slots):
            return _clarification(
                query_type=query_type,
                answer="release plan은 범위가 넓습니다. product, route, release scenario 중 하나를 지정해주세요.",
                fab_id=fab_id,
                data_source_type="release_plan",
                slots=slots,
            )
        template = release_plan_lookup(
            fab_id,
            product=_slot_value(slots, "product"),
            route=_slot_value(slots, "route"),
            release_scenario=_slot_value(slots, "release_scenario"),
        )
        return _template_result(template, query_type, "release_plan", slots, fab_id)

    if query_type == "master_data_lookup":
        try:
            template = _select_master_template(normalized, slots, fab_id)
        except ValueError as exc:
            return Text2SQLResult(
                status="unsupported",
                query_type=query_type,
                answer="요청한 route/product 조합은 현재 적재된 General Data에서 찾을 수 없습니다.",
                confidence=0.6,
                limitations=[str(exc)],
                plan=QueryPlan(
                    query_type=query_type,
                    template_id=None,
                    fab_id=fab_id,
                    data_source_type="model_master",
                    slots=slots,
                ),
            )
        if template is None:
            return Text2SQLResult(
                status="unsupported",
                query_type=query_type,
                answer="현재 초기 Text2SQL은 toolgroup, route step, release plan 조회만 지원합니다.",
                confidence=0.4,
                limitations=["PM, breakdown, setup, transport 조회 템플릿은 다음 단계에서 추가합니다."],
                plan=QueryPlan(
                    query_type=query_type,
                    template_id=None,
                    fab_id=fab_id,
                    data_source_type="model_master",
                    slots=slots,
                ),
            )
        return _template_result(template, query_type, "model_master", slots, fab_id)

    return Text2SQLResult(
        status="unsupported",
        query_type="unsupported",
        answer="현재 초기 Text2SQL 범위 밖의 질문입니다.",
        confidence=0.2,
        limitations=["지원 범위: toolgroup, route step, release plan, operational status coverage 확인"],
        plan=QueryPlan(query_type="unsupported", template_id=None, fab_id=fab_id, slots=slots),
    )


def _select_master_template(
    normalized_question: str,
    slots: dict[str, QuerySlot],
    fab_id: str,
) -> SqlTemplate | None:
    if _contains_any(normalized_question, ROUTE_TERMS):
        product = _slot_value(slots, "product") or _slot_value(slots, "route")
        if product:
            return master_route_steps(
                fab_id,
                product=product,
                area=_slot_value(slots, "area"),
                toolgroup=_slot_value(slots, "toolgroup"),
            )
    if _contains_any(normalized_question, TOOLGROUP_TERMS) or "area" in slots or "toolgroup" in slots:
        return master_toolgroups(
            fab_id,
            area=_slot_value(slots, "area"),
            toolgroup=_slot_value(slots, "toolgroup"),
        )
    return None


def _template_result(
    template: SqlTemplate,
    query_type: QueryType,
    data_source_type: Literal["model_master", "release_plan"],
    slots: dict[str, QuerySlot],
    fab_id: str,
) -> Text2SQLResult:
    limitations = [
        "현재 결과는 SMT2020 General Data 기반 simulation/model input 기준입니다.",
        "live/current factory state로 해석하면 안 됩니다.",
    ]
    plan = QueryPlan(
        query_type=query_type,
        template_id=template.name,
        fab_id=fab_id,
        data_source_type=data_source_type,
        slots=slots,
        limitations=limitations,
    )
    return Text2SQLResult(
        status="succeeded",
        query_type=query_type,
        answer=f"{template.description} SQL을 생성했습니다.",
        sql=template.sql,
        confidence=0.75,
        limitations=limitations,
        plan=plan,
    )


def _operational_data_unavailable(
    fab_id: str,
    slots: dict[str, QuerySlot],
) -> Text2SQLResult:
    limitations = [
        "현재 PostgreSQL에는 AutoSched report table(autosched_*)이 적재되어 있지 않습니다.",
        "General Data는 model input/master data라서 WIP, 설비 현재 상태, utilization을 추정하지 않습니다.",
    ]
    return Text2SQLResult(
        status="data_unavailable",
        query_type="status",
        answer=(
            f"{fab_id}의 live/current operational status는 아직 조회할 수 없습니다. "
            "AutoSched report 적재 후 활성화해야 합니다."
        ),
        confidence=0.8,
        limitations=limitations,
        plan=QueryPlan(
            query_type="status",
            template_id=None,
            fab_id=fab_id,
            data_source_type="operational_report",
            slots=slots,
            limitations=limitations,
        ),
    )


def _clarification(
    *,
    query_type: QueryType,
    answer: str,
    slots: dict[str, QuerySlot],
    fab_id: str | None = None,
    data_source_type: Literal["operational_report", "model_master", "release_plan"] | None = None,
) -> Text2SQLResult:
    return Text2SQLResult(
        status="needs_clarification",
        query_type=query_type,
        answer=answer,
        confidence=0.5,
        limitations=["필수 slot이 부족해서 SQL을 생성하지 않았습니다."],
        plan=QueryPlan(
            query_type=query_type,
            template_id=None,
            fab_id=fab_id,
            data_source_type=data_source_type,
            slots=slots,
        ),
    )


def _summarize_execution(planned: Text2SQLResult, rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "조회는 성공했지만 조건에 맞는 행이 없습니다."
    if planned.query_type == "master_data_lookup":
        return f"General Data 기준으로 {len(rows)}개 행을 조회했습니다."
    if planned.query_type == "release_plan_lookup":
        return f"Release plan 기준으로 {len(rows)}개 행을 조회했습니다."
    return f"{len(rows)}개 행을 조회했습니다."


def _extract_slots(
    question: str,
    normalized_question: str,
    *,
    fab: str | None,
) -> dict[str, QuerySlot]:
    slots: dict[str, QuerySlot] = {}

    context_fab = _normalize_fab(fab) if fab else None
    if context_fab:
        slots["fab_id"] = QuerySlot(context_fab, "request_context", 0.9, fab or context_fab)

    parsed_fab = _parse_fab(question)
    if parsed_fab:
        slots["fab_id"] = QuerySlot(parsed_fab, "explicit_user", 1.0, parsed_fab)

    product = _parse_product(question)
    if product:
        slots["product"] = QuerySlot(product, "parser", 0.9, product)
        slots["route"] = QuerySlot(f"Route_{product}", "parser", 0.8, product)

    route = _parse_route(question)
    if route:
        slots["route"] = QuerySlot(route, "parser", 0.9, route)
        slots["product"] = QuerySlot(route.removeprefix("Route_"), "parser", 0.8, route)

    toolgroup = _parse_toolgroup(question)
    if toolgroup:
        slots["toolgroup"] = QuerySlot(toolgroup, "parser", 0.95, toolgroup)

    lot_id = _parse_lot(question)
    if lot_id:
        slots["lot_id"] = QuerySlot(lot_id, "parser", 0.9, lot_id)

    area = _parse_area(normalized_question)
    if area:
        slots["area"] = QuerySlot(area, "alias_match", 0.85, area)

    release_scenario = _parse_release_scenario(question)
    if release_scenario:
        slots["release_scenario"] = QuerySlot(
            release_scenario, "parser", 0.75, release_scenario
        )

    return slots


def _classify_query_type(normalized_question: str) -> QueryType:
    if _contains_any(normalized_question, RELEASE_TERMS):
        return "release_plan_lookup"
    if _contains_any(normalized_question, ROUTE_TERMS | TOOLGROUP_TERMS | PM_BREAKDOWN_TERMS):
        if _contains_any(normalized_question, STATUS_TERMS) and not _looks_like_master_lookup(
            normalized_question
        ):
            return "status"
        return "master_data_lookup"
    if _contains_any(normalized_question, STATUS_TERMS):
        return "status"
    return "unsupported"


def _looks_like_master_lookup(normalized_question: str) -> bool:
    return any(term in normalized_question for term in ("어떤", "목록", "list", "구성", "보여", "조회"))


def _has_selective_release_constraint(slots: dict[str, QuerySlot]) -> bool:
    return any(key in slots for key in ("product", "route", "release_scenario"))


def _slot_value(slots: dict[str, QuerySlot], key: str) -> str | None:
    slot = slots.get(key)
    return slot.value if slot else None


def _normalize_question(question: str) -> str:
    return question.casefold().replace("-", "_")


def _contains_any(value: str, terms: set[str]) -> bool:
    return any(term in value for term in terms)


def _normalize_fab(value: str | None) -> str | None:
    if not value:
        return None
    raw = value.strip().lower().replace(" ", "")
    if raw in ALLOWED_FABS:
        return raw
    if raw in {"10", "11", "12", "13"}:
        return f"fab{raw}"
    return None


def _parse_fab(question: str) -> str | None:
    match = FAB_PATTERN.search(question)
    if not match:
        return None
    number = match.group(1) or match.group(2)
    return f"fab{number}"


def _parse_product(question: str) -> str | None:
    match = PRODUCT_PATTERN.search(question)
    if not match:
        return None
    return f"Product_{match.group(1).lower()}"


def _parse_route(question: str) -> str | None:
    match = ROUTE_PATTERN.search(question)
    if not match:
        return None
    return f"Route_Product_{match.group(1).lower()}"


def _parse_toolgroup(question: str) -> str | None:
    match = TOOLGROUP_PATTERN.search(question)
    return match.group(0) if match else None


def _parse_lot(question: str) -> str | None:
    match = LOT_PATTERN.search(question)
    return match.group(0) if match else None


def _parse_area(normalized_question: str) -> str | None:
    normalized = normalized_question.replace("-", "_")
    for alias, canonical in AREA_ALIASES.items():
        if alias in normalized:
            return canonical
    return None


def _parse_release_scenario(question: str) -> str | None:
    lowered = question.casefold()
    known = [
        "variable due dates",
        "high service level",
        "1000 wspw per product",
        "8 years",
    ]
    for scenario in known:
        if scenario in lowered:
            return scenario
    return None
