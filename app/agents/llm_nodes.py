from __future__ import annotations

from dataclasses import asdict
from typing import Any

from app.agents.llm import AzureAgentClient
from app.agents.planner import PlannerDecision
from app.sub_agent.reflection import verify_response

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
