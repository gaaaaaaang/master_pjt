"""Evidence-bounded synthesis for FAB diagnosis questions."""

from __future__ import annotations

from typing import Any

DIAGNOSTIC_ISSUE_TYPES = {
    "bottleneck",
    "bottleneck_impact",
    "breakdown",
    "dispatch_override",
    "equipment_down",
    "hot_lot",
    "lot_hold",
    "material_shortage",
    "ontime_drop",
    "pm_delay",
    "queue_time",
    "queue_time_risk",
    "spc_alarm",
    "wip",
    "yield",
    "yield_drop",
}
DIAGNOSTIC_ISSUE_ALIASES = {
    "station_down": "equipment_down",
}
SIMULATION_SOURCE_MARKERS = ("simulation", "synthetic", "generated", "mock")
DIAGNOSTIC_OBSERVATION_MARKERS = {
    "bottleneck": ("bottleneck", "queue", "wip", "util"),
    "bottleneck_impact": ("bottleneck", "queue", "wip", "util"),
    "breakdown": ("breakdown", "down", "availability"),
    "dispatch_override": ("dispatch", "override", "priority"),
    "equipment_down": ("down", "availability", "uptime"),
    "hot_lot": ("hot", "priority"),
    "lot_hold": ("hold",),
    "material_shortage": ("material", "shortage"),
    "ontime_drop": ("ontime", "on_time"),
    "pm_delay": ("pm", "maintenance"),
    "queue_time": ("queue",),
    "queue_time_risk": ("queue",),
    "spc_alarm": ("spc", "alarm"),
    "wip": ("wip",),
    "yield": ("yield",),
    "yield_drop": ("yield",),
}
RELIABILITY_WEIGHTS = {
    "verified_analogy": 3.0,
    "verified_historical_analogy": 1.5,
    "guidance_only": 1.0,
    "unverified_reference": 0.5,
    "simulation_only": 0.25,
    "simulation_reference": 0.25,
}


