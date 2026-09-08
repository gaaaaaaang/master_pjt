from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Any, Literal

from app.agents.intent import (
    LLM_SLOT_NAMES,
    AnswerRequirement,
    IntentAnalysis,
    analyze_request,
    build_answer_requirements,
    enrich_analysis,
    intent_payload,
)
from app.agents.llm import AzureAgentClient
from app.agents.prompts import PLANNER_PROMPT_VERSION, PLANNER_SYSTEM_PROMPT
from app.sub_agent.text2sql import QuerySlot

AgentName = Literal["text2sql", "rag", "impact", "case_search", "visualization"]
PlanStatus = Literal["ready", "needs_clarification", "data_unavailable", "unsupported"]

AGENT_NAMES = {"text2sql", "rag", "impact", "case_search", "visualization"}
QUERY_TYPES = {
    "status", "master_data_lookup", "release_plan_lookup", "diagnosis",
    "impact", "trend", "knowledge_lookup", "unsupported",
}
RAG_KNOWLEDGE_BASES = {"incident_playbook", "process_basics"}
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
        "extracted_slots": {
            "type": "array", "items": {
                "type": "object", "additionalProperties": False,
                "properties": {
                    "name": {"type": "string", "enum": sorted(LLM_SLOT_NAMES)},
                    "value": {"type": "string"}, "raw_text": {"type": "string"},
                },
                "required": ["name", "value", "raw_text"],
            },
        },
        "success_criteria": {"type": "array", "items": {"type": "string"}},
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
        "extracted_slots", "success_criteria",
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
    depends_on: list[str] = field(default_factory=list)
    input_requirements: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PlannerDecision:
    status: PlanStatus
    query_type: str
    intent: str
    selected_sub_agents: list[AgentName]
    execution_steps: list[ExecutionStep]
    slots: dict[str, QuerySlot] = field(default_factory=dict)
    intent_analysis: IntentAnalysis | None = None
    answer_requirements: list[AnswerRequirement] = field(default_factory=list)
    success_criteria: list[str] = field(default_factory=list)
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
    analysis = analyze_request(
        message, fab=fab, line=line, process=process, product=product,
        route=route, equipment=equipment, date_basis=date_basis, metric=metric,
    )
    try:
        output = (llm_client or AzureAgentClient()).complete_json(
            system_prompt=PLANNER_SYSTEM_PROMPT,
            input_data={
                "question": message,
                "request_analysis": intent_payload(analysis),
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
        fallback = _create_deterministic_fallback_plan(
            message,
            fab=analysis.slots["fab_id"].value if "fab_id" in analysis.slots else fab,
            line=line,
            process=process,
            product=product,
            route=route,
            equipment=equipment,
            date_basis=date_basis,
            metric=metric,
            reason=str(exc),
        )
        return _ground_plan(fallback, analysis)
    analysis = enrich_analysis(analysis, output.get("extracted_slots", []))
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
    return _ground_plan(PlannerDecision(
        status=output["status"],
        query_type=output["query_type"],
        intent=output["intent"],
        success_criteria=list(output.get("success_criteria", [])),
        selected_sub_agents=selected_agents,
        execution_steps=execution_steps,
        rag_knowledge_base=rag_knowledge_base,
        missing_slots=list(output["missing_slots"]),
        clarification_question=output["clarification_question"],
        limitations=list(output["limitations"]),
    ), analysis)


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
    fab_id = fab or _extract_fab(normalized)
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
    operational_status = (bool(fab_id) or bool(metric) or any(
        term in normalized for term in ("wip", "재공", "가동률", "queue", "cycle", "수율", "처리량")
    )) and (
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
        for term in ("추세", "추이", "비교", "차트", "그래프", "일별", "주별", "월별", "날짜별", "기간별", "흐름", "변화")
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
    needs_fab = query_type not in {"knowledge_lookup", "unsupported"} and not fab_id
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
    match = re.search(r"\bfab[\s_-]*(10|11|12|13)\b", normalized)
    return f"fab{match.group(1)}" if match else None


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
    if query_type == "status" and any(
        term in normalized for term in ("비교", "차트", "그래프", "시각화", "compare", "chart", "plot")
    ):
        extras.append("visualization")
    if query_type == "diagnosis":
        if any(
            term in normalized for term in ("영향", "계산", "impact", "capacity", "output", "생산능력")
        ):
            extras.append("impact")
        if any(
            term in normalized
            for term in ("추세", "비교", "차트", "그래프", "일별", "기간별", "흐름", "trend", "compare", "chart", "plot")
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


def _ground_plan(plan: PlannerDecision, analysis: IntentAnalysis) -> PlannerDecision:
    """Bind the LLM plan to parsed user scope before any agent can execute it."""
    slots = dict(analysis.slots)
    # Do not accept an LLM-invented FAB or let an old request field override the
    # current question. Other parser-owned slots have the same precedence.
    status = "unsupported" if plan.query_type == "unsupported" and plan.status == "ready" else plan.status
    missing = list(plan.missing_slots)
    clarification = plan.clarification_question
    if analysis.missing_slots:
        status = "needs_clarification"
        missing = list(analysis.missing_slots)
        clarification = analysis.clarification_question
    elif plan.query_type not in {"knowledge_lookup", "unsupported"} and "fab_id" not in slots:
        status = "needs_clarification"
        missing = ["fab_id"]
        clarification = "어느 FAB을 조회할까요?"
    elif status == "needs_clarification":
        # A table/column name is the data agent's responsibility, not a user slot.
        missing = [key for key in missing if key not in slots
                   and key not in {"table", "table_name", "column", "column_name", "schema"}]
        if not missing:
            status, clarification = "ready", None
    agents, steps = _normalize_agent_route(
        status=status, query_type=plan.query_type, question=analysis.question,
        selected_sub_agents=plan.selected_sub_agents, execution_steps=plan.execution_steps,
    )
    if status != "ready":
        agents, steps = [], []
    steps = [replace(
        step,
        depends_on=["text2sql"] if step.agent in {"impact", "visualization"} else [],
        input_requirements=(
            ["successful SQL rows with matching scope and units"]
            if step.agent in {"impact", "visualization"}
            else ["request scope", "available upstream evidence and its limitations"]
        ),
    ) for step in steps]
    return replace(
        plan, status=status, slots=slots, intent_analysis=analysis,
        missing_slots=missing, clarification_question=clarification,
        selected_sub_agents=agents, execution_steps=steps,
        answer_requirements=build_answer_requirements(plan.query_type, agents, analysis),
    )
