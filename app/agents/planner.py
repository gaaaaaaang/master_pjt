from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from app.agents.llm import AzureAgentClient
from app.agents.prompts import PLANNER_PROMPT_VERSION, PLANNER_SYSTEM_PROMPT
from app.sub_agent.text2sql import QuerySlot

AgentName = Literal["text2sql", "rag", "impact", "case_search", "visualization"]
PlanStatus = Literal["ready", "needs_clarification", "data_unavailable", "unsupported"]

AGENT_NAMES = {"text2sql", "rag", "impact", "case_search", "visualization"}
QUERY_TYPES = {
    "status", "master_data_lookup", "release_plan_lookup", "diagnosis",
    "impact", "trend", "unsupported",
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
        "selected_sub_agents", "execution_steps", "clarification_question", "limitations",
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
    missing_slots: list[str] = field(default_factory=list)
    clarification_question: str | None = None
    limitations: list[str] = field(default_factory=list)
    prompt_version: str = PLANNER_PROMPT_VERSION
    prompt_contract: str = PLANNER_SYSTEM_PROMPT


def create_plan(
    message: str,
    *,
    fab: str | None = None,
    llm_client: AzureAgentClient | None = None,
) -> PlannerDecision:
    """Create a structured execution plan with an LLM Chat Completions call."""
    output = (llm_client or AzureAgentClient()).complete_json(
        system_prompt=PLANNER_SYSTEM_PROMPT,
        input_data={"question": message, "request_fab": fab},
        output_schema=PLANNER_OUTPUT_SCHEMA,
        schema_name="fab_planner_decision",
    )
    fab_id = output.get("fab_id") or fab
    slots = {}
    if fab_id:
        slots["fab_id"] = QuerySlot(
            str(fab_id).lower(), "llm_inference", 0.9, str(fab_id)
        )
    return PlannerDecision(
        status=output["status"],
        query_type=output["query_type"],
        intent=output["intent"],
        selected_sub_agents=list(output["selected_sub_agents"]),
        execution_steps=[ExecutionStep(**step) for step in output["execution_steps"]],
        slots=slots,
        missing_slots=list(output["missing_slots"]),
        clarification_question=output["clarification_question"],
        limitations=list(output["limitations"]),
    )