def synthesize_diagnosis(evidence: list[dict[str, Any]]) -> dict[str, Any]:
    """Separate observed data from hypotheses and analogous incidents."""
    sql_items = [item for item in evidence if item.get("source_type") == "text2sql_plan"]
    rag_items = [item for item in evidence if item.get("source_type") == "rag_chunk"]
    case_items = [item for item in evidence if item.get("source_type") == "similar_case"]

    observations = []
    for item in sql_items:
        metadata = item.get("metadata") or {}
        # Text2SQL already bounds evidence. Dropping its tail here hid the most
        # recent days and made a diagnosis depend only on older observations.
        sample_rows = list(metadata.get("sample_rows") or [])
        if metadata.get("status") != "succeeded" or not sample_rows:
            continue
        observations.append(
            {
                "title": item.get("title"),
                "row_count": metadata.get("row_count", 0),
                "columns": list(metadata.get("columns") or []),
                "sample_rows": sample_rows,
                "metric_summaries": metadata.get("metric_summaries", []),
                "sql": metadata.get("sql"),
            }
        )

    raw_candidates = []
    for item in rag_items:
        metadata = item.get("metadata") or {}
        has_query_issue_intent = bool(metadata.get("query_issue_intents"))
        issue_source = (
            metadata.get("matched_issue_types")
            if has_query_issue_intent
            else metadata.get("declared_issue_types")
            or metadata.get("issue_types")
            or metadata.get("issue_type")
        )
        raw_candidates.append(
            {
                "source": "incident_playbook",
                "reliability": _knowledge_reliability(item),
                "title": item.get("title"),
                "content": str(item.get("content") or "")[:700],
                "source_document": metadata.get("source_document"),
                "retrieval_score": _retrieval_score(item),
                "issue_types": _diagnostic_issue_types(issue_source),
                "issue_aligned": metadata.get("issue_aligned") is not False,
            }
        )
    eligible_candidates = [
        candidate
        for candidate in raw_candidates
        if candidate["issue_types"] and candidate["issue_aligned"]
    ]
    candidates = eligible_candidates[:3]
    raw_similar_cases = [
        {
            "case_id": (item.get("metadata") or {}).get("case_id"),
            "case_type": (item.get("metadata") or {}).get("case_type"),
            "reliability": _case_reliability(item),
            "content": str(item.get("content") or "")[:700],
            "source": (item.get("metadata") or {}).get("source"),
            "cause": (item.get("metadata") or {}).get("cause"),
            "verification": (item.get("metadata") or {}).get("verification"),
            "temporal_alignment": (item.get("metadata") or {}).get(
                "temporal_alignment", "unspecified"
            ),
            "retrieval_score": _retrieval_score(item),
            "issue_type": _diagnostic_issue_type(
                (item.get("metadata") or {}).get("issue_type")
            ),
        }
        for item in case_items
    ]
    eligible_similar_cases = [case for case in raw_similar_cases if case["issue_type"]]
    similar_cases = eligible_similar_cases[:3]

    missing = []
    if not observations:
        missing.append("operational_sql_observation")
    if not candidates:
        missing.append("incident_playbook_candidate")
    if not similar_cases:
        missing.append("similar_incident")

    issue_types = sorted(
        {
            issue_type
            for candidate in candidates
            for issue_type in candidate["issue_types"]
            if issue_type != "all"
        }
        | {
            str(case["issue_type"])
            for case in similar_cases
            if case.get("issue_type")
        }
    )
    verified_case_conflicts = _verified_case_conflicts(similar_cases)
    conflicted_issue_types = {
        str(conflict["issue_type"]) for conflict in verified_case_conflicts
    }
    candidate_rankings = _rank_candidates(
        observations,
        candidates,
        similar_cases,
        conflicted_issue_types=conflicted_issue_types,
    )
    ranked_issue_types = [item["issue_type"] for item in candidate_rankings]
    verified_count = sum(
        case["reliability"] == "verified_analogy" for case in similar_cases
    )
    historical_verified_count = sum(
        case["reliability"] == "verified_historical_analogy"
        for case in similar_cases
    )
    simulated_count = sum(
        case["case_type"] == "simulated_reference" for case in similar_cases
    )
    unverified_count = sum(
        case["reliability"] == "unverified_reference" for case in similar_cases
    )
    simulated_knowledge_count = sum(
        candidate["reliability"] == "simulation_reference" for candidate in candidates
    )
    corroboration = (
        "verified_analogy"
        if verified_count
        else "verified_historical_only"
        if historical_verified_count
        else "simulated_reference_only"
        if simulated_count
        else "unverified_reference_only"
        if unverified_count
        else "none"
    )
    consistency = _evidence_consistency(candidate_rankings)
    required_checks = ["time_aligned_metric_change", "actual_event_or_maintenance_log_linkage"]
    if simulated_count or simulated_knowledge_count or unverified_count:
        required_checks.append("verified_incident_match")
    if historical_verified_count:
        required_checks.append("time_aligned_incident_match")
    if verified_case_conflicts:
        required_checks.append("resolve_verified_case_conflict")

    summary_parts = [
        (
            f"관측 데이터 {len(observations)}건, 문서 기반 원인 후보 {len(candidates)}건, "
            f"유사 사례 {len(similar_cases)}건을 구분해 검토했습니다."
        )
    ]
    if candidates or similar_cases:
        summary_parts.append("문서와 사례의 원인은 현재 건의 원인 후보이며 동일 원인으로 확정할 수 없습니다.")
    else:
        summary_parts.append("현재 근거로는 원인 후보를 제시하거나 실제 원인을 확정할 수 없습니다.")
    if missing:
        summary_parts.append("부족한 근거: " + ", ".join(missing))
    if simulated_count:
        summary_parts.append(
            f"유사 사례 {simulated_count}건은 시뮬레이션 참고 사례이며 실제 incident 검증 근거가 아닙니다."
        )
    if unverified_count:
        summary_parts.append(
            f"유사 사례 {unverified_count}건은 출처가 검증 기준을 충족하지 않아 "
            "검증 사례로 사용할 수 없습니다."
        )
    if historical_verified_count:
        summary_parts.append(
            f"검증 사례 {historical_verified_count}건은 질문 기간 밖의 과거 사례이므로 "
            "현재 건의 시간 정합 근거가 아닙니다."
        )
    if verified_case_conflicts:
        summary_parts.append(
            "검증 사례 간 기록된 원인이 달라 현재 건과 시간 정렬된 event log로 구분해야 합니다."
        )
    if simulated_knowledge_count:
        summary_parts.append(
            f"Playbook 근거 {simulated_knowledge_count}건은 시뮬레이션/RAG 참고 문서이며 "
            "실제 FAB SOP가 아닙니다."
        )
    if len(issue_types) > 1:
        summary_parts.append("복수 원인 후보가 있어 시간 정렬된 지표와 이벤트 로그로 구분해야 합니다.")
    if candidate_rankings:
        top = candidate_rankings[0]
        summary_parts.append(
            f"우선 검증 후보는 {top['issue_type']}이며 근거 수준은 {top['support_level']}입니다."
        )
        if consistency in {"partially_aligned", "competing_hypotheses"}:
            summary_parts.append("출처 간 후보가 완전히 일치하지 않아 우선순위는 잠정적입니다.")

    return {
        "source_type": "diagnosis_synthesis",
        "title": "Calibrated diagnosis evidence matrix",
        "content": " ".join(summary_parts),
        "metadata": {
            "conclusion_level": "candidate_only",
            "observations": observations,
            "candidate_causes": candidates,
            "similar_cases": similar_cases,
            "candidate_issue_types": issue_types,
            "ranked_candidate_issue_types": ranked_issue_types,
            "candidate_rankings": candidate_rankings,
            "reliability_assessment": {
                "corroboration_level": corroboration,
                "evidence_consistency": consistency,
                "verified_case_count": verified_count,
                "historical_verified_case_count": historical_verified_count,
                "simulated_case_count": simulated_count,
                "unverified_case_count": unverified_count,
                "verified_case_conflicts": verified_case_conflicts,
                "simulated_knowledge_count": simulated_knowledge_count,
                "can_confirm_root_cause": False,
            },
            "required_verification": required_checks,
            "missing_evidence": missing,
            "excluded_evidence": {
                "rag_without_diagnostic_issue": len(raw_candidates)
                - len(
                    [candidate for candidate in raw_candidates if candidate["issue_types"]]
                ),
                "rag_issue_mismatch": sum(
                    not candidate["issue_aligned"] for candidate in raw_candidates
                ),
                "case_without_diagnostic_issue": len(raw_similar_cases)
                - len(eligible_similar_cases),
            },
            "truncated_evidence": {
                "eligible_rag_after_top_k": max(0, len(eligible_candidates) - 3),
                "eligible_cases_after_top_k": max(0, len(eligible_similar_cases) - 3),
            },
            "source_coverage": {
                "operational_sql": bool(observations),
                "incident_playbook": bool(candidates),
                "similar_cases": bool(similar_cases),
            },
        },
}


