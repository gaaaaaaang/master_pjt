"""Deterministic self-reflection checks before final answer composition."""

from __future__ import annotations

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
    return bool(source_types) and source_types <= {"rag", "knowledge", "planner_plan"}


def _mentions_calculation_boundary(answer: str, limitations: list[str]) -> bool:
    text = f"{answer} {' '.join(limitations)}".casefold()
    return any(term in text for term in ("계산", "입력", "기준", "limitation", "한계", "metric"))
