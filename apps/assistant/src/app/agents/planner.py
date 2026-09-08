from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Any, Literal

from app.agents.llm import AzureAgentClient
from app.agents.prompts import PLANNER_PROMPT_VERSION, PLANNER_SYSTEM_PROMPT
from app.db.fab_catalog import resolve_fab
from app.sub_agent.text2sql import QuerySlot, is_explicit_master_lookup

AgentName = Literal["text2sql", "rag", "impact", "case_search", "visualization"]
PlanStatus = Literal["ready", "needs_clarification", "data_unavailable", "unsupported"]

AGENT_NAMES = {"text2sql", "rag", "impact", "case_search", "visualization"}
QUERY_TYPES = {
    "status", "master_data_lookup", "release_plan_lookup", "diagnosis",
    "impact", "trend", "knowledge_lookup", "unsupported",
}
RAG_KNOWLEDGE_BASES = {"incident_playbook", "process_basics"}
SCHEMA_DISCOVERY_SLOTS = {
    "table", "table_name", "table_names", "source_table", "source_tables", "target_table",
    "physical_table", "schema", "schema_name", "database", "database_name", "column", "column_name",
    "data_source", "data_source_type", "data_layer", "dataset", "table_or_data_layer",
    "data_layer_or_table", "data_layer_or_table_name", "table_name_or_data_layer",
    "테이블", "테이블명", "컬럼", "컬럼명", "스키마", "데이터_계층",
}
REQUIRED_AGENT_ROUTES: dict[str, list[AgentName]] = {
    "status": ["text2sql"],
    "master_data_lookup": ["text2sql"],
    "release_plan_lookup": ["text2sql"],
    "diagnosis": ["text2sql", "rag", "case_search"],
    "impact": ["text2sql", "impact"],
    "trend": ["text2sql", "visualization"],
    "knowledge_lookup": ["rag"],
}
AGENT_EXECUTION_ORDER: list[AgentName] = [
    "text2sql",
    "rag",
    "case_search",
    "impact",
    "visualization",
]
OPTIONAL_COMPOUND_AGENTS: dict[str, set[AgentName]] = {
    "status": {"visualization"},
    "diagnosis": {"impact", "visualization"},
    "impact": {"rag", "case_search", "visualization"},
    "trend": {"rag", "case_search"},
}

PLANNER_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "status": {
            "type": "string",
            "enum": ["ready", "needs_clarification", "data_unavailable", "unsupported"],
        },
        "query_type": {"type": "string", "enum": sorted(QUERY_TYPES)},
        "intent": {"type": "string"},
        "fab_id": {"type": ["string", "null"]},
        "rag_knowledge_base": {
            "type": ["string", "null"],
            "enum": ["incident_playbook", "process_basics", None],
        },
        "missing_slots": {"type": "array", "items": {"type": "string"}},
        "selected_sub_agents": {
            "type": "array",
            "items": {"type": "string", "enum": sorted(AGENT_NAMES)},
        },
        "execution_steps": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "agent": {"type": "string", "enum": sorted(AGENT_NAMES)},
                    "action": {"type": "string"},
                    "required": {"type": "boolean"},
                    "reason": {"type": "string"},
                },
                "required": ["agent", "action", "required", "reason"],
            },
        },
        "clarification_question": {"type": ["string", "null"]},
        "limitations": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "status", "query_type", "intent", "fab_id", "missing_slots",
        "rag_knowledge_base", "selected_sub_agents", "execution_steps",
        "clarification_question", "limitations",
    ],
}


@dataclass(frozen=True)
class ExecutionStep:
    agent: AgentName
    action: str
    required: bool
    reason: str


@dataclass(frozen=True)
class PlannerDecision:
    status: PlanStatus
    query_type: str
    intent: str
    selected_sub_agents: list[AgentName]
    execution_steps: list[ExecutionStep]
    slots: dict[str, QuerySlot] = field(default_factory=dict)
    rag_knowledge_base: str | None = None
    missing_slots: list[str] = field(default_factory=list)
    clarification_question: str | None = None
    limitations: list[str] = field(default_factory=list)
    prompt_version: str = PLANNER_PROMPT_VERSION
    prompt_contract: str = PLANNER_SYSTEM_PROMPT
    execution_mode: str = "llm_chat_completions"


