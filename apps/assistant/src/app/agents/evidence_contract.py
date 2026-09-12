"""Shared, machine-derived boundaries for answering from heterogeneous evidence.

No question wording, FAB name, metric value, or diagnosis phrase is special-cased.
Tool availability and claim strength are independent: absent cases cannot erase
successful observations, and successful observations cannot establish causation.
"""
from __future__ import annotations

from typing import Any

EVIDENCE_CONTRACT_INSTRUCTION = (
    "answer_evidence_contract is computed from active tool results. Start with available "
    "observations/calculations, even when another evidence type is unavailable. "
    "Follow each claim boundary: a returned sample is not the entire population; "
    "between-group differences do not prove temporal increases; observed associations "
    "do not prove causes. Only list cause hypotheses contained in diagnosis candidates. "
    "When hypothesis status is none_supported, give observed differences and the stated "
    "verification needs without proposing extra causes, including tentative ones. "
    "Document retrieval supports background knowledge, not automatic confirmation of "
    "a factory event. Use supplied numerical summaries and calculated estimates "
    "as the source of values, preserving each metric's unit, scope and baseline; "
    "do not recompute them from rounded values in prose. An estimate is conditional "
    "on its supplied assumptions. "
    "Missing evidence is a specific information gap, not an access-policy restriction. "
    "Never present internal contract keys or data encodings in user-facing prose."
)


def answer_evidence_contract(evidence: list[dict[str, Any]]) -> dict[str, Any]:
    queries, documents, calculations, diagnoses, charts = [], [], [], [], []
    for item in evidence:
        kind, metadata = item.get("source_type"), item.get("metadata") or {}
        if kind == "text2sql_plan":
            rows = metadata.get("sample_rows") or []
            successful = metadata.get("status") == "succeeded"
            queries.append({
                "status": metadata.get("status", "unknown"),
                "observations_available": successful and bool(rows),
                "returned_rows": metadata.get("row_count"),
                "sample_rows": len(rows),
                "sample_is_complete": metadata.get("sample_is_complete") is True,
                "row_limit_reached": metadata.get("limit_reached") is True,
                "scope": (metadata.get("query_plan") or {}).get("slots") or {},
                "fields": metadata.get("columns") or (list(rows[0]) if rows else []),
                "temporal_summaries": metadata.get("metric_summaries") if successful else [],
                "cross_group_summary": metadata.get("fab_comparison") if successful else None,
            })
        elif kind == "rag_chunk":
            documents.append({"source_document": metadata.get("source_document"),
                              "chunk_id": metadata.get("chunk_id"),
                              "reliability": metadata.get("reliability", "unverified"),
                              "knowledge_base": metadata.get("knowledge_base")})
        elif kind == "impact_calculation" and metadata.get("status") == "succeeded":
            calculations.append({"assumptions": metadata.get("assumptions") or [],
                                 "formulae": metadata.get("formulae") or [],
                                 "provenance": metadata.get("provenance"),
                                 "limitations": metadata.get("limitations") or []})
        elif kind == "diagnosis_synthesis":
            candidates = metadata.get("candidate_rankings") or metadata.get("candidate_causes") or []
            diagnoses.append({"status": "candidates_only" if candidates else "none_supported",
                              "candidates": candidates,
                              "reliability_assessment": metadata.get("reliability_assessment") or {},
                              "required_verification": metadata.get("required_verification") or [],
                              "missing_evidence": metadata.get("missing_evidence") or []})
        elif kind == "visualization_spec" and metadata.get("status") == "succeeded":
            charts.append({"chart_type": metadata.get("chart_type"),
                           "series_gaps": metadata.get("series_gaps") or [],
                           "series_coverage": metadata.get("series_coverage") or [],
                           "imputed_points": metadata.get("imputed_points") or []})
    return {"version": 1, "queries": queries, "documents": documents,
            "conditional_calculations": calculations, "diagnosis": diagnoses,
            "charts": charts, "confirmed_root_cause_supported": False,
            "diagnosis_evidence_status": "evaluated" if diagnoses else "not_evaluated"}


def with_evidence_contract(input_data: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """One path for composition, readiness, final review, and agent recovery."""
    evidence = input_data.get("evidence")
    if not isinstance(evidence, list):
        results = (input_data.get("execution_context") or {}).get("active_results")
        if not isinstance(results, dict):
            return input_data, ""
        evidence = [item for result in results.values() for item in result.get("evidence", [])]
    return {**input_data, "answer_evidence_contract": answer_evidence_contract(evidence)}, EVIDENCE_CONTRACT_INSTRUCTION