def _rank_candidates(
    observations: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    similar_cases: list[dict[str, Any]],
    *,
    conflicted_issue_types: set[str] | None = None,
) -> list[dict[str, Any]]:
    conflicted_issue_types = conflicted_issue_types or set()
    observation_columns = {
        str(column).casefold()
        for observation in observations
        for column in observation.get("columns", [])
    }
    grouped: dict[str, dict[str, Any]] = {}

    def add_support(issue_type: str, source_type: str, item: dict[str, Any]) -> None:
        reliability = str(item["reliability"])
        entry = grouped.setdefault(
            issue_type,
            {
                "issue_type": issue_type,
                "evidence_score": 0.0,
                "source_types": set(),
                "support_count": 0,
                "verified_case_count": 0,
                "playbook_count": 0,
                "operational_alignment": _has_observation_alignment(
                    issue_type, observation_columns
                ),
                "max_retrieval_score": 0.0,
            },
        )
        entry["evidence_score"] += RELIABILITY_WEIGHTS[reliability]
        entry["source_types"].add(source_type)
        entry["support_count"] += 1
        entry["max_retrieval_score"] = max(
            entry["max_retrieval_score"], item["retrieval_score"]
        )
        if reliability == "verified_analogy":
            entry["verified_case_count"] += 1
        if source_type == "incident_playbook":
            entry["playbook_count"] += 1

    for candidate in candidates:
        for issue_type in candidate["issue_types"]:
            add_support(issue_type, "incident_playbook", candidate)
    for case in similar_cases:
        add_support(str(case["issue_type"]), "similar_case", case)

    rankings = []
    for entry in grouped.values():
        source_types = sorted(entry.pop("source_types"))
        if entry["operational_alignment"]:
            entry["evidence_score"] += 0.5
        if len(source_types) > 1:
            entry["evidence_score"] += 1.0
        score = round(entry["evidence_score"], 3)
        rankings.append(
            {
                **entry,
                "evidence_score": score,
                "source_types": source_types,
                "support_level": (
                    "conflicting_candidate"
                    if entry["issue_type"] in conflicted_issue_types
                    else
                    "strong_candidate"
                    if entry["verified_case_count"] and len(source_types) > 1
                    else "moderate_candidate"
                    if entry["verified_case_count"] or entry["operational_alignment"]
                    else "weak_candidate"
                ),
            }
        )
    return sorted(
        rankings,
        key=lambda item: (
            -item["evidence_score"],
            -item["max_retrieval_score"],
            item["issue_type"],
        ),
    )