def create_plan(
    message: str,
    *,
    fab: str | None = None,
    line: str | None = None,
    process: str | None = None,
    product: str | None = None,
    route: str | None = None,
    equipment: str | None = None,
    date_basis: str | None = None,
    metric: str | None = None,
    conversation_history: list[dict[str, Any]] | None = None,
    execution_feedback: list[dict[str, Any]] | None = None,
    llm_client: AzureAgentClient | None = None,
) -> PlannerDecision:
    """Create a structured execution plan with an LLM Chat Completions call."""
    try:
        output = (llm_client or AzureAgentClient()).complete_json(
            system_prompt=PLANNER_SYSTEM_PROMPT,
            input_data={
                "question": message,
                "request_fab": fab,
                "request_line": line,
                "request_process": process,
                "request_product": product,
                "request_route": route,
                "request_equipment": equipment,
                "request_date_basis": date_basis,
                "request_metric": metric,
                "conversation_history": conversation_history or [],
                "execution_feedback": execution_feedback or [],
            },
            output_schema=PLANNER_OUTPUT_SCHEMA,
            schema_name="fab_planner_decision",
        )
    except RuntimeError as exc:
        return _create_deterministic_fallback_plan(
            message,
            fab=fab,
            line=line,
            process=process,
            product=product,
            route=route,
            equipment=equipment,
            date_basis=date_basis,
            metric=metric,
            reason=str(exc),
        )
    fab_resolution = resolve_fab(message, fab, conversation_history)
    fab_id = fab_resolution.fab_id
    if is_explicit_master_lookup(message) and output["query_type"] in {"status", "trend"}:
        # A fresh explicit lookup must not inherit the previous metric/chart task.
        output = {**output, "query_type": "master_data_lookup", "intent": message,
                  "selected_sub_agents": ["text2sql"], "execution_steps": []}
    # A language model has no current DB observation at planning time. Historical
    # failures must not stop a fresh attempt after migrations or connection recovery.
    database_route = "text2sql" in REQUIRED_AGENT_ROUTES.get(output["query_type"], [])
    if database_route:
        original_missing = output["missing_slots"]
        remaining = [slot for slot in original_missing
                     if re.sub(r"[\s-]+", "_", slot.strip().casefold()) not in SCHEMA_DISCOVERY_SLOTS
                     and not (slot == "fab_id" and fab_id)]
        if remaining != original_missing:
            output = {**output, "missing_slots": remaining}
            if output["status"] == "needs_clarification" and fab_id and not remaining:
                # Physical storage selection belongs to the metadata-aware agent.
                # Business ambiguities (metric/date/FAB etc.) still require clarification.
                output = {**output, "status": "ready", "clarification_question": None, "limitations": []}
    if (
        output["status"] == "data_unavailable"
        and output["query_type"] in REQUIRED_AGENT_ROUTES
        and "text2sql" in REQUIRED_AGENT_ROUTES[output["query_type"]]
        and fab_id
        and not output["missing_slots"]
    ):
        output = {**output, "status": "ready", "limitations": [],
                  "clarification_question": None}
    slots = {}
    if fab_id:
        slots["fab_id"] = QuerySlot(
            fab_id, fab_resolution.source, 0.9, fab_resolution.raw_text
        )
    if line:
        slots["line"] = QuerySlot(line, "request_context", 0.9, line)
    if process:
        slots["process"] = QuerySlot(process, "request_context", 0.9, process)
    for key, value in (
        ("product", product),
        ("route", route),
        ("equipment", equipment),
        ("date_basis", date_basis),
        ("metric", metric),
    ):
        if value:
            slots[key] = QuerySlot(value, "request_context", 0.9, value)
    rag_knowledge_base = output.get("rag_knowledge_base") or _infer_rag_knowledge_base(
        message,
        output["query_type"],
        output["selected_sub_agents"],
    )
    selected_agents, execution_steps = _normalize_agent_route(
        status=output["status"],
        query_type=output["query_type"],
        question=message,
        selected_sub_agents=list(output["selected_sub_agents"]),
        execution_steps=[ExecutionStep(**step) for step in output["execution_steps"]],
    )
    return PlannerDecision(
        status=output["status"],
        query_type=output["query_type"],
        intent=output["intent"],
        selected_sub_agents=selected_agents,
        execution_steps=execution_steps,
        slots=slots,
        rag_knowledge_base=rag_knowledge_base,
        missing_slots=list(output["missing_slots"]),
        clarification_question=output["clarification_question"],
        limitations=list(output["limitations"]),
    )


