"""Grounded request contract shared by planning, dispatch, review, and composition.

This module extracts scope only. It never queries a database or infers observed values.
The SQL slot parser remains the single source for canonical identifiers and dates.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from app.db.fab_catalog import comparison_fabs, mentioned_fabs, resolve_fab
from app.sub_agent.text2sql import QuerySlot, extract_query_slots


@dataclass(frozen=True)
class AnswerRequirement:
    requirement_id: str
    description: str
    agents: list[str]
    acceptance_criteria: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class IntentAnalysis:
    question: str
    slots: dict[str, QuerySlot] = field(default_factory=dict)
    requested_outcomes: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    missing_slots: list[str] = field(default_factory=list)
    clarification_question: str | None = None


def is_procedure_request(question: str) -> bool:
    """Recognize a request for operating instructions, not a measured KPI."""
    normalized = question.casefold()
    return bool(
        re.search(r"점검|대응|조치|복구|check|respond|recover", normalized)
        and re.search(r"순서|절차|방법|어떻게|어떤\s*순서|how\s+to|procedure|steps", normalized)
    )


def analyze_request(question: str, **context: Any) -> IntentAnalysis:
    """Prefer explicit current-turn scope over UI defaults; never guess a FAB."""
    slots = extract_query_slots(
        question,
        **{
            key: context.get(key)
            for key in (
                "fab",
                "process",
                "product",
                "route",
                "equipment",
                "date_basis",
                "metric",
                "conversation_history",
            )
        },
    )
    resolution = resolve_fab(question, context.get("fab"), context.get("conversation_history"))
    slots.pop("fab_id", None)
    if resolution.fab_id:
        slots["fab_id"] = QuerySlot(
            resolution.fab_id, resolution.source,
            1.0 if resolution.source == "explicit_user" else 0.9, resolution.raw_text,
        )
    fabs = comparison_fabs(question, context.get("conversation_history")) or mentioned_fabs(question)
    missing: list[str] = []
    clarification = None
    if fabs:
        slots["fab_id"] = QuerySlot(fabs[0], "explicit_user", 1.0, fabs[0])
        slots["fab_ids"] = QuerySlot(",".join(fabs), "explicit_user", 1.0, ", ".join(fabs))
    if any(fab not in {"fab10", "fab11", "fab12", "fab13"} for fab in fabs):
        missing = ["supported_fab"]
        clarification = "조회 가능한 FAB은 fab10, fab11, fab12, fab13입니다. 대상을 지정해주세요."
    elif "unresolved_area_reference" in slots:
        missing = ["process"]
        clarification = "어느 공정을 뜻하는지 확인이 필요합니다. 공정명을 지정하거나 직전 결과 전체라면 '그 공정들'이라고 말씀해주세요."
    line_match = re.search(r"(?<![A-Za-z0-9])([A-Za-z]+[0-9]*)\s*라인", question)
    if line_match:
        slots["line"] = QuerySlot(line_match.group(1), "explicit_user", 1.0, line_match.group(0))
    elif context.get("line"):
        slots["line"] = QuerySlot(context["line"], "request_context", 0.9, context["line"])
    for key, canonical in (("process", "area"), ("equipment", "toolgroup")):
        if canonical in slots:
            slots[key] = slots[canonical]
        elif context.get(key) and not (canonical == "area" and "areas" in slots):
            slots[key] = QuerySlot(context[key], "request_context", 0.9, context[key])
    normalized = question.casefold()
    outcomes = []
    patterns = {
        "diagnosis": r"원인|이유|왜|진단|병목|root cause|diagnos|why",
        "impact": r"영향|계산|늘면|줄면|증가하면|감소하면|떨어지면|impact|what.if",
        "trend": r"추세|추이|일별|주별|월별|날짜별|기간별|trend|daily|weekly|monthly",
        "comparison": r"비교|차이|대비|compare|comparison|versus|\bvs\b",
        "visualization": r"그래프|차트|시각화|chart|graph|plot",
        "knowledge": r"(?<!공)정의|개념|기초|설명|매뉴얼|절차|what is|explain|definition|sop",
    }
    for outcome, pattern in patterns.items():
        if re.search(pattern, normalized):
            outcomes.append(outcome)
    if is_procedure_request(question) and "knowledge" not in outcomes:
        outcomes.append("knowledge")
    if (
        not missing
        and "release_table" in slots
        and "date_basis" not in slots
        and any(outcome in outcomes for outcome in ("trend", "visualization"))
    ):
        missing = ["date_basis"]
        clarification = (
            "lotrelease 날짜 기준을 start_date(투입일) 또는 due_date(납기일) 중에서 지정해주세요."
        )
    assumptions = []
    if not any(key in slots for key in ("date_start", "relative_period", "periods")):
        assumptions.append(
            "기간 미지정: 현재 상태 조회는 사용 가능한 최신 보고서 기준이며 실시간 측정이 아닙니다."
        )
    return IntentAnalysis(question, slots, outcomes, assumptions, missing, clarification)


def build_answer_requirements(
    query_type: str,
    agents: list[str],
    analysis: IntentAnalysis,
) -> list[AnswerRequirement]:
    scope = ", ".join(
        f"{key}={slot.value}"
        for key, slot in analysis.slots.items()
        if key
        in {
            "fab_id",
            "products",
            "toolgroups",
            "area",
            "metrics",
            "date_start",
            "date_end",
            "date_basis",
        }
    )
    descriptions = {
        "text2sql": "요청 대상·지표·기간에 맞는 DB 관측값과 조회 기준 확보",
        "rag": "질문과 관련된 공정 지식·점검 지침 및 출처 확보",
        "case_search": "대상과 문제 유형이 맞는 유사 사례 및 검증 여부 확인",
        "impact": "입력 변화량·기준값·계산식에 근거한 영향 추정과 한계 설명",
        "visualization": "요청한 비교·추세를 실제 조회 결과로 표시",
    }
    return [
        AnswerRequirement(
            requirement_id=f"{agent}_evidence",
            description=descriptions[agent],
            agents=[agent],
            acceptance_criteria=[
                f"요청 범위 유지: {scope or analysis.question}",
                "근거가 없거나 일부만 있으면 부족한 내용을 명시하고 값을 만들지 않는다.",
                *(
                    ["관측 사실·원인 후보·참고 사례를 구분하고 실제 원인을 단정하지 않는다."]
                    if query_type == "diagnosis"
                    else []
                ),
            ],
        )
        for agent in agents
    ]


def intent_payload(analysis: IntentAnalysis) -> dict[str, Any]:
    return asdict(analysis)


LLM_SLOT_NAMES = {
    "line",
    "process",
    "product",
    "route",
    "equipment",
    "metric",
    "date_basis",
    "change_metric",
    "change_amount",
    "change_unit",
    "change_direction",
    "comparison_target",
}


def enrich_analysis(analysis: IntentAnalysis, extracted: list[dict[str, Any]]) -> IntentAnalysis:
    """Accept semantic aliases only with a verbatim span in the current question.

    Parser facts always win. In particular an LLM cannot invent a factory or a date
    range, or silently bind a scope taken from a previous assistant answer.
    """
    from dataclasses import replace

    slots = dict(analysis.slots)
    for item in extracted[:24]:
        name = item.get("name")
        value = item.get("value")
        raw = item.get("raw_text")
        if (
            name not in LLM_SLOT_NAMES
            or name in slots
            or not isinstance(value, str)
            or not value.strip()
            or len(value) > 160
            or not isinstance(raw, str)
            or not raw.strip()
            or raw.casefold() not in analysis.question.casefold()
        ):
            continue
        # A copied span proves presence, not its semantic type. In particular
        # FAB IDs and process aliases must not acquire an invented line scope.
        question = analysis.question.casefold()
        if name == "line" and not re.search(r"라인(?!\s*차트)|\bline\b(?!\s*chart)", question):
            continue
        if name == "route" and not re.search(r"route|경로|라우트", question):
            continue
        if name == "date_basis" and value.casefold() not in {
            "start_date", "due_date", "report_time", "interval_start", "interval_end", "event_time",
        }:
            continue
        if name == "metric":
            # A quoted span is not a machine-readable metric identifier. Only
            # canonicalize against the existing catalog; an arbitrary LLM label
            # must not override catalog-backed query routing. The complete user
            # question remains available for metadata-driven discovery.
            from app.sub_agent.snapshot_queries import METRICS, SLOT_METRICS
            from app.sub_agent.text2sql import METRIC_ALIASES

            known = set(METRIC_ALIASES.values()) | set(METRICS) | set(SLOT_METRICS)
            proposed = [METRIC_ALIASES.get(part.strip().casefold(), part.strip().casefold())
                        for part in value.split(",")]
            if not proposed or any(part not in known for part in proposed):
                continue
            value = ",".join(dict.fromkeys(proposed))
        if name in {"line", "process", "product", "route", "equipment"} and re.fullmatch(
            r"fab\s*[-_]?\s*\d+", value.strip(), flags=re.IGNORECASE
        ):
            continue
        slots[name] = QuerySlot(value.strip(), "llm_inference", 0.75, raw)
    return replace(analysis, slots=slots)


def resolved_request_context(slots: dict[str, QuerySlot]) -> dict[str, str]:
    """The same grounded context must drive tools and the next conversation turn."""
    mapping = {
        "fab": "fab_id",
        "line": "line",
        "process": "process",
        "product": "product",
        "route": "route",
        "equipment": "equipment",
        "date_basis": "date_basis",
        "metric": "metric",
    }
    return {key: slots[slot].value for key, slot in mapping.items() if slot in slots}
