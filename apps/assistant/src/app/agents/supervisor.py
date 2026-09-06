from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from typing import Any, Literal, cast

from app.agents.llm import AzureAgentClient
from app.agents.planner import AGENT_NAMES, PlannerDecision
from app.agents.prompts import (
    AGENT_RECOVERY_PROMPT_VERSION,
    AGENT_RECOVERY_SYSTEM_PROMPT,
    SUPERVISOR_PROMPT_VERSION,
    SUPERVISOR_SYSTEM_PROMPT,
)
from app.schemas.chat import ChatRequest, Evidence

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
    prompt_version: str = SUPERVISOR_PROMPT_VERSION
    prompt_contract: str = SUPERVISOR_SYSTEM_PROMPT


def review_plan(
    plan: PlannerDecision,
    question: str,
    *,
    llm_client: AzureAgentClient | None = None,
) -> tuple[PlannerDecision, dict[str, Any]]:
    """Review and authorize a Planner plan with an independent LLM call."""
    output = (llm_client or AzureAgentClient()).complete_json(
        system_prompt=SUPERVISOR_SYSTEM_PROMPT,
        input_data={"question": question, "planner_decision": asdict(plan)},
        output_schema=SUPERVISOR_OUTPUT_SCHEMA,
        schema_name="fab_supervisor_decision",
    )
    selected = list(output["selected_sub_agents"])
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
        )