def _create_deterministic_fallback_plan(
    message: str,
    *,
    fab: str | None,
    line: str | None,
    process: str | None,
    product: str | None,
    route: str | None,
    equipment: str | None,
    date_basis: str | None,
    metric: str | None,
    reason: str,
) -> PlannerDecision:
    normalized = message.casefold().replace("-", "_")
    fab_id = resolve_fab(message, fab).fab_id
    knowledge = any(
        term in normalized
        for term in ("뭐야", "무엇", "설명", "정의", "기초", "매뉴얼", "대응 절차", "sop")
    )
    has_bare_selection = bool(metric) and bool(
        re.search(
            r"(?:>=|<=|>|<)\s*\d|\d+(?:\.\d+)?\s*(?:%|퍼센트|percent)?\s*"
            r"(?:이상|이하|초과|미만)|(?:상위|하위|top|bottom)\s*\d+",
            normalized,
        )
    )
    operational_status = bool(fab_id) and (
        has_bare_selection
        or any(
            term in normalized
            for term in ("현재", "지금", "상태", "몇", "값", "수치", "current", "status")
        )
    )
    if any(
        term in normalized
        for term in ("왜", "원인", "진단", "유사 사례", "병목", "이유", "까닭")
    ):
        query_type = "diagnosis"
    elif any(
        term in normalized
        for term in (
            "영향",
            "늘면",
            "떨어지면",
            "내려가면",
            "줄어들면",
            "증가하면",
            "감소하면",
            "계산",
        )
    ):
        query_type = "impact"
    elif any(
        term in normalized
        for term in ("추세", "비교", "차트", "그래프", "일별", "기간별", "흐름", "변화")
    ):
        query_type = "trend"
    elif operational_status:
        query_type = "status"
    elif knowledge:
        query_type = "knowledge_lookup"
    elif "lotrelease" in normalized or "release plan" in normalized:
        query_type = "release_plan_lookup"
    elif any(term in normalized for term in ("목록", "toolgroup", "설비군", "route 구성")):
        query_type = "master_data_lookup"
    elif fab_id:
        query_type = "status"
    else:
        query_type = "unsupported"

    ambiguous_release_date = (
        query_type == "trend"
        and ("lotrelease" in normalized or "release" in normalized)
        and any(term in normalized for term in ("날짜", "일별", "추세"))
        and not date_basis
        and "start_date" not in normalized
        and "due_date" not in normalized
    )
    needs_fab = query_type != "knowledge_lookup" and not fab_id
    if ambiguous_release_date:
        status: PlanStatus = "needs_clarification"
        missing_slots = ["date_basis"]
        clarification = "lotrelease 날짜 기준을 start_date 또는 due_date 중에서 지정해주세요."
    elif needs_fab:
        status = "needs_clarification"
        missing_slots = ["fab_id"]
        clarification = "어느 FAB을 조회할까요?"
    elif query_type == "unsupported":
        status = "unsupported"
        missing_slots = []
        clarification = None
    else:
        status = "ready"
        missing_slots = []
        clarification = None

    agents = list(REQUIRED_AGENT_ROUTES.get(query_type, [])) if status == "ready" else []
    if status == "ready":
        agents.extend(_deterministic_compound_agents(normalized, query_type))
        agents = [agent for agent in AGENT_EXECUTION_ORDER if agent in set(agents)]
    steps = [
        ExecutionStep(
            agent=agent,
            action=f"Run {agent} for deterministic fallback {query_type}",
            required=query_type != "diagnosis",
            reason="Planner LLM was unavailable; required route invariant was applied.",
        )
        for agent in agents
    ]
    if query_type == "diagnosis":
        steps = [replace(step, required=False) for step in steps]

    slots = {}
    for key, value in (
        ("fab_id", fab_id),
        ("line", line),
        ("process", process),
        ("product", product),
        ("route", route),
        ("equipment", equipment),
        ("date_basis", date_basis),
        ("metric", metric),
    ):
        if value:
            slots[key] = QuerySlot(str(value), "request_context", 0.8, str(value))

    return PlannerDecision(
        status=status,
        query_type=query_type,
        intent=f"Deterministic fallback plan for: {message}",
        selected_sub_agents=agents,
        execution_steps=steps,
        slots=slots,
        rag_knowledge_base=(
            "process_basics"
            if query_type == "knowledge_lookup"
            else "incident_playbook"
            if query_type == "diagnosis"
            else None
        ),
        missing_slots=missing_slots,
        clarification_question=clarification,
        limitations=[f"Planner LLM unavailable; deterministic routing was used: {reason}"],
        execution_mode="deterministic_fallback",
    )


