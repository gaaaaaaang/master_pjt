from __future__ import annotations

from dataclasses import asdict
from typing import Any

from app.agents.llm import AzureAgentClient
from app.agents.planner import PlannerDecision
from app.agents.prompts import ANSWER_COMPOSER_SYSTEM_PROMPT
from app.rag.grounding import compose_grounded
from app.sub_agent.result_delivery import result_presentation

REFLECTION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
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
    "type": "object",
    "additionalProperties": False,
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
}


def compact_evidence(evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Do not send debug traces or the Planner's own instructions back as evidence."""
    fields = (
        "chunk_id",
        "source_document",
        "page_number",
        "section_title",
        "reliability",
        "knowledge_base",
        "issue_type", "issue_types", "declared_issue_types", "query_issue_intents",
        "matched_issue_types", "issue_aligned", "playbook_ids",
    )
    return [
        {
            **item,
            "metadata": {
                key: item.get("metadata", {})[key]
                for key in fields
                if key in item.get("metadata", {})
            },
        }
        if item.get("source_type") == "rag_chunk"
        else item
        for item in evidence
        if item.get("source_type") != "planner_plan"
    ]


def compact_summaries(parts: list[str]) -> list[str]:
    # Full RAG text is already present once in evidence.
    return list(dict.fromkeys(part for part in parts if part and not part.startswith("RAG(")))


def reflect_with_llm(
    *, question: str, query_type: str, answer_parts: list[str],
    evidence: list[dict[str, Any]], limitations: list[str],
    agent_reflections: list[dict[str, Any]] | None = None,
    supervisor_reviews: list[dict[str, Any]] | None = None,
    conversation_history: list[dict[str, Any]] | None = None,
    request_contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    draft = "\n\n".join(compact_summaries(answer_parts))
    deterministic = evidence_readiness(evidence, request_contract or {})
    output = AzureAgentClient().complete_json(
        system_prompt=(
            "You are the self-reflection agent for a semiconductor FAB assistant. "
            "This is BEFORE answer composition: review evidence readiness, not the prose of a final answer. "
            "draft_tool_summary contains tool logs, such as row counts, not user-facing numeric claims. "
            "Do not report missing answer wording or a tool log's row count as a fabricated measurement. "
            "Keep data limitations in warnings and phrasing/formatting requests in composer_instructions. "
            "Never invent values. General Data "
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
            "evidence": compact_evidence(evidence), "limitations": limitations,
            "agent_reflections": agent_reflections or [],
            "supervisor_reviews": supervisor_reviews or [],
            "conversation_history": conversation_history or [],
            "deterministic_safety_check": deterministic,
        },
        output_schema=REFLECTION_SCHEMA,
        schema_name="fab_self_reflection",
    )
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


def evidence_readiness(evidence, request_contract):
    """Pre-composition coverage is distinct from post-composition claim checks."""
    sources = [item for item in evidence if item.get("source_type") != "planner_plan"]
    coverage = request_contract.get("coverage") or {}
    warnings = []
    if not sources:
        warnings.append("답변에 사용할 도구 근거가 없습니다.")
    for requirement in coverage.get("requirements", []):
        if requirement.get("status") != "satisfied":
            warnings.append(f"근거 확보가 완료되지 않은 요청 항목: {requirement.get('description') or requirement.get('requirement_id')}")
    return {"phase": "before_answer_composition", "is_supported": bool(sources) and not warnings,
            "warnings": warnings, "evidence_count": len(sources), "coverage": coverage}


def uses_document_grounding(plan: PlannerDecision, evidence: list[dict[str, Any]]) -> bool:
    """Use verified document answers when tools produced no other factual evidence.

    Planning a SQL/case call is not proof that operational facts were returned.
    Conversely, a knowledge_lookup label must not discard actual SQL results.
    """
    if plan.query_type != "knowledge_lookup" and "rag" not in plan.selected_sub_agents:
        return False
    for item in evidence:
        kind = item.get("source_type")
        if kind in {"planner_plan", "rag_chunk"}:
            continue
        if kind == "text2sql_plan" and (item.get("metadata") or {}).get("status") in {
            "failed", "data_unavailable", "unsupported", "needs_clarification"
        }:
            continue
        return False
    return True


def compose_with_llm(
    *, question: str, plan: PlannerDecision, answer_parts: list[str],
    evidence: list[dict[str, Any]], limitations: list[str], reflection: dict[str, Any],
    grounding: dict[str, Any] | None = None,
    conversation_history: list[dict[str, Any]] | None = None,
    diagnostics: dict[str, Any] | None = None,
) -> str:
    if uses_document_grounding(plan, evidence):
        result = compose_grounded(question, evidence)
        if diagnostics is not None:
            diagnostics["execution_mode"] = "document_grounded"
        if grounding is not None:
            grounding.update(
                status=result.status,
                validation=result.validation,
                citations=result.citations,
                review=result.review,
                version=result.version,
            )
        return result.answer
    if grounding is not None and "rag" in plan.selected_sub_agents:
        grounding.update(
            status="not_checked",
            validation="not_applied_mixed_evidence",
            scope="mixed_tool_answer",
        )
    output = AzureAgentClient().complete_json(
        system_prompt=ANSWER_COMPOSER_SYSTEM_PROMPT,
        input_data={
            "question": question, "plan": {k: v for k, v in asdict(plan).items() if k not in {"prompt_contract", "prompt_version"}}, "tool_summaries": compact_summaries(answer_parts),
            "result_presentation": result_presentation(evidence),
            "evidence": compact_evidence(evidence), "limitations": limitations, "reflection": reflection,
            "conversation_history": conversation_history or [],
        },
        output_schema=COMPOSER_SCHEMA,
        schema_name="fab_final_answer",
    )
    if diagnostics is not None:
        diagnostics["execution_mode"] = "llm_chat_completions"
    return str(output["answer"]).strip()
