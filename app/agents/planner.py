from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from app.agents.prompts import PLANNER_PROMPT_VERSION, PLANNER_SYSTEM_PROMPT
from app.agents.router import QUERY_TYPES, classify_query
from app.sub_agent.text2sql import QuerySlot, QueryType, plan_text2sql

AgentName = Literal["text2sql", "rag", "impact", "case_search", "visualization"]
PlanStatus = Literal["ready", "needs_clarification", "data_unavailable", "unsupported"]


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
    missing_slots: list[str] = field(default_factory=list)
    clarification_question: str | None = None
    limitations: list[str] = field(default_factory=list)
    prompt_version: str = PLANNER_PROMPT_VERSION
    prompt_contract: str = PLANNER_SYSTEM_PROMPT


def create_plan(message: str, *, fab: str | None = None) -> PlannerDecision:
    """Create a deterministic execution plan matching the Planner prompt contract."""
    routed_type = classify_query(message)
    text2sql_plan = plan_text2sql(message, fab=fab)

    if routed_type in {"diagnosis", "impact", "trend"}:
        return _scenario_plan(routed_type, text2sql_plan.plan.slots if text2sql_plan.plan else {})

    query_type = text2sql_plan.query_type
    slots = text2sql_plan.plan.slots if text2sql_plan.plan else {}

    if text2sql_plan.status == "needs_clarification":
        return PlannerDecision(
            status="needs_clarification",
            query_type=query_type,
            intent=_intent_for(query_type),
            selected_sub_agents=[],
            execution_steps=[],
            slots=slots,
            missing_slots=_missing_slots_for(query_type, slots),
            clarification_question=text2sql_plan.answer,
            limitations=text2sql_plan.limitations,
        )

    if text2sql_plan.status == "data_unavailable":
        return PlannerDecision(
            status="data_unavailable",
            query_type=query_type,
            intent="live_current_status_lookup",
            selected_sub_agents=["text2sql"],
            execution_steps=[
                ExecutionStep(
                    agent="text2sql",
                    action="return operational data_unavailable until AutoSched tables are loaded",
                    required=True,
                    reason="live/current status must not fall back to SMT2020 General Data",
                )
            ],
            slots=slots,
            limitations=text2sql_plan.limitations,
        )

    if text2sql_plan.status == "unsupported":
        return PlannerDecision(
            status="unsupported",
            query_type=query_type,
            intent=_intent_for(query_type),
            selected_sub_agents=[],
            execution_steps=[],
            slots=slots,
            limitations=text2sql_plan.limitations,
        )

    return PlannerDecision(
        status="ready",
        query_type=query_type,
        intent=_intent_for(query_type),
        selected_sub_agents=["text2sql"],
        execution_steps=[
            ExecutionStep(
                agent="text2sql",
                action="generate and optionally execute template-based read-only SQL",
                required=True,
                reason="question can be answered from an allowlisted SQL template",
            )
        ],
        slots=slots,
        limitations=text2sql_plan.limitations,
    )


def make_plan(query_type: str) -> list[str]:
    """Backward-compatible helper returning human-readable step labels."""
    plans = {
        "status": ["질의 메타데이터 추출", "Text2SQL 실행", "결과 검증", "답변 작성"],
        "master_data_lookup": ["slot 확인", "General Data Text2SQL 실행", "한계 검증", "답변 작성"],
        "release_plan_lookup": ["release 조건 확인", "Release Text2SQL 실행", "한계 검증", "답변 작성"],
        "diagnosis": ["추이 SQL 실행", "공정 지식 검색", "근거 결합", "self-reflection", "답변 작성"],
        "impact": ["영향 대상 확인", "영향도 계산", "계산 결과 검증", "답변 작성"],
        "trend": ["비교 기간 확인", "시계열 SQL 실행", "차트 데이터 생성", "답변 작성"],
    }
    return plans.get(query_type, [QUERY_TYPES.get(query_type, "질의 분류"), "답변 작성"])


def _scenario_plan(query_type: str, slots: dict[str, QuerySlot]) -> PlannerDecision:
    if query_type == "diagnosis":
        agents: list[AgentName] = ["text2sql", "rag", "case_search"]
        steps = [
            ExecutionStep("text2sql", "collect numeric trend or status evidence", False, "diagnosis needs data evidence when available"),
            ExecutionStep("rag", "retrieve process knowledge and operating guidance", True, "diagnosis needs process knowledge"),
            ExecutionStep("case_search", "find similar historical incidents", False, "similar cases can improve explanation"),
        ]
        return PlannerDecision(
            status="ready",
            query_type="diagnosis",
            intent="root_cause_diagnosis",
            selected_sub_agents=agents,
            execution_steps=steps,
            slots=slots,
            limitations=["RAG and case-search are placeholder paths until Milvus/case storage are connected."],
        )

    if query_type == "impact":
        agents = ["text2sql", "impact"]
        steps = [
            ExecutionStep("text2sql", "collect required baseline and scenario metrics", True, "impact requires numeric inputs"),
            ExecutionStep("impact", "estimate metric delta from available inputs", True, "impact calculation belongs to Impact sub-agent"),
        ]
        return PlannerDecision(
            status="ready",
            query_type="impact",
            intent="impact_estimation",
            selected_sub_agents=agents,
            execution_steps=steps,
            slots=slots,
            limitations=["Impact calculations remain limited until operational metrics are loaded."],
        )

    agents = ["text2sql", "visualization"]
    steps = [
        ExecutionStep("text2sql", "collect trend or comparison rows", True, "trend/comparison requires time-series data"),
        ExecutionStep("visualization", "build chart specification from tabular rows", False, "chart output is useful when rows are available"),
    ]
    return PlannerDecision(
        status="ready",
        query_type="trend",
        intent="trend_comparison",
        selected_sub_agents=agents,
        execution_steps=steps,
        slots=slots,
        limitations=["SC-004 trend templates are not implemented until operational data is loaded."],
    )


def _intent_for(query_type: QueryType | str) -> str:
    intents = {
        "status": "live_current_status_lookup",
        "master_data_lookup": "model_master_lookup",
        "release_plan_lookup": "release_plan_lookup",
        "unsupported": "unsupported_query",
    }
    return intents.get(query_type, str(query_type))


def _missing_slots_for(query_type: str, slots: dict[str, QuerySlot]) -> list[str]:
    missing = []
    if "fab_id" not in slots:
        missing.append("fab_id")
    if query_type == "release_plan_lookup" and not {"product", "route", "release_scenario"} & slots.keys():
        missing.append("product_or_route_or_release_scenario")
    return missing