def _extract_fab(normalized: str) -> str | None:
    return resolve_fab(normalized).fab_id


def _normalize_agent_route(
    *,
    status: str,
    query_type: str,
    question: str,
    selected_sub_agents: list[AgentName],
    execution_steps: list[ExecutionStep],
) -> tuple[list[AgentName], list[ExecutionStep]]:
    if status != "ready" or query_type not in REQUIRED_AGENT_ROUTES:
        return selected_sub_agents, execution_steps

    required_agents = REQUIRED_AGENT_ROUTES[query_type]
    allowed_extras = OPTIONAL_COMPOUND_AGENTS.get(query_type, set())
    requested_extras = set(
        _deterministic_compound_agents(question.casefold().replace("-", "_"), query_type)
    )
    selected = set(required_agents) | (
        (set(selected_sub_agents) | requested_extras) & allowed_extras
    )
    agents = [agent for agent in AGENT_EXECUTION_ORDER if agent in selected]
    existing = {step.agent: step for step in execution_steps}
    normalized_steps = []
    for agent in agents:
        step = existing.get(agent)
        required = query_type != "diagnosis" or agent not in required_agents
        normalized_steps.append(
            ExecutionStep(
                agent=agent,
                action=step.action if step else f"Run {agent} for {query_type}",
                required=required,
                reason=step.reason if step else f"{agent} is required by the {query_type} route.",
            )
        )
    return list(agents), normalized_steps


def _deterministic_compound_agents(
    normalized: str, query_type: str
) -> list[AgentName]:
    extras: list[AgentName] = []
    if query_type == "diagnosis":
        if any(
            term in normalized for term in ("영향", "계산", "capacity", "output", "생산능력")
        ):
            extras.append("impact")
        if any(
            term in normalized
            for term in ("추세", "비교", "차트", "그래프", "일별", "기간별", "흐름")
        ):
            extras.append("visualization")
    elif query_type == "impact" and any(
        term in normalized for term in ("비교", "차트", "그래프", "시각화", "plot")
    ):
        extras.append("visualization")
    return extras


def _infer_rag_knowledge_base(
    message: str,
    query_type: str,
    selected_sub_agents: list[str],
) -> str | None:
    if "rag" not in selected_sub_agents:
        return None
    lowered = message.casefold()
    incident_terms = (
        "queue",
        "wip",
        "breakdown",
        "alarm",
        "down",
        "hold",
        "대응",
        "조치",
        "장애",
        "고장",
        "병목",
        "늘었",
        "증가",
        "왜",
    )
    basics_terms = (
        "뭐야",
        "무엇",
        "설명",
        "기초",
        "개념",
        "photo",
        "cmp",
        "lithography",
        "autosched",
    )
    if query_type == "knowledge_lookup" and any(term in lowered for term in basics_terms):
        return "process_basics"
    if query_type == "diagnosis" or any(term in lowered for term in incident_terms):
        return "incident_playbook"
    return "process_basics"