def _evidence_consistency(rankings: list[dict[str, Any]]) -> str:
    if not rankings:
        return "insufficient"
    if any(item["support_level"] == "conflicting_candidate" for item in rankings):
        return "verified_case_conflict"
    if len(rankings) == 1:
        return "aligned" if len(rankings[0]["source_types"]) > 1 else "single_hypothesis"
    if any(len(item["source_types"]) > 1 for item in rankings):
        return "partially_aligned"
    return "competing_hypotheses"


def _verified_case_conflicts(
    similar_cases: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, list[str]]] = {}
    for case in similar_cases:
        if case.get("reliability") not in {
            "verified_analogy",
            "verified_historical_analogy",
        }:
            continue
        issue_type = str(case.get("issue_type") or "")
        cause = str(case.get("cause") or "").strip()
        case_id = str(case.get("case_id") or "")
        if not issue_type or not cause or not case_id:
            continue
        normalized_cause = " ".join(cause.casefold().split())
        grouped.setdefault(issue_type, {}).setdefault(normalized_cause, []).append(case_id)
    return [
        {
            "issue_type": issue_type,
            "causes": sorted(causes),
            "case_ids": sorted(
                case_id for case_ids in cause_groups.values() for case_id in case_ids
            ),
        }
        for issue_type, cause_groups in sorted(grouped.items())
        if len(cause_groups) > 1
        for causes in [[*cause_groups]]
    ]


def _has_observation_alignment(issue_type: str, columns: set[str]) -> bool:
    return any(
        marker in column
        for marker in DIAGNOSTIC_OBSERVATION_MARKERS.get(issue_type, ())
        for column in columns
    )


def _retrieval_score(item: dict[str, Any]) -> float:
    try:
        return max(0.0, float((item.get("metadata") or {}).get("score") or 0.0))
    except (TypeError, ValueError):
        return 0.0


def _csv_values(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def _diagnostic_issue_types(value: Any) -> list[str]:
    return [
        normalized
        for item in _csv_values(value)
        if (normalized := _diagnostic_issue_type(item))
    ]


def _diagnostic_issue_type(value: Any) -> str | None:
    normalized = str(value or "").strip().casefold()
    normalized = DIAGNOSTIC_ISSUE_ALIASES.get(normalized, normalized)
    return normalized if normalized in DIAGNOSTIC_ISSUE_TYPES else None


def _case_reliability(item: dict[str, Any]) -> str:
    metadata = item.get("metadata") or {}
    case_type = metadata.get("case_type")
    source = str(metadata.get("source") or "").casefold()
    if (
        case_type == "verified"
        and source
        and not any(marker in source for marker in SIMULATION_SOURCE_MARKERS)
        and _has_complete_verification(metadata.get("verification"))
    ):
        return (
            "verified_historical_analogy"
            if metadata.get("temporal_alignment")
            == "historical_outside_requested_period"
            else "verified_analogy"
        )
    if case_type == "simulated_reference":
        return "simulation_only"
    return "unverified_reference"


def _has_complete_verification(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    return bool(
        str(value.get("verified_at") or "").strip()
        and str(value.get("incident_at") or "").strip()
        and str(value.get("verified_by") or "").strip()
        and isinstance(value.get("evidence_refs"), list)
        and value["evidence_refs"]
        and all(str(ref).strip() for ref in value["evidence_refs"])
    )


def _knowledge_reliability(item: dict[str, Any]) -> str:
    content = str(item.get("content") or "").casefold()
    simulation_markers = ("시뮬레이션", "simulation use", "simulation reference", "draft baseline")
    return (
        "simulation_reference"
        if any(marker in content for marker in simulation_markers)
        else "guidance_only"
    )
