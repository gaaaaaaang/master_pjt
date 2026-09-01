from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from typing import Any, Literal, cast

from app.agents.llm import AzureAgentClient
from app.agents.planner import AGENT_NAMES, PlannerDecision
from app.agents.prompts import SUPERVISOR_PROMPT_VERSION, SUPERVISOR_SYSTEM_PROMPT
from app.schemas.chat import ChatRequest, Evidence

SupervisorStatus = Literal[
    "succeeded",
    "needs_clarification",
    "data_unavailable",
    "unsupported",
    "failed",
    "needs_replan",
]

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
    sql: str | None = None
    chart: dict[str, Any] | None = None
    confidence: float | None = None
    limitations: list[str] = field(default_factory=list)
    plan: PlannerDecision | None = None
    agent_runs: list[AgentRun] = field(default_factory=list)
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


class Supervisor:
    """Run the same LLM LangGraph cycle used by the streaming API."""

    def run(self, request: ChatRequest) -> SupervisorResult:
        from app.agents.graph import build_agent_graph, initial_graph_state

        state = initial_graph_state(request)
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
            sql=state.get("sql"),
            chart=state.get("chart"),
            confidence=state.get("confidence"),
            limitations=state.get("limitations", []),
            plan=plan,
            agent_runs=[AgentRun(**run) for run in state.get("agent_runs", [])],
            reflection=state.get("reflection", {}),
        )
