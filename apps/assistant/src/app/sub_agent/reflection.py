"""Deterministic self-reflection checks before final answer composition."""

from __future__ import annotations

import re
from typing import Any


def verify_response(
    answer: str,
    evidence: list[dict[str, Any]] | None = None,
    limitations: list[str] | None = None,
    query_type: str | None = None,
) -> dict[str, Any]:
    evidence = evidence or []
    limitations = limitations or []
    warnings: list[str] = []

    if not answer.strip():
        warnings.append("Final answer is empty.")

    if query_type in {"status", "diagnosis", "impact", "trend"} and not evidence:
        warnings.append("Evidence is required for operational answers.")

    if _mentions_general_data(evidence) and _sounds_like_live_state(answer):
        warnings.append("General Data must not be described as live/current factory state.")

    if query_type == "diagnosis" and _has_only_rag_evidence(evidence):
        warnings.append("RAG evidence alone cannot prove the actual root cause.")

    if _has_rag_knowledge_base(evidence, "incident_playbook") and _sounds_like_direct_action(answer):
        warnings.append("Incident playbook answers must be framed as review guidance, not automatic action.")

    if _has_rag_knowledge_base(evidence, "process_basics") and _sounds_like_operational_action(answer):
        warnings.append("Process basics answers must not turn into operational dispatch or equipment action.")

    if _contains_numeric_claim(answer) and not _has_sql_evidence(evidence):
        warnings.append("Numeric operational claims require SQL evidence.")

    if query_type == "impact" and not _mentions_calculation_boundary(answer, limitations):
        warnings.append("Impact answers must include input-data and calculation limitations.")

    if not limitations and query_type in {"status", "diagnosis", "impact", "trend"}:
        warnings.append("Operational answers should expose limitations when data is incomplete.")

    return {
        "is_supported": bool(answer.strip()) and not warnings,
        "warnings": warnings,
        "evidence_count": len(evidence),
        "limitation_count": len(limitations),
    }


def _mentions_general_data(evidence: list[dict[str, Any]]) -> bool:
    return any(
        item.get("metadata", {}).get("data_source_type") == "model_master"
        or "General Data" in str(item.get("content", ""))
        for item in evidence
    )


def _sounds_like_live_state(answer: str) -> bool:
    lowered = answer.casefold()
    live_terms = ("현재 상태는", "현재 wip는", "실시간", "live", "current factory state")
    return any(term in lowered for term in live_terms)


def _has_only_rag_evidence(evidence: list[dict[str, Any]]) -> bool:
    source_types = {str(item.get("source_type", "")) for item in evidence}
    return bool(source_types) and source_types <= {"rag", "rag_chunk", "knowledge", "planner_plan"}


def _has_rag_knowledge_base(evidence: list[dict[str, Any]], knowledge_base: str) -> bool:
    return any(item.get("metadata", {}).get("knowledge_base") == knowledge_base for item in evidence)


def _has_sql_evidence(evidence: list[dict[str, Any]]) -> bool:
    return any(item.get("source_type") == "text2sql_plan" for item in evidence)


def _contains_numeric_claim(answer: str) -> bool:
    return bool(re.search(r"\d+(?:\.\d+)?\s*(?:%|개|건|lot|lots|wip|시간|분)", answer.casefold()))


def _sounds_like_direct_action(answer: str) -> bool:
    lowered = answer.casefold()
    terms = (
        "즉시 실행",
        "자동 실행",
        "바로 hold",
        "바로 release",
        "장비를 정지",
        "stop the tool",
        "execute",
    )
    return any(term in lowered for term in terms)


def _sounds_like_operational_action(answer: str) -> bool:
    lowered = answer.casefold()
    terms = ("dispatch", "hold", "release", "장비 정지", "우선순위 변경", "투입 조정")
    return any(term in lowered for term in terms)


def _mentions_calculation_boundary(answer: str, limitations: list[str]) -> bool:
    text = f"{answer} {' '.join(limitations)}".casefold()
    return any(term in text for term in ("계산", "입력", "기준", "limitation", "한계", "metric"))
