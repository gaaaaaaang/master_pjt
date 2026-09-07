from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from typing import Any, Literal, cast

from app.agents.llm import AzureAgentClient
from app.agents.planner import AGENT_NAMES, PlannerDecision
from app.agents.prompts import (
    AGENT_RECOVERY_PROMPT_VERSION,
    AGENT_RECOVERY_SYSTEM_PROMPT,
    ANSWER_SUPERVISOR_PROMPT_VERSION,
    ANSWER_SUPERVISOR_SYSTEM_PROMPT,
    SUPERVISOR_PROMPT_VERSION,
    SUPERVISOR_SYSTEM_PROMPT,
)
from app.schemas.chat import ChatRequest, Evidence
from app.sub_agent.reflection import verify_response

SupervisorStatus = Literal[
    "succeeded",
    "needs_clarification",
    "data_unavailable",
    "unsupported",
    "failed",
    "needs_replan",
]
RecoveryAction = Literal["continue", "retry_same_agent", "replan", "alternate_agent"]

SUPERVISOR_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "proceed": {"type": "boolean"},
        "status": {
            "type": "string",
            "enum": ["ready", "needs_clarification", "data_unavailable", "unsupported"],
        },
        "selected_sub_agents": {
            "type": "array",
            "items": {"type": "string", "enum": sorted(AGENT_NAMES)},
        },
        "reason": {"type": "string"},
        "answer": {"type": ["string", "null"]},
        "limitations": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "proceed", "status", "selected_sub_agents", "reason", "answer", "limitations"
    ],
}

AGENT_RECOVERY_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "action": {
            "type": "string",
            "enum": ["continue", "retry_same_agent", "replan", "alternate_agent"],
        },
        "alternate_agent": {
            "type": ["string", "null"],
            "enum": [*sorted(AGENT_NAMES), None],
        },
        "reason": {"type": "string"},
        "planner_feedback": {"type": ["string", "null"]},
        "limitations": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["action", "alternate_agent", "reason", "planner_feedback", "limitations"],
}

ANSWER_SUPERVISOR_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "approved": {"type": "boolean"},
        "issues": {"type": "array", "items": {"type": "string"}},
        "corrected_answer": {"type": ["string", "null"]},
        "reason": {"type": "string"},
    },
    "required": ["approved", "issues", "corrected_answer", "reason"],
}


@dataclass(frozen=True)
class AgentRun:
    agent: str
    status: str
    summary: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SupervisorResult:
    conversation_id: str
    status: SupervisorStatus
    query_type: str
    answer: str
    evidence: list[Evidence] = field(default_factory=list)
    reasoning_state: list[dict[str, Any]] = field(default_factory=list)
    sql: str | None = None
    chart: dict[str, Any] | None = None
    confidence: float | None = None
    limitations: list[str] = field(default_factory=list)
    plan: PlannerDecision | None = None
    agent_runs: list[AgentRun] = field(default_factory=list)
    agent_reflections: list[dict[str, Any]] = field(default_factory=list)
    supervisor_reviews: list[dict[str, Any]] = field(default_factory=list)
    supervisor_decisions: list[dict[str, Any]] = field(default_factory=list)
    retry_counts: dict[str, int] = field(default_factory=dict)
    replan_count: int = 0
    reflection_decisions: list[dict[str, Any]] = field(default_factory=list)
    termination_reason: str | None = None
    reflection: dict[str, Any] = field(default_factory=dict)
    answer_review: dict[str, Any] = field(default_factory=dict)
    prompt_version: str = SUPERVISOR_PROMPT_VERSION
    prompt_contract: str = SUPERVISOR_SYSTEM_PROMPT


def review_plan(
    plan: PlannerDecision,
    question: str,
    *,
    llm_client: AzureAgentClient | None = None,
) -> tuple[PlannerDecision, dict[str, Any]]:
    """Review and authorize a Planner plan with an independent LLM call."""
    try:
        output = (llm_client or AzureAgentClient()).complete_json(
            system_prompt=SUPERVISOR_SYSTEM_PROMPT,
            input_data={"question": question, "planner_decision": asdict(plan)},
            output_schema=SUPERVISOR_OUTPUT_SCHEMA,
            schema_name="fab_supervisor_decision",
        )
    except RuntimeError as exc:
        output = {
            "proceed": plan.status == "ready",
            "status": plan.status,
            "selected_sub_agents": list(plan.selected_sub_agents),
            "reason": f"Supervisor LLM unavailable; deterministic plan contract used: {exc}",
            "answer": None,
            "limitations": ["Supervisor LLM review was unavailable."],
            "fallback_used": True,
        }
    selected = (
        list(plan.selected_sub_agents)
        if output["status"] == "ready" and output["proceed"]
        else list(output["selected_sub_agents"])
    )
    selected_set = set(selected)
    reviewed = replace(
        plan,
        status=output["status"],
        selected_sub_agents=selected,
        execution_steps=[step for step in plan.execution_steps if step.agent in selected_set],
        limitations=[*plan.limitations, *output["limitations"]],
    )
    return reviewed, output


