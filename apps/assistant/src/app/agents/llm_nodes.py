from __future__ import annotations

from dataclasses import asdict
from typing import Any

from app.agents.llm import AzureAgentClient
from app.agents.planner import PlannerDecision
from app.sub_agent.reflection import required_question_context, verify_response

REFLECTION_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "is_supported": {"type": "boolean"},
        "warnings": {"type": "array", "items": {"type": "string"}},
        "composer_instructions": {"type": "array", "items": {"type": "string"}},
        "action": {
            "type": "string",
            "enum": ["compose", "replan", "retry_target", "human_review"],
        },
        "retry_target": {
            "type": ["string", "null"],
            "enum": ["text2sql", "rag", "impact", "case_search", "visualization", None],
        },
        "reason": {"type": "string"},
    },
    "required": [
        "is_supported",
        "warnings",
        "composer_instructions",
        "action",
        "retry_target",
        "reason",
    ],
}

COMPOSER_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
}


def reflect_with_llm(
    *, question: str, query_type: str, answer_parts: list[str],
    evidence: list[dict[str, Any]], limitations: list[str],
    agent_reflections: list[dict[str, Any]] | None = None,
    supervisor_reviews: list[dict[str, Any]] | None = None,
    conversation_history: list[dict[str, Any]] | None = None,
    request_contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    draft = "\n\n".join(dict.fromkeys(answer_parts))
    deterministic = verify_response(
        draft, evidence=evidence, limitations=limitations, query_type=query_type,
    )
    try:
        output = AzureAgentClient().complete_json(
            system_prompt=(
                "You are the self-reflection agent for a semiconductor FAB assistant. "
                "Check whether tool evidence supports the answer. Never invent values. General Data "
                "is simulation/model input, not live factory state. Return concise repair instructions."
                "RAG-only diagnosis may suggest possible causes but cannot confirm the actual root cause. "
                "Incident playbook evidence must be framed as review guidance, not automatic execution. "
                "Process-basics evidence must stay educational and must not become operational control. "
                "Use supervisor_reviews as agent-level review history and treat only pending reviews "
                "as unresolved findings. Choose compose, bounded replan, bounded retry_target, or "
                "human_review. Never request a retry for unavailable or unsupported data. "
                "Check every requirement in request_contract against the actual evidence. "
                "Only active evidence supports the answer; old execution history is audit context."
            ),
            input_data={
                "question": question, "query_type": query_type, "draft_tool_summary": draft,
                "request_contract": request_contract or {},
                "evidence": evidence, "limitations": limitations,
                "agent_reflections": agent_reflections or [],
                "supervisor_reviews": supervisor_reviews or [],
                "conversation_history": conversation_history or [],
                "deterministic_safety_check": deterministic,
            },
            output_schema=REFLECTION_SCHEMA,
            schema_name="fab_self_reflection",
        )
    except RuntimeError as exc:
        output = {
            "is_supported": deterministic["is_supported"],
            "warnings": list(deterministic["warnings"]),
            "composer_instructions": [
                "Preserve tool evidence and limitations without adding unsupported claims."
            ],
            "action": "compose",
            "retry_target": None,
            "reason": f"Reflection LLM unavailable; deterministic verification used: {exc}",
            "fallback_used": True,
        }
    output["evidence_count"] = len(evidence)
    output["limitation_count"] = len(limitations)
    output["deterministic_warnings"] = deterministic["warnings"]
    output["agent_reflections"] = agent_reflections or []
    reviews = supervisor_reviews or []
    output["supervisor_reviews"] = reviews
    unresolved_reviews = [
        review for review in reviews if review.get("resolution", "pending") == "pending"
    ]
    if unresolved_reviews:
        review_warnings = [
            f"{review['agent_name']} requires supervisor review: {review['reason']}"
            for review in unresolved_reviews
        ]
        output["is_supported"] = False
        output["warnings"] = list(dict.fromkeys([*output["warnings"], *review_warnings]))
        instruction = "Keep unresolved agent findings explicit and do not overstate the result."
        output["composer_instructions"] = list(
            dict.fromkeys([*output["composer_instructions"], instruction])
        )
    return output


def compose_with_llm(
    *, question: str, plan: PlannerDecision, answer_parts: list[str],
    evidence: list[dict[str, Any]], limitations: list[str], reflection: dict[str, Any],
    conversation_history: list[dict[str, Any]] | None = None,
    diagnostics: dict[str, Any] | None = None,
) -> str:
    try:
        output = AzureAgentClient().complete_json(
            system_prompt=(
                "You are the final answer Composer for a semiconductor FAB assistant. Answer in the "
                "user's language using only supplied tool evidence. Address every plan.success_criteria "
                "and plan.answer_requirements item, including an explicit gap when evidence is absent. "
                "Preserve the scope in plan.slots for follow-up questions. Include concrete query results when "
                "present, data basis, and material limitations. Follow reflection instructions. Do not "
                "refer to internal evidence objects; present their values directly to the user. "
                "A chart is already returned separately: summarize its observed values and supplied "
                "trend_summary, but do not invent or print axis ranges or raw chart specifications. "
                "For diagnosis, distinguish observations from hypotheses and explicitly label simulated "
                "reference cases; simulation-only evidence never confirms a root cause."
            ),
            input_data={
                "question": question, "plan": asdict(plan), "tool_summaries": answer_parts,
                "evidence": evidence, "limitations": limitations, "reflection": reflection,
                "conversation_history": conversation_history or [],
            },
            output_schema=COMPOSER_SCHEMA,
            schema_name="fab_final_answer",
        )
        if diagnostics is not None:
            diagnostics["execution_mode"] = "llm_chat_completions"
        return str(output["answer"]).strip()
    except RuntimeError:
        if diagnostics is not None:
            diagnostics["execution_mode"] = "deterministic_fallback"
        sections = list(dict.fromkeys(part.strip() for part in answer_parts if part.strip()))
        scope = required_question_context(question)
        if scope:
            sections.insert(0, "요청 범위: " + ", ".join(scope))
        sql_rows = next(
            (
                item.get("metadata", {}).get("sample_rows", [])
                for item in evidence
                if item.get("source_type") == "text2sql_plan"
                and item.get("metadata", {}).get("status") == "succeeded"
                and item.get("metadata", {}).get("sample_rows")
            ),
            [],
        )
        if sql_rows:
            sections.append(
                "조회값: "
                + "; ".join(
                    ", ".join(f"{key}={value}" for key, value in row.items())
                    for row in sql_rows[:5]
                )
            )
        if limitations:
            sections.append("제한사항: " + " ".join(dict.fromkeys(limitations)))
        return "\n\n".join(sections) or "현재 사용 가능한 근거로 답변을 구성할 수 없습니다."
