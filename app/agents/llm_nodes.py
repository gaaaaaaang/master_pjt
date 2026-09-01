from __future__ import annotations

from dataclasses import asdict, replace
from typing import Any

from app.agents.llm import AzureAgentClient
from app.agents.planner import ExecutionStep, PlannerDecision
from app.agents.prompts import PLANNER_SYSTEM_PROMPT, SUPERVISOR_SYSTEM_PROMPT
from app.sub_agent.reflection import verify_response
from app.sub_agent.text2sql import QuerySlot

AGENTS = {"text2sql", "rag", "impact", "case_search", "visualization"}
QUERY_TYPES = {
    "status", "master_data_lookup", "release_plan_lookup", "diagnosis",
    "impact", "trend", "unsupported",
}

PLANNER_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "status": {"type": "string", "enum": ["ready", "needs_clarification", "data_unavailable", "unsupported"]},
        "query_type": {"type": "string", "enum": sorted(QUERY_TYPES)},
        "intent": {"type": "string"},
        "fab_id": {"type": ["string", "null"]},
        "missing_slots": {"type": "array", "items": {"type": "string"}},
        "selected_sub_agents": {"type": "array", "items": {"type": "string", "enum": sorted(AGENTS)}},
        "execution_steps": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False,
                "properties": {
                    "agent": {"type": "string", "enum": sorted(AGENTS)},
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

SUPERVISOR_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "proceed": {"type": "boolean"},
        "status": {"type": "string", "enum": ["ready", "needs_clarification", "data_unavailable", "unsupported"]},
        "selected_sub_agents": {"type": "array", "items": {"type": "string", "enum": sorted(AGENTS)}},
        "reason": {"type": "string"},
        "answer": {"type": ["string", "null"]},
        "limitations": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["proceed", "status", "selected_sub_agents", "reason", "answer", "limitations"],
}

REFLECTION_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "is_supported": {"type": "boolean"},
        "warnings": {"type": "array", "items": {"type": "string"}},
        "composer_instructions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["is_supported", "warnings", "composer_instructions"],
}

COMPOSER_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
}


def create_llm_plan(message: str, *, fab: str | None = None) -> PlannerDecision:
    output = AzureAgentClient().complete_json(
        system_prompt=PLANNER_SYSTEM_PROMPT,
        input_data={"question": message, "request_fab": fab},
        output_schema=PLANNER_SCHEMA,
        schema_name="fab_planner_decision",
    )
    fab_id = output.get("fab_id") or fab
    slots = {}
    if fab_id:
        slots["fab_id"] = QuerySlot(str(fab_id).lower(), "llm_inference", 0.9, str(fab_id))
    steps = [ExecutionStep(**step) for step in output["execution_steps"]]
    return PlannerDecision(
        status=output["status"], query_type=output["query_type"], intent=output["intent"],
        selected_sub_agents=list(output["selected_sub_agents"]), execution_steps=steps,
        slots=slots, missing_slots=list(output["missing_slots"]),
        clarification_question=output["clarification_question"],
        limitations=list(output["limitations"]),
    )


def review_plan(plan: PlannerDecision, question: str) -> tuple[PlannerDecision, dict[str, Any]]:
    output = AzureAgentClient().complete_json(
        system_prompt=SUPERVISOR_SYSTEM_PROMPT,
        input_data={"question": question, "planner_decision": asdict(plan)},
        output_schema=SUPERVISOR_SCHEMA,
        schema_name="fab_supervisor_decision",
    )
    selected = list(output["selected_sub_agents"])
    selected_set = set(selected)
    reviewed = replace(
        plan, status=output["status"], selected_sub_agents=selected,
        execution_steps=[step for step in plan.execution_steps if step.agent in selected_set],
        limitations=[*plan.limitations, *output["limitations"]],
    )
    return reviewed, output


def reflect_with_llm(
    *, question: str, query_type: str, answer_parts: list[str],
    evidence: list[dict[str, Any]], limitations: list[str],
) -> dict[str, Any]:
    draft = "\n\n".join(dict.fromkeys(answer_parts))
    deterministic = verify_response(
        draft, evidence=evidence, limitations=limitations, query_type=query_type,
    )
    output = AzureAgentClient().complete_json(
        system_prompt=(
            "You are the self-reflection agent for a semiconductor FAB assistant. "
            "Check whether tool evidence supports the answer. Never invent values. General Data "
            "is simulation/model input, not live factory state. Return concise repair instructions."
        ),
        input_data={
            "question": question, "query_type": query_type, "draft_tool_summary": draft,
            "evidence": evidence, "limitations": limitations,
            "deterministic_safety_check": deterministic,
        },
        output_schema=REFLECTION_SCHEMA,
        schema_name="fab_self_reflection",
    )
    output["evidence_count"] = len(evidence)
    output["limitation_count"] = len(limitations)
    output["deterministic_warnings"] = deterministic["warnings"]
    return output


def compose_with_llm(
    *, question: str, plan: PlannerDecision, answer_parts: list[str],
    evidence: list[dict[str, Any]], limitations: list[str], reflection: dict[str, Any],
) -> str:
    output = AzureAgentClient().complete_json(
        system_prompt=(
            "You are the final answer Composer for a semiconductor FAB assistant. Answer in the "
            "user's language using only supplied tool evidence. Include concrete query results when "
            "present, data basis, and material limitations. Follow reflection instructions. Do not "
            "refer to internal evidence objects; present their values directly to the user."
        ),
        input_data={
            "question": question, "plan": asdict(plan), "tool_summaries": answer_parts,
            "evidence": evidence, "limitations": limitations, "reflection": reflection,
        },
        output_schema=COMPOSER_SCHEMA,
        schema_name="fab_final_answer",
    )
    return str(output["answer"]).strip()