def review_agent_result(
    plan: PlannerDecision,
    reflection: dict[str, Any],
    *,
    retry_count: int,
    retry_budget_remaining: int,
    replan_budget_remaining: int,
    alternate_budget_remaining: int,
    allowed_alternate_agents: list[str],
    llm_client: AzureAgentClient | None = None,
) -> dict[str, Any]:
    """Choose a bounded recovery action for one reflected agent result."""
    try:
        output = (llm_client or AzureAgentClient()).complete_json(
            system_prompt=AGENT_RECOVERY_SYSTEM_PROMPT,
            input_data={
                "planner_decision": asdict(plan),
                "agent_reflection": reflection,
                "retry_count": retry_count,
                "retry_budget_remaining": retry_budget_remaining,
                "replan_budget_remaining": replan_budget_remaining,
                "alternate_budget_remaining": alternate_budget_remaining,
                "allowed_alternate_agents": allowed_alternate_agents,
            },
            output_schema=AGENT_RECOVERY_OUTPUT_SCHEMA,
            schema_name="fab_agent_recovery_decision",
        )
    except RuntimeError as exc:
        output = {
            "action": "continue",
            "alternate_agent": None,
            "reason": f"Recovery LLM unavailable; bounded deterministic continue used: {exc}",
            "planner_feedback": None,
            "limitations": ["Recovery LLM review was unavailable."],
            "fallback_used": True,
        }
    action = str(output["action"])
    status = str(reflection.get("status") or "unknown")
    alternate = output.get("alternate_agent")
    fallback_reason: str | None = None

    if action == "retry_same_agent" and (
        retry_budget_remaining <= 0
        or retry_count >= 1
        or status in {"data_unavailable", "unsupported", "needs_clarification", "skipped"}
    ):
        action = "replan" if replan_budget_remaining > 0 else "continue"
        fallback_reason = "Retry was rejected by the bounded recovery policy."
    if action == "replan" and replan_budget_remaining <= 0:
        action = "continue"
        fallback_reason = "Replan budget is exhausted."
    if action == "alternate_agent" and (
        alternate_budget_remaining <= 0 or alternate not in allowed_alternate_agents
    ):
        action = "replan" if replan_budget_remaining > 0 else "continue"
        alternate = None
        fallback_reason = "Alternate agent was rejected by the compatibility or budget policy."

    return {
        **output,
        "action": action,
        "alternate_agent": alternate if action == "alternate_agent" else None,
        "reason": (
            f"{output['reason']} {fallback_reason}" if fallback_reason else output["reason"]
        ),
        "prompt_version": AGENT_RECOVERY_PROMPT_VERSION,
    }


def review_final_answer(
    *,
    question: str,
    answer: str,
    plan: PlannerDecision,
    evidence: list[dict[str, Any]],
    limitations: list[str],
    llm_client: AzureAgentClient | None = None,
) -> dict[str, Any]:
    """Review the composed answer against the original request and grounded evidence."""
    deterministic = verify_response(
        answer,
        evidence=evidence,
        limitations=limitations,
        query_type=plan.query_type,
        question=question,
    )
    try:
        output = (llm_client or AzureAgentClient()).complete_json(
            system_prompt=ANSWER_SUPERVISOR_SYSTEM_PROMPT,
            input_data={
                "question": question,
                "planner_decision": asdict(plan),
                "final_answer": answer,
                "evidence": evidence,
                "limitations": limitations,
                "deterministic_check": deterministic,
            },
            output_schema=ANSWER_SUPERVISOR_OUTPUT_SCHEMA,
            schema_name="fab_answer_supervisor_decision",
        )
    except RuntimeError as exc:
        output = {
            "approved": deterministic["is_supported"],
            "issues": list(deterministic["warnings"]),
            "corrected_answer": None,
            "reason": f"Answer Supervisor LLM unavailable; deterministic check used: {exc}",
            "fallback_used": True,
        }
    issues = list(dict.fromkeys([*deterministic["warnings"], *output["issues"]]))
    original_approved = bool(output["approved"]) and not issues
    approved = original_approved
    corrected_answer = str(output.get("corrected_answer") or "").strip() or None
    correction_check = None
    correction_applied = False
    if not approved and corrected_answer:
        correction_check = verify_response(
            corrected_answer,
            evidence=evidence,
            limitations=limitations,
            query_type=plan.query_type,
            question=question,
        )
        if correction_check["is_supported"]:
            approved = True
            issues = []
            correction_applied = True

    return {
        **output,
        "approved": approved,
        "original_approved": original_approved,
        "issues": issues,
        "corrected_answer": corrected_answer,
        "correction_applied": correction_applied,
        "deterministic_check": deterministic,
        "correction_check": correction_check,
        "prompt_version": ANSWER_SUPERVISOR_PROMPT_VERSION,
    }


class Supervisor:
    """Run the same LLM LangGraph cycle used by the streaming API."""

    def run(
        self,
        request: ChatRequest,
        *,
        conversation_history: list[dict[str, Any]] | None = None,
    ) -> SupervisorResult:
        from app.agents.graph import build_agent_graph, initial_graph_state

        state = initial_graph_state(request, conversation_history=conversation_history)
        for update in build_agent_graph().stream(state, stream_mode="updates"):
            for patch in update.values():
                state.update(patch)

        plan = state["plan"]
        return SupervisorResult(
            conversation_id=state["conversation_id"],
            status=cast(SupervisorStatus, state.get("status", "failed")),
            query_type=plan.query_type,
            answer=state.get("answer", ""),
            evidence=[Evidence.model_validate(item) for item in state.get("evidence", [])],
            reasoning_state=state.get("reasoning_state", []),
            sql=state.get("sql"),
            chart=state.get("chart"),
            confidence=state.get("confidence"),
            limitations=state.get("limitations", []),
            plan=plan,
            agent_runs=[AgentRun(**run) for run in state.get("agent_runs", [])],
            agent_reflections=state.get("agent_reflections", []),
            supervisor_reviews=state.get("supervisor_reviews", []),
            supervisor_decisions=state.get("supervisor_decisions", []),
            retry_counts=state.get("retry_counts", {}),
            replan_count=state.get("replan_count", 0),
            reflection_decisions=state.get("reflection_decisions", []),
            termination_reason=state.get("termination_reason"),
            reflection=state.get("reflection", {}),
            answer_review=state.get("answer_review", {}),
        )
