"""Deterministic self-reflection checks before final answer composition."""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Literal

AgentReflectionDecision = Literal["pass", "needs_supervisor_review"]


def reflect_agent_output(
    *,
    agent_name: str,
    agent_intent: str,
    planner_plan: dict[str, Any],
    agent_output: dict[str, Any],
    success_criteria: list[str],
    evidence: list[dict[str, Any]] | None = None,
    limitations: list[str] | None = None,
    required: bool = False,
) -> dict[str, Any]:
    """Evaluate one agent result against its planner intent and evidence contract."""
    evidence = evidence or []
    limitations = limitations or []
    status = str(agent_output.get("status") or "unknown")
    warnings: list[str] = []

    if not agent_intent.strip():
        warnings.append("Planner intent for the agent is missing.")
    if not success_criteria:
        warnings.append("Success criteria for the agent are missing.")
    if not str(agent_output.get("summary") or "").strip():
        warnings.append("Agent output summary is empty.")

    if status != "succeeded":
        requirement = "Required" if required else "Selected"
        warnings.append(f"{requirement} agent finished with status '{status}'.")
    elif agent_name == "text2sql" and not agent_output.get("sql"):
        warnings.append("Text2SQL succeeded without returning SQL.")
    elif agent_name in {"rag", "case_search"} and not evidence:
        warnings.append(f"{agent_name} succeeded without recording retrieved evidence.")
    elif agent_name == "impact":
        calculation = agent_output.get("calculation") or {}
        if not calculation:
            warnings.append("Impact analysis succeeded without a calculation result.")
        elif not calculation.get("inputs") or not calculation.get("formulae"):
            warnings.append("Impact calculation must expose its inputs and formulae.")
        elif not calculation.get("estimates"):
            warnings.append("Impact analysis succeeded without numeric estimates.")
    elif agent_name == "visualization" and not agent_output.get("chart"):
        warnings.append("Visualization succeeded without a chart specification.")

    decision: AgentReflectionDecision = "pass" if not warnings else "needs_supervisor_review"
    reason = (
        "Agent output satisfies the declared success criteria."
        if decision == "pass"
        else " ".join(warnings)
    )
    return {
        "agent_name": agent_name,
        "agent_intent": agent_intent,
        "planner_intent": str(planner_plan.get("intent") or ""),
        "query_type": str(planner_plan.get("query_type") or ""),
        "required": required,
        "status": status,
        "success_criteria": success_criteria,
        "agent_output": agent_output,
        "decision": decision,
        "recommended_action": "continue" if decision == "pass" else "supervisor_review",
        "reason": reason,
        "warnings": warnings,
        "evidence_count": len(evidence),
        "limitation_count": len(limitations),
    }


def verify_response(
    answer: str,
    evidence: list[dict[str, Any]] | None = None,
    limitations: list[str] | None = None,
    query_type: str | None = None,
    question: str | None = None,
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

    if query_type == "diagnosis" and not _has_calibrated_diagnosis(evidence):
        warnings.append("Diagnosis answers require a candidate-only evidence synthesis.")

    if query_type == "diagnosis" and not _mentions_diagnosis_uncertainty(answer):
        warnings.append("Diagnosis answers must label causes as candidates rather than confirmed facts.")

    if (
        query_type == "diagnosis"
        and _diagnosis_has_no_supported_candidates(evidence)
        and not _discloses_no_diagnosis_candidate(answer)
    ):
        warnings.append(
            "Diagnosis answers must disclose when evidence cannot support any cause candidate."
        )

    if query_type == "diagnosis" and _has_simulated_evidence(evidence) and not _discloses_simulation(answer):
        warnings.append("Diagnosis answers must disclose simulated reference sources.")

    if query_type == "diagnosis" and _omits_top_diagnosis_candidate(answer, evidence):
        warnings.append("Diagnosis answers must preserve the highest-ranked cause candidate.")

    missing_verified_case_citations = (
        _missing_verified_case_citations(answer, evidence)
        if query_type == "diagnosis"
        else []
    )
    if missing_verified_case_citations:
        warnings.append(
            "Diagnosis answers must cite verified incident case IDs: "
            + ", ".join(missing_verified_case_citations)
        )

    undisclosed_verified_case_conflict = (
        _has_verified_case_conflict(evidence)
        and not _discloses_verified_case_conflict(answer)
        if query_type == "diagnosis"
        else False
    )
    if undisclosed_verified_case_conflict:
        warnings.append("Diagnosis answers must disclose conflicting verified case causes.")

    undisclosed_historical_case = (
        _has_historical_verified_case(evidence)
        and not _discloses_historical_case_limit(answer)
        if query_type == "diagnosis"
        else False
    )
    if undisclosed_historical_case:
        warnings.append(
            "Diagnosis answers must disclose verified cases outside the requested period."
        )

    if _has_rag_knowledge_base(evidence, "incident_playbook") and _sounds_like_direct_action(answer):
        warnings.append("Incident playbook answers must be framed as review guidance, not automatic action.")

    if _has_rag_knowledge_base(evidence, "process_basics") and _sounds_like_operational_action(answer):
        warnings.append("Process basics answers must not turn into operational dispatch or equipment action.")

    if _contains_numeric_claim(answer) and not _has_sql_evidence(evidence):
        warnings.append("Numeric operational claims require SQL evidence.")

    impact_requested = query_type == "impact" or _question_requests_impact(question or "")
    if impact_requested and not _mentions_calculation_boundary(answer, evidence):
        warnings.append("Impact answers must include input-data and calculation limitations.")

    missing_mixed_impact_dimensions = (
        _missing_mixed_impact_dimension_disclosures(answer, evidence)
        if impact_requested
        else []
    )
    if missing_mixed_impact_dimensions:
        warnings.append(
            "Impact answers must disclose mixed baseline dimensions and require separated "
            "baselines: " + ", ".join(missing_mixed_impact_dimensions)
        )

    if (
        query_type in {"status", "diagnosis", "impact", "trend"}
        and limitations
        and not _mentions_limitation(answer)
    ):
        warnings.append("Material limitations must be visible in the final answer.")

    if (
        not limitations
        and query_type in {"status", "diagnosis", "impact", "trend"}
        and _evidence_is_incomplete(evidence)
    ):
        warnings.append("Operational answers should expose limitations when data is incomplete.")

    missing_identifiers: list[str] = []
    missing_metrics: list[str] = []
    missing_dates: list[str] = []
    missing_facets: list[str] = []
    missing_selection_constraints: list[str] = []
    if question:
        missing_identifiers = _missing_question_identifiers(question, answer)
        missing_metrics = _missing_question_metrics(question, answer)
        missing_dates = _missing_question_dates(question, answer)
        missing_facets = _missing_question_facets(question, answer)
        if query_type == "status":
            missing_selection_constraints = _missing_selection_constraints(
                question, answer
            )
        if missing_identifiers:
            warnings.append(
                "Final answer omits requested identifiers: " + ", ".join(missing_identifiers)
            )
        if missing_metrics:
            warnings.append("Final answer omits requested metrics: " + ", ".join(missing_metrics))
        if missing_dates:
            warnings.append("Final answer omits requested date context: " + ", ".join(missing_dates))
        if missing_facets:
            warnings.append("Final answer omits requested task facets: " + ", ".join(missing_facets))
        if missing_selection_constraints:
            warnings.append(
                "Final answer omits requested selection constraints: "
                + ", ".join(missing_selection_constraints)
            )

    impact_value_present = not _has_impact_estimate(evidence) or _mentions_impact_estimate(
        answer, evidence
    )
    if impact_requested and not impact_value_present:
        warnings.append("Impact answers must include at least one calculated estimate value.")

    missing_impact_estimates = (
        _missing_requested_impact_estimates(question or "", answer, evidence)
        if impact_requested
        else []
    )
    if missing_impact_estimates:
        warnings.append(
            "Compound impact answers must include each supported estimate: "
            + ", ".join(missing_impact_estimates)
        )

    impact_direction_conflicts = (
        _impact_direction_conflicts(answer, evidence) if impact_requested else []
    )
    if impact_direction_conflicts:
        warnings.append(
            "Impact answer direction conflicts with calculated estimates: "
            + ", ".join(impact_direction_conflicts)
        )

    trend_direction_conflicts = (
        _trend_direction_conflicts(answer, evidence) if query_type == "trend" else []
    )
    if trend_direction_conflicts:
        warnings.append(
            "Trend answer direction conflicts with visualization summary: "
            + ", ".join(trend_direction_conflicts)
        )

    missing_trend_series = (
        _missing_requested_trend_series_values(question or "", answer, evidence)
        if query_type == "trend"
        else []
    )
    if missing_trend_series:
        warnings.append(
            "Trend answers must include a grounded value for each requested target and metric: "
            + ", ".join(missing_trend_series)
        )

    missing_comparison_periods = (
        _missing_comparison_period_values(question or "", answer, evidence)
        if query_type == "trend"
        else []
    )
    if missing_comparison_periods:
        warnings.append(
            "Comparison answers must include a grounded value for each period: "
            + ", ".join(missing_comparison_periods)
        )

    undisclosed_series_gaps = (
        _undisclosed_series_gaps(answer, evidence) if query_type == "trend" else []
    )
    if undisclosed_series_gaps:
        warnings.append(
            "Trend answers must disclose missing observations: "
            + ", ".join(undisclosed_series_gaps)
        )

    undisclosed_insufficient_trends = (
        _undisclosed_insufficient_trends(answer, evidence)
        if query_type == "trend"
        else []
    )
    if undisclosed_insufficient_trends:
        warnings.append(
            "Trend answers must not infer change from insufficient observations or coverage: "
            + ", ".join(undisclosed_insufficient_trends)
        )

    undisclosed_imputed_points = (
        _undisclosed_imputed_points(answer, evidence) if query_type == "trend" else []
    )
    if undisclosed_imputed_points:
        warnings.append(
            "Trend answers must disclose zero-filled count buckets: "
            + ", ".join(undisclosed_imputed_points)
        )

    missing_result_metrics: list[str] = []
    missing_result_targets: list[str] = []
    if query_type == "status":
        missing_result_metrics = _missing_requested_metric_values(
            question or "", answer, evidence
        )
        missing_result_targets = _missing_requested_target_values(
            question or "", answer, evidence
        )
    result_value_present = not missing_result_metrics and not missing_result_targets
    if not result_value_present:
        details = [*missing_result_metrics, *missing_result_targets]
        warnings.append(
            "Status answers must include each requested metric value from SQL results: "
            + ", ".join(details)
        )

    if query_type == "status":
        unsupported_numeric_claims = _unsupported_status_numeric_claims(
            question or "", answer, evidence
        )
    elif impact_requested:
        unsupported_numeric_claims = _unsupported_impact_numeric_claims(
            question or "", answer, evidence
        )
    elif query_type == "diagnosis":
        unsupported_numeric_claims = _unsupported_diagnosis_numeric_claims(
            question or "", answer, evidence
        )
    elif query_type == "trend":
        unsupported_numeric_claims = _unsupported_trend_numeric_claims(
            question or "", answer, evidence
        )
    else:
        unsupported_numeric_claims = []
    if unsupported_numeric_claims:
        answer_kind = (
            "Impact"
            if impact_requested
            else "Diagnosis"
            if query_type == "diagnosis"
            else "Trend"
            if query_type == "trend"
            else "Status"
        )
        warnings.append(
            f"{answer_kind} answer contains numeric claims absent from grounded inputs and results: "
            + ", ".join(unsupported_numeric_claims)
        )

    chart_requested = _question_requests_chart(question or "")
    chart_claim_grounded = (
        not chart_requested
        or _has_successful_visualization(evidence)
        or _discloses_chart_unavailable(answer)
    )
    if not chart_claim_grounded:
        warnings.append(
            "Chart answers require successful visualization evidence or an explicit unavailable disclosure."
        )

    quality_dimensions = {
        "question_alignment": (
            not missing_identifiers
            and not missing_metrics
            and not missing_dates
            and not missing_facets
            and not missing_selection_constraints
        ),
        "evidence_grounding": (
            result_value_present
            and not unsupported_numeric_claims
            and not missing_impact_estimates
            and not impact_direction_conflicts
            and not missing_mixed_impact_dimensions
            and not trend_direction_conflicts
            and not missing_trend_series
            and not missing_comparison_periods
            and not missing_verified_case_citations
            and not undisclosed_verified_case_conflict
            and not undisclosed_historical_case
            and not undisclosed_series_gaps
            and not undisclosed_insufficient_trends
            and not undisclosed_imputed_points
            and chart_claim_grounded
            and not any(
            warning
            in {
                "Numeric operational claims require SQL evidence.",
                "Impact answers must include at least one calculated estimate value.",
            }
            for warning in warnings
            )
        ),
        "limitation_visibility": (
            (not limitations or _mentions_limitation(answer))
            and not missing_mixed_impact_dimensions
        ),
        "provenance_calibration": not any(
            warning.startswith("Diagnosis answers") or "root cause" in warning
            for warning in warnings
        ),
    }
    return {
        "is_supported": bool(answer.strip()) and not warnings,
        "warnings": warnings,
        "evidence_count": len(evidence),
        "limitation_count": len(limitations),
        "missing_identifiers": missing_identifiers,
        "missing_metrics": missing_metrics,
        "missing_dates": missing_dates,
        "missing_facets": missing_facets,
        "missing_selection_constraints": missing_selection_constraints,
        "missing_result_metrics": missing_result_metrics,
        "missing_result_targets": missing_result_targets,
        "unsupported_numeric_claims": unsupported_numeric_claims,
        "missing_impact_estimates": missing_impact_estimates,
        "impact_direction_conflicts": impact_direction_conflicts,
        "missing_mixed_impact_dimensions": missing_mixed_impact_dimensions,
        "trend_direction_conflicts": trend_direction_conflicts,
        "missing_trend_series": missing_trend_series,
        "missing_comparison_periods": missing_comparison_periods,
        "missing_verified_case_citations": missing_verified_case_citations,
        "undisclosed_verified_case_conflict": undisclosed_verified_case_conflict,
        "undisclosed_historical_case": undisclosed_historical_case,
        "undisclosed_series_gaps": undisclosed_series_gaps,
        "undisclosed_insufficient_trends": undisclosed_insufficient_trends,
        "undisclosed_imputed_points": undisclosed_imputed_points,
        "chart_claim_grounded": chart_claim_grounded,
        "quality_dimensions": quality_dimensions,
        "quality_score": round(
            sum(quality_dimensions.values()) / len(quality_dimensions), 4
        ),
    }


def required_question_context(question: str) -> list[str]:
    """Return canonical identifiers, metrics, and dates a final answer must preserve."""
    return list(
        dict.fromkeys(
            [
                *_missing_question_identifiers(question, ""),
                *_missing_question_metrics(question, ""),
                *_missing_question_dates(question, ""),
            ]
        )
    )


def _mentions_general_data(evidence: list[dict[str, Any]]) -> bool:
    return any(
        item.get("metadata", {}).get("data_source_type") == "model_master"
        or "General Data" in str(item.get("content", ""))
        for item in evidence
    )


def _sounds_like_live_state(answer: str) -> bool:
    live_terms = re.compile(
        r"현재 상태는|현재 wip는|실시간|\blive\b|current factory state|\breal[- ]time\b"
    )
    negation = re.compile(
        r"아니|아님|아닙|않|없|불가|불일치|다를 수|해석하면 안|추정하면 안|"
        r"\bnot\b|cannot|can't|isn't|aren't|unavailable|may differ"
    )
    # Inspect each claim separately: a later disclaimer must not hide an earlier
    # affirmative live claim, and a negative mention is not itself an assertion.
    for clause in re.split(r"[.!?\n;]+|하지만|그러나|반면|다만", answer.casefold()):
        matches = list(live_terms.finditer(clause))
        for index, match in enumerate(matches):
            prefix = clause[max(0, match.start() - 24):match.start()]
            end = matches[index + 1].start() if index + 1 < len(matches) else len(clause)
            suffix = clause[match.end():end]
            if re.search(r"\b(?:not|non)[- ]+(?:actual\s+)?$", prefix):
                continue
            # live/current factory state is one phrase with two lexical matches.
            if suffix.strip() in {"/", "/current"} and index + 1 < len(matches):
                suffix = clause[match.end():]
            if not negation.search(suffix):
                return True
    return False


def _has_only_rag_evidence(evidence: list[dict[str, Any]]) -> bool:
    source_types = {str(item.get("source_type", "")) for item in evidence}
    return bool(source_types) and source_types <= {"rag", "rag_chunk", "knowledge", "planner_plan"}


def _has_rag_knowledge_base(evidence: list[dict[str, Any]], knowledge_base: str) -> bool:
    return any(item.get("metadata", {}).get("knowledge_base") == knowledge_base for item in evidence)


def _has_calibrated_diagnosis(evidence: list[dict[str, Any]]) -> bool:
    return any(
        item.get("source_type") == "diagnosis_synthesis"
        and item.get("metadata", {}).get("conclusion_level") == "candidate_only"
        for item in evidence
    )


def _mentions_diagnosis_uncertainty(answer: str) -> bool:
    lowered = answer.casefold()
    return any(
        term in lowered
        for term in ("원인 후보", "가능성", "추정", "확정할 수 없", "확정 불가", "확정되지")
    )


def _diagnosis_has_no_supported_candidates(evidence: list[dict[str, Any]]) -> bool:
    syntheses = [
        item
        for item in evidence
        if item.get("source_type") == "diagnosis_synthesis"
        and item.get("metadata", {}).get("conclusion_level") == "candidate_only"
    ]
    return bool(syntheses) and all(
        not item.get("metadata", {}).get("candidate_issue_types")
        and not item.get("metadata", {}).get("candidate_causes")
        and not item.get("metadata", {}).get("similar_cases")
        for item in syntheses
    )


def _discloses_no_diagnosis_candidate(answer: str) -> bool:
    lowered = answer.casefold()
    return any(
        term in lowered
        for term in (
            "원인 후보를 제시할 수 없",
            "원인 후보를 판단할 수 없",
            "원인 후보가 없",
            "원인 후보 없음",
            "원인 근거가 부족",
            "원인 판단 근거가 부족",
            "insufficient evidence for a cause",
            "no supported cause candidate",
        )
    )


def _has_simulated_evidence(evidence: list[dict[str, Any]]) -> bool:
    has_case = any(
        item.get("source_type") == "similar_case"
        and item.get("metadata", {}).get("case_type") == "simulated_reference"
        for item in evidence
    )
    has_knowledge = any(
        item.get("source_type") == "diagnosis_synthesis"
        and item.get("metadata", {})
        .get("reliability_assessment", {})
        .get("simulated_knowledge_count", 0)
        > 0
        for item in evidence
    )
    return has_case or has_knowledge


def _discloses_simulation(answer: str) -> bool:
    lowered = answer.casefold()
    return any(term in lowered for term in ("시뮬레이션", "simulation", "모의 사례", "합성 사례"))


DIAGNOSIS_ANSWER_TERMS = {
    "bottleneck": ("bottleneck", "병목"),
    "bottleneck_impact": ("bottleneck impact", "bottleneck_impact", "병목 영향"),
    "breakdown": ("breakdown", "고장"),
    "dispatch_override": ("dispatch override", "dispatch_override", "디스패치 변경"),
    "equipment_down": ("equipment down", "equipment_down", "설비 다운", "장비 고장"),
    "hot_lot": ("hot lot", "hot_lot", "핫랏"),
    "lot_hold": ("lot hold", "lot_hold", "lot 홀드"),
    "material_shortage": ("material shortage", "material_shortage", "자재 부족"),
    "ontime_drop": ("ontime drop", "ontime_drop", "납기율 하락"),
    "pm_delay": ("pm delay", "pm_delay", "pm 지연"),
    "queue_time": ("queue time", "queue_time", "큐타임", "대기 시간"),
    "queue_time_risk": ("queue time risk", "queue_time_risk", "큐타임 위험"),
    "spc_alarm": ("spc alarm", "spc_alarm", "spc 알람"),
    "wip": ("wip", "재공"),
    "yield": ("yield", "수율"),
    "yield_drop": ("yield drop", "yield_drop", "수율 하락"),
}


def _omits_top_diagnosis_candidate(
    answer: str, evidence: list[dict[str, Any]]
) -> bool:
    syntheses = [
        item
        for item in evidence
        if item.get("source_type") == "diagnosis_synthesis"
        and item.get("metadata", {}).get("conclusion_level") == "candidate_only"
    ]
    if not syntheses:
        return False
    rankings = syntheses[-1].get("metadata", {}).get("candidate_rankings") or []
    if len(rankings) < 2:
        return False
    issue_type = str(rankings[0].get("issue_type") or "").casefold()
    if not issue_type:
        return False
    lowered = answer.casefold()
    terms = DIAGNOSIS_ANSWER_TERMS.get(issue_type, (issue_type,))
    return not any(term in lowered for term in terms)


def _missing_verified_case_citations(
    answer: str,
    evidence: list[dict[str, Any]],
) -> list[str]:
    case_ids = {
        str(case.get("case_id"))
        for item in evidence
        if item.get("source_type") == "diagnosis_synthesis"
        for case in item.get("metadata", {}).get("similar_cases", [])
        if case.get("reliability")
        in {"verified_analogy", "verified_historical_analogy"}
        and case.get("case_id")
    }
    lowered = answer.casefold()
    return sorted(case_id for case_id in case_ids if case_id.casefold() not in lowered)


def _has_verified_case_conflict(evidence: list[dict[str, Any]]) -> bool:
    return any(
        item.get("source_type") == "diagnosis_synthesis"
        and bool(
            item.get("metadata", {})
            .get("reliability_assessment", {})
            .get("verified_case_conflicts")
        )
        for item in evidence
    )


def _discloses_verified_case_conflict(answer: str) -> bool:
    lowered = answer.casefold()
    return any(
        term in lowered
        for term in (
            "서로 다른 원인",
            "원인이 서로 다",
            "사례 간 원인이 다",
            "원인 기록이 다",
            "원인 충돌",
            "conflicting cause",
            "causes differ",
        )
    )


def _has_historical_verified_case(evidence: list[dict[str, Any]]) -> bool:
    return any(
        case.get("reliability") == "verified_historical_analogy"
        for item in evidence
        if item.get("source_type") == "diagnosis_synthesis"
        for case in item.get("metadata", {}).get("similar_cases", [])
    )


def _discloses_historical_case_limit(answer: str) -> bool:
    lowered = answer.casefold()
    has_period_mismatch = any(
        term in lowered
        for term in (
            "질문 기간 밖",
            "요청 기간 밖",
            "기간이 다른",
            "시점이 다른",
            "outside the requested period",
            "different time period",
        )
    )
    has_historical_boundary = any(
        term in lowered
        for term in (
            "과거 사례",
            "직접 근거가 아",
            "시간 정합 근거가 아",
            "historical case",
            "not time-aligned evidence",
        )
    )
    return has_period_mismatch and has_historical_boundary


def _has_sql_evidence(evidence: list[dict[str, Any]]) -> bool:
    return any(
        item.get("source_type") == "text2sql_plan"
        and item.get("metadata", {}).get("status") == "succeeded"
        and bool(item.get("metadata", {}).get("sql"))
        and (
            int(item.get("metadata", {}).get("row_count") or 0) > 0
            or bool(item.get("metadata", {}).get("sample_rows"))
        )
        for item in evidence
    )


def _evidence_is_incomplete(evidence: list[dict[str, Any]]) -> bool:
    sql_items = [item for item in evidence if item.get("source_type") == "text2sql_plan"]
    if not sql_items:
        return True
    return any(
        item.get("metadata", {}).get("status") != "succeeded"
        or not item.get("metadata", {}).get("sql")
        for item in sql_items
    )


def _contains_numeric_claim(answer: str) -> bool:
    return bool(
        re.search(
            r"(?<![0-9a-z_])\d+(?:\.\d+)?\s*(?:%|개|건|lot|lots|wip|시간|분)",
            answer.casefold(),
        )
    )


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


def _mentions_calculation_boundary(answer: str, evidence: list[dict[str, Any]]) -> bool:
    calculation = next(
        (
            item.get("metadata", {})
            for item in evidence
            if item.get("source_type") == "impact_calculation"
        ),
        {},
    )
    lowered = answer.casefold()
    if calculation.get("status") == "succeeded":
        has_input = any(term in lowered for term in ("입력", "baseline", "기준"))
        has_method = any(term in lowered for term in ("계산", "계산식", "공식", "가정"))
        has_boundary = any(term in lowered for term in ("가정", "한계", "제한", "1차", "모델"))
        return has_input and has_method and has_boundary
    return _mentions_limitation(answer)


def _missing_mixed_impact_dimension_disclosures(
    answer: str, evidence: list[dict[str, Any]]
) -> list[str]:
    calculation = next(
        (
            item.get("metadata", {})
            for item in evidence
            if item.get("source_type") == "impact_calculation"
        ),
        {},
    )
    dimensions = list(
        calculation.get("baseline", {}).get("mixed_dimensions")
        or calculation.get("provenance", {}).get("mixed_dimensions")
        or []
    )
    if not dimensions:
        return []

    lowered = answer.casefold()
    aliases = {
        "fab_id": ("fab", "팹"),
        "part": ("part", "파트", "제품"),
        "product": ("product", "제품"),
        "product_name": ("product", "제품"),
        "route": ("route", "라우트", "공정 경로"),
        "route_name": ("route", "라우트", "공정 경로"),
        "stn": ("stn", "station", "설비"),
        "stngrp": ("stngrp", "station group", "공정 그룹"),
        "period": ("period", "기간"),
        "report_time": ("report time", "리포트 시각", "기간"),
        "report_date": ("report date", "리포트 날짜", "기간"),
        "release_date": ("release date", "투입일", "기간"),
        "start_date": ("start date", "시작일", "기간"),
        "due_date": ("due date", "납기일", "기간"),
    }
    missing = [
        dimension
        for dimension in dimensions
        if not any(term in lowered for term in aliases.get(dimension, (dimension,)))
    ]
    separation_disclosed = any(
        term in lowered
        for term in ("별 baseline", "별로", "분리", "나눠", "각각", "각 대상", "각 기간")
    )
    if not separation_disclosed:
        return dimensions
    return missing


def _question_requests_impact(question: str) -> bool:
    lowered = question.casefold()
    return any(
        term in lowered
        for term in ("영향", "영향도", "impact", "capacity 변화", "생산능력 변화")
    )


def _question_requests_chart(question: str) -> bool:
    lowered = question.casefold()
    return any(term in lowered for term in ("차트", "그래프", "chart", "graph"))


def _has_successful_visualization(evidence: list[dict[str, Any]]) -> bool:
    return any(
        item.get("source_type") == "visualization_spec"
        and item.get("metadata", {}).get("status") == "succeeded"
        and item.get("metadata", {}).get("chart_type")
        for item in evidence
    )


def _discloses_chart_unavailable(answer: str) -> bool:
    lowered = answer.casefold()
    chart_terms = ("차트", "그래프", "chart", "graph")
    unavailable_terms = (
        "생성할 수 없",
        "생성하지 못",
        "표시할 수 없",
        "제공할 수 없",
        "unavailable",
        "cannot create",
        "could not create",
    )
    return any(term in lowered for term in chart_terms) and any(
        term in lowered for term in unavailable_terms
    )


def _has_impact_estimate(evidence: list[dict[str, Any]]) -> bool:
    return any(
        item.get("source_type") == "impact_calculation"
        and item.get("metadata", {}).get("status") == "succeeded"
        and bool(item.get("metadata", {}).get("estimates"))
        for item in evidence
    )


def _mentions_impact_estimate(answer: str, evidence: list[dict[str, Any]]) -> bool:
    estimate_values = [
        (str(key), value)
        for item in evidence
        if item.get("source_type") == "impact_calculation"
        and item.get("metadata", {}).get("status") == "succeeded"
        for key, value in item.get("metadata", {}).get("estimates", {}).items()
        if not str(key).startswith("baseline_") and isinstance(value, (int, float))
    ]
    normalized_answer = answer.replace(",", "")
    return any(
        _impact_estimate_appears(normalized_answer, key, float(value))
        for key, value in estimate_values
    )


def _impact_estimate_appears(answer: str, key: str, value: float) -> bool:
    exact = format(value, "g")
    if re.search(rf"(?<![\d.]){re.escape(exact)}(?![\d.])", answer):
        return True
    if value >= 0 or not _is_directional_estimate(key):
        return False
    magnitude = format(abs(value), "g")
    return any(
        _direction_in_context(answer, match.start(), match.end()) == "decrease"
        for match in re.finditer(
            rf"(?<![\d.+-]){re.escape(magnitude)}(?![\d.])",
            answer,
        )
    )


def _missing_requested_impact_estimates(
    question: str,
    answer: str,
    evidence: list[dict[str, Any]],
) -> list[str]:
    calculation = next(
        (
            item.get("metadata", {})
            for item in evidence
            if item.get("source_type") == "impact_calculation"
            and item.get("metadata", {}).get("status") == "succeeded"
        ),
        {},
    )
    changes = calculation.get("scenario", {}).get("parsed_changes", [])
    estimates = calculation.get("estimates", {})
    if len(changes) < 2 or not estimates:
        return []

    question_lower = question.casefold()
    answer_lower = answer.casefold().replace(",", "")
    groups = (
        (
            "Capacity",
            ("capacity_delta_percent", "estimated_capacity_change_percent"),
            ("capacity", "생산능력", "생산 능력", "캐파"),
        ),
        (
            "Cycle Time",
            ("projected_cycle_time",),
            ("cycle time", "cycleavg", "사이클 타임", "사이클타임", "사이클"),
        ),
        (
            "Lot completion",
            ("estimated_lotcomps_delta",),
            ("lot completion", "lotcomps", "output", "생산량", "산출량"),
        ),
    )
    missing = []
    for label, keys, aliases in groups:
        if not any(alias in question_lower for alias in aliases):
            continue
        values = [
            (key, estimates[key])
            for key in keys
            if isinstance(estimates.get(key), (int, float))
        ]
        if not values:
            continue
        has_label = any(alias in answer_lower for alias in aliases)
        has_value = any(
            _impact_estimate_appears(answer_lower, key, float(value))
            for key, value in values
        )
        if not has_label or not has_value:
            missing.append(label)
    return missing


def _impact_direction_conflicts(
    answer: str,
    evidence: list[dict[str, Any]],
) -> list[str]:
    normalized_answer = answer.replace(",", "").casefold()
    conflicts = []
    seen = set()
    for item in evidence:
        if (
            item.get("source_type") != "impact_calculation"
            or item.get("metadata", {}).get("status") != "succeeded"
        ):
            continue
        for key, raw_value in item.get("metadata", {}).get("estimates", {}).items():
            if not _is_directional_estimate(str(key)) or not isinstance(raw_value, (int, float)):
                continue
            value = float(raw_value)
            if value == 0:
                continue
            magnitude = format(abs(value), "g")
            for match in re.finditer(
                rf"(?<![\d.])[-+]?{re.escape(magnitude)}(?![\d.])",
                normalized_answer,
            ):
                direction = _direction_in_context(
                    normalized_answer, match.start(), match.end()
                )
                expected = "increase" if value > 0 else "decrease"
                if direction and direction != expected:
                    label = f"{key}={format(value, 'g')} described as {direction}"
                    if label not in seen:
                        seen.add(label)
                        conflicts.append(label)
    return conflicts


def _is_directional_estimate(key: str) -> bool:
    lowered = key.casefold()
    return "delta" in lowered or "change" in lowered


def _direction_in_context(text: str, start: int, end: int) -> str | None:
    window_start = max(0, start - 36)
    window_end = min(len(text), end + 36)
    context = text[window_start:window_end].casefold()
    local_start = start - window_start
    local_end = end - window_start
    candidates: list[tuple[int, str]] = []
    for direction, terms in (
        ("increase", ("증가", "상승", "늘어", "increase", "higher")),
        ("decrease", ("감소", "하락", "떨어", "줄어", "decrease", "lower")),
    ):
        for term in terms:
            for match in re.finditer(re.escape(term), context):
                if match.end() <= local_start:
                    distance = local_start - match.end()
                elif match.start() >= local_end:
                    distance = match.start() - local_end
                else:
                    distance = 0
                candidates.append((distance, direction))
    if not candidates:
        return None
    nearest_distance = min(distance for distance, _ in candidates)
    nearest_directions = {
        direction for distance, direction in candidates if distance == nearest_distance
    }
    if len(nearest_directions) != 1:
        return None
    return nearest_directions.pop()


def _trend_direction_conflicts(
    answer: str,
    evidence: list[dict[str, Any]],
) -> list[str]:
    summaries = [
        summary
        for item in evidence
        if item.get("source_type") == "visualization_spec"
        and item.get("metadata", {}).get("status") == "succeeded"
        for summary in item.get("metadata", {}).get("trend_summary", [])
        if isinstance(summary, dict)
    ]
    normalized_answer = answer.replace(",", "").casefold()
    conflicts = []
    for summary in summaries:
        raw_value = summary.get("percent_delta")
        if not isinstance(raw_value, (int, float)) or raw_value == 0:
            continue
        value = float(raw_value)
        magnitude = format(abs(value), "g")
        series = str(summary.get("series") or "trend")
        series_terms = _trend_series_terms(series)
        for match in re.finditer(
            rf"(?<![\d.])[-+]?{re.escape(magnitude)}\s*%(?![\d.])",
            normalized_answer,
        ):
            context = normalized_answer[
                max(0, match.start() - 40) : min(len(normalized_answer), match.end() + 40)
            ]
            if len(summaries) > 1 and not any(term in context for term in series_terms):
                continue
            direction = _direction_in_context(
                normalized_answer, match.start(), match.end()
            )
            expected = "increase" if value > 0 else "decrease"
            if direction and direction != expected:
                conflicts.append(
                    f"{series} percent_delta={format(value, 'g')} described as {direction}"
                )
                break
    return conflicts


def _trend_series_terms(series: str) -> tuple[str, ...]:
    lowered = series.casefold()
    parts = [part.strip() for part in re.split(r"\s*(?:/|·)\s*", lowered) if part.strip()]
    aliases = [lowered, *parts]
    for part in parts:
        if "_" in part:
            aliases.extend((part.replace("_", "-"), part.replace("_", " ")))
        for metric, columns in _METRIC_COLUMNS.items():
            if part in columns:
                aliases.extend(alias.casefold() for alias in _METRIC_ALIASES[metric])
    return tuple(dict.fromkeys(aliases))


def _undisclosed_series_gaps(
    answer: str,
    evidence: list[dict[str, Any]],
) -> list[str]:
    gaps = [
        gap
        for item in evidence
        if item.get("source_type") == "visualization_spec"
        and item.get("metadata", {}).get("status") == "succeeded"
        for gap in item.get("metadata", {}).get("series_gaps", [])
        if isinstance(gap, dict) and gap.get("missing_x")
    ]
    if not gaps:
        return []

    lowered = answer.casefold()
    gap_terms = (
        "누락",
        "데이터 없음",
        "관측 없음",
        "미집계",
        "결측",
        "missing",
        "no data",
        "gap",
    )
    if not any(term in lowered for term in gap_terms):
        return [str(gap.get("series") or "trend") for gap in gaps]

    undisclosed = []
    for gap in gaps:
        series = str(gap.get("series") or "trend")
        series_present = any(term in lowered for term in _trend_series_terms(series))
        values_present = all(
            _value_appears_in_text(value, lowered)
            for value in gap.get("missing_x", [])
        )
        if not series_present or not values_present:
            undisclosed.append(series)
    return undisclosed


def _undisclosed_insufficient_trends(
    answer: str,
    evidence: list[dict[str, Any]],
) -> list[str]:
    insufficient = [
        item
        for evidence_item in evidence
        if evidence_item.get("source_type") == "visualization_spec"
        and evidence_item.get("metadata", {}).get("status") == "succeeded"
        for item in evidence_item.get("metadata", {}).get("series_coverage", [])
        if isinstance(item, dict) and item.get("assessment") == "insufficient"
    ]
    if not insufficient:
        return []

    lowered = answer.casefold()
    disclosure_terms = (
        "관측치가 1개",
        "관측치 1개",
        "한 시점",
        "단일 시점",
        "비교할 시점이 부족",
        "전체 시점 중",
        "관측 비율",
        "누락이 많",
        "추세를 계산할 수 없",
        "추세를 판단할 수 없",
        "coverage",
        "insufficient",
        "single observation",
        "not enough observations",
    )
    unsupported_no_change_terms = (
        "변화 없음",
        "변화가 없",
        "변동 없음",
        "그대로 유지",
        "no change",
        "stable",
    )
    has_disclosure = any(term in lowered for term in disclosure_terms)
    claims_no_change = any(term in lowered for term in unsupported_no_change_terms)
    undisclosed = []
    for item in insufficient:
        series = str(item.get("series") or "trend")
        series_present = any(term in lowered for term in _trend_series_terms(series))
        if not series_present or not has_disclosure or claims_no_change:
            undisclosed.append(series)
    return undisclosed


def _undisclosed_imputed_points(
    answer: str,
    evidence: list[dict[str, Any]],
) -> list[str]:
    points = [
        point
        for evidence_item in evidence
        if evidence_item.get("source_type") == "visualization_spec"
        and evidence_item.get("metadata", {}).get("status") == "succeeded"
        for point in evidence_item.get("metadata", {}).get("imputed_points", [])
        if isinstance(point, dict)
    ]
    if not points:
        return []

    lowered = answer.casefold()
    has_empty_bucket_context = bool(
        re.search(
            r"(?:빈|없는|누락된).{0,40}(?:bucket|버킷|날짜|시점|구간|행)",
            lowered,
        )
        or re.search(r"(?:missing|empty).{0,24}(?:bucket|date|point|row)", lowered)
    )
    has_zero_fill_action = bool(
        re.search(r"0\s*(?:건)?으로.{0,16}(?:채|보완)", lowered)
        or re.search(r"(?:zero[- ]fill|filled with zero)", lowered)
    )
    disclosed = has_empty_bucket_context and has_zero_fill_action
    return [] if disclosed else [f"{len(points)} point(s)"]


def _missing_question_facets(question: str, answer: str) -> list[str]:
    question_lower = question.casefold()
    answer_lower = answer.casefold()
    facets = []
    trend_terms = ("추세", "차트", "그래프", "trend", "chart", "흐름")
    trend_answer_terms = (*trend_terms, "시계열", "일별", "주별", "월별", "기간별")
    if any(term in question_lower for term in trend_terms) and not any(
        term in answer_lower for term in trend_answer_terms
    ):
        facets.append("trend_or_chart")
    impact_terms = ("영향", "영향도", "impact")
    impact_answer_terms = (*impact_terms, "계산", "변화", "capacity", "생산능력")
    if any(term in question_lower for term in impact_terms) and not any(
        term in answer_lower for term in impact_answer_terms
    ):
        facets.append("impact_calculation")
    return facets


def _mentions_limitation(answer: str) -> bool:
    lowered = answer.casefold()
    return any(
        term in lowered
        for term in (
            "제한",
            "한계",
            "가정",
            "기준",
            "부족",
            "없습니다",
            "없어",
            "있지 않",
            "활성화해야",
            "조회할 수 없",
            "확인 필요",
            "확정할 수 없",
            "확정 불가",
            "시뮬레이션",
            "unavailable",
        )
    )


_EQUIPMENT_ID_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])([A-Z][A-Za-z]{1,8}(?:[\s_-]+[A-Z]{2})"
    r"(?:[\s_-]+\d{1,3})+)(?![A-Za-z0-9_])"
)


def _equipment_id_parts(match: re.Match[str]) -> list[str]:
    return re.split(r"[\s_-]+", match.group(1))


def _missing_question_identifiers(question: str, answer: str) -> list[str]:
    answer_lower = answer.casefold()
    identifiers: list[tuple[str, tuple[str, ...]]] = []
    for match in re.finditer(r"\bfab[\s_-]*(1[0-3])\b", question, flags=re.IGNORECASE):
        canonical = f"fab{match.group(1)}"
        identifiers.append((canonical, (canonical, match.group(0).casefold())))
    for match in re.finditer(
        r"\b(product|part)[\s_-]*([eE]?\d+)\b", question, flags=re.IGNORECASE
    ):
        suffix = match.group(2).casefold()
        identifiers.append(
            (f"Product_{suffix}", (f"product_{suffix}", f"product {suffix}", f"part_{suffix}", f"part {suffix}"))
        )
    for match in _EQUIPMENT_ID_PATTERN.finditer(question):
        parts = _equipment_id_parts(match)
        canonical = "_".join(parts).casefold()
        identifiers.append(
            (
                "_".join(parts),
                (canonical, canonical.replace("_", "-"), canonical.replace("_", " ")),
            )
        )
    generic = re.findall(
        r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b",
        question,
        flags=re.IGNORECASE,
    )
    identifiers.extend((item, (item.casefold(),)) for item in generic)
    missing = []
    seen = set()
    for label, aliases in identifiers:
        key = label.casefold()
        if key in seen:
            continue
        seen.add(key)
        if not any(alias in answer_lower for alias in aliases):
            missing.append(label)
    return missing


def _missing_question_dates(
    question: str,
    answer: str,
) -> list[str]:
    answer_lower = answer.casefold()
    required: list[str] = []
    for raw in re.findall(r"(?<!\d)\d{4}[-/.]\d{2}[-/.]\d{2}(?!\d)", question):
        canonical = re.sub(r"[/.]", "-", raw)
        if raw.casefold() not in answer_lower and canonical.casefold() not in answer_lower:
            required.append(raw)
    relative_contexts = (
        ((r"지난\s*주", r"last\s+week"), (r"지난\s*주", r"last\s+week")),
        ((r"이번\s*주", r"this\s+week"), (r"이번\s*주", r"this\s+week")),
        ((r"지난\s*달", r"last\s+month"), (r"지난\s*달", r"last\s+month")),
        ((r"이번\s*달", r"this\s+month"), (r"이번\s*달", r"this\s+month")),
        ((r"지난\s*분기", r"last\s+quarter"), (r"지난\s*분기", r"last\s+quarter")),
        ((r"이번\s*분기", r"this\s+quarter"), (r"이번\s*분기", r"this\s+quarter")),
        ((r"최근\s*\d+\s*일",), (r"최근\s*\d+\s*일", r"last\s+\d+\s+days?")),
        (
            (r"(?:최근|지난)\s*(?:1\s*)?일주일",),
            (r"(?:최근|지난)\s*(?:1\s*)?일주일", r"last\s+week", r"last\s+7\s+days?"),
        ),
    )
    for question_patterns, answer_patterns in relative_contexts:
        match = next(
            (
                found
                for pattern in question_patterns
                if (found := re.search(pattern, question, flags=re.IGNORECASE))
            ),
            None,
        )
        if match and not any(
            re.search(pattern, answer, flags=re.IGNORECASE) for pattern in answer_patterns
        ):
            required.append(match.group(0))

    for match in re.finditer(r"(\d{4})\s*년\s*(1[0-2]|[1-9])\s*월", question):
        year, month = match.groups()
        answer_pattern = rf"{year}\s*(?:년\s*|[-/.])0?{int(month)}\s*월?"
        if not re.search(answer_pattern, answer, flags=re.IGNORECASE):
            required.append(match.group(0))

    seen_quarters: set[tuple[str | None, str]] = set()
    quarter_matches = [
        *re.finditer(r"(?:(\d{4})\s*년\s*)?([1-4])\s*분기", question),
        *re.finditer(r"\bq([1-4])\s*(\d{4})\b", question, flags=re.IGNORECASE),
        *re.finditer(r"\b(\d{4})\s*q([1-4])\b", question, flags=re.IGNORECASE),
    ]
    for match in quarter_matches:
        groups = match.groups()
        if match.re.pattern.startswith(r"\bq"):
            quarter, year = groups
        else:
            year, quarter = groups
        key = (year, quarter)
        if key in seen_quarters:
            continue
        seen_quarters.add(key)
        if year:
            answer_pattern = (
                rf"(?:{year}\s*(?:년\s*)?{quarter}\s*분기|"
                rf"q{quarter}\s*{year}|{year}\s*q{quarter})"
            )
        else:
            answer_pattern = rf"(?:{quarter}\s*분기|q{quarter}(?![0-9a-z]))"
        if not re.search(answer_pattern, answer, flags=re.IGNORECASE):
            required.append(match.group(0))
    return list(dict.fromkeys(value for value in required if value.casefold() not in answer_lower))


def _missing_selection_constraints(question: str, answer: str) -> list[str]:
    missing: list[str] = []
    comparator_patterns = {
        ">=": (r"이상", r">=", r"at\s+least"),
        "<=": (r"이하", r"<=", r"at\s+most"),
        ">": (r"초과", r"(?<![<])>(?!=)", r"more\s+than", r"over", r"above"),
        "<": (r"미만", r"(?<![>])<(?!=)", r"less\s+than", r"under", r"below"),
    }
    korean_operators = {"이상": ">=", "이하": "<=", "초과": ">", "미만": "<"}
    english_operators = {
        "at least": ">=", "at most": "<=", "more than": ">", "over": ">",
        "above": ">", "less than": "<", "under": "<", "below": "<",
    }

    def comparison_is_missing(value: str, operator: str) -> bool:
        value_pattern = rf"(?<![\d.]){re.escape(value)}(?![\d.])"
        direction = comparator_patterns[operator]
        value_then_direction = rf"{value_pattern}.{{0,16}}(?:{'|'.join(direction)})"
        direction_then_value = rf"(?:{'|'.join(direction)}).{{0,16}}{value_pattern}"
        return not re.search(
            rf"(?:{value_then_direction}|{direction_then_value})",
            answer,
            flags=re.IGNORECASE,
        )

    for match in re.finditer(
        r"(\d+(?:\.\d+)?)\s*(%|퍼센트|percent)?\s*(이상|이하|초과|미만)",
        question,
        flags=re.IGNORECASE,
    ):
        value, unit, comparator = match.groups()
        if comparison_is_missing(value, korean_operators[comparator]):
            missing.append(f"{value}{unit or ''} {comparator}")

    for match in re.finditer(
        r"(>=|<=|>|<)\s*(\d+(?:\.\d+)?)\s*(%|퍼센트|percent)?",
        question,
        flags=re.IGNORECASE,
    ):
        operator, value, unit = match.groups()
        if comparison_is_missing(value, operator):
            missing.append(f"{operator} {value}{unit or ''}")

    for match in re.finditer(
        r"(at\s+least|at\s+most|more\s+than|over|above|less\s+than|under|below)"
        r"\s*(\d+(?:\.\d+)?)\s*(%|percent)?",
        question,
        flags=re.IGNORECASE,
    ):
        comparator, value, unit = match.groups()
        canonical = english_operators[re.sub(r"\s+", " ", comparator.casefold())]
        if comparison_is_missing(value, canonical):
            missing.append(f"{comparator} {value}{unit or ''}")

    for match in re.finditer(
        r"(상위|하위|top|bottom)\s*(\d+)\s*(?:개|건)?",
        question,
        flags=re.IGNORECASE,
    ):
        direction, count = match.groups()
        aliases = (
            (r"상위", r"top")
            if direction.casefold() in {"상위", "top"}
            else (r"하위", r"bottom")
        )
        if not re.search(
            rf"(?:{'|'.join(aliases)})\s*{re.escape(count)}\s*(?:개|건)?",
            answer,
            flags=re.IGNORECASE,
        ):
            missing.append(f"{direction} {count}")

    qualitative_rankings = {
        "DESC": (
            r"(?:높은|큰)\s*순", r"내림차순", r"상위", r"\btop\b",
            r"\bdesc(?:ending)?\b",
        ),
        "ASC": (
            r"(?:낮은|작은)\s*순", r"오름차순", r"하위", r"\bbottom\b",
            r"\basc(?:ending)?\b",
        ),
    }
    qualitative_question_patterns = {
        "DESC": r"(?:높은|큰)\s*순|내림차순|상위(?!\s*\d)|\btop\b(?!\s*\d)",
        "ASC": r"(?:낮은|작은)\s*순|오름차순|하위(?!\s*\d)|\bbottom\b(?!\s*\d)",
    }
    for direction, question_pattern in qualitative_question_patterns.items():
        for match in re.finditer(question_pattern, question, flags=re.IGNORECASE):
            if not re.search(
                "|".join(qualitative_rankings[direction]),
                answer,
                flags=re.IGNORECASE,
            ):
                missing.append(match.group(0))
    return missing


_METRIC_ALIASES = {
    "WIP": ("wip", "재공"),
    "Queue Time": ("queue time", "queue", "대기시간", "대기 시간"),
    "Cycle Time": ("cycle time", "cycleavg", "사이클 타임", "사이클타임"),
    "Ontime": ("ontime", "on-time", "납기 준수", "납기준수"),
    "Utilization": ("utilization", "util_percent", "가동률"),
    "Down": ("down", "다운", "비가동"),
    "PM": ("pm", "예방정비", "예방 정비"),
    "Lot completion": ("lot completion", "lotcomps", "lot 완료", "완료 lot"),
    "Output": ("output", "throughput", "생산량", "산출량"),
    "Capacity": ("capacity", "캐파", "생산능력", "생산 능력"),
}


def _missing_question_metrics(question: str, answer: str) -> list[str]:
    question_lower = question.casefold()
    answer_lower = answer.casefold()
    missing = []
    for metric, aliases in _METRIC_ALIASES.items():
        if any(alias in question_lower for alias in aliases) and not any(
            alias in answer_lower for alias in aliases
        ):
            missing.append(metric)
    return missing


_METRIC_COLUMNS = {
    "WIP": ("wiplotavg", "wiplotcur"),
    "Cycle Time": ("cycleavg",),
    "Ontime": ("ontime_percent",),
    "Utilization": ("util_percent",),
    "Down": ("down_percent",),
    "PM": ("pm_percent",),
    "Lot completion": ("lotcomps",),
}


def _requested_metric_groups(question: str) -> dict[str, tuple[str, ...]]:
    lowered = question.casefold()
    return {
        metric: columns
        for metric, columns in _METRIC_COLUMNS.items()
        if any(alias in lowered for alias in _METRIC_ALIASES[metric])
    }


def _sql_sample_rows(evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        row
        for item in evidence
        if item.get("source_type") == "text2sql_plan"
        and item.get("metadata", {}).get("status") == "succeeded"
        for row in item.get("metadata", {}).get("sample_rows", [])
        if isinstance(row, dict)
    ]


def _missing_requested_metric_values(
    question: str,
    answer: str,
    evidence: list[dict[str, Any]],
) -> list[str]:
    groups = _requested_metric_groups(question)
    rows = _sql_sample_rows(evidence)
    answer_lower = answer.casefold()
    missing = []
    for metric, columns in groups.items():
        values = [
            row[column]
            for row in rows
            for column in columns
            if row.get(column) is not None
        ]
        if values and not any(_value_appears_in_text(value, answer_lower) for value in values):
            missing.append(metric)
    return missing


def _requested_result_targets(question: str) -> list[tuple[str, re.Pattern[str], str]]:
    targets: list[tuple[str, re.Pattern[str], str]] = []
    seen = set()
    for match in re.finditer(
        r"\b(product|part)[\s_-]*([eE]?\d+)\b", question, flags=re.IGNORECASE
    ):
        suffix = match.group(2)
        label = f"Product_{suffix}"
        normalized = f"part{suffix}".casefold()
        if normalized not in seen:
            seen.add(normalized)
            targets.append(
                (
                    label,
                    re.compile(
                        rf"\b(?:product|part)[\s_-]*{re.escape(suffix)}\b",
                        flags=re.IGNORECASE,
                    ),
                    normalized,
                )
            )
    for match in _EQUIPMENT_ID_PATTERN.finditer(question):
        parts = _equipment_id_parts(match)
        label = "_".join(parts)
        normalized = _normalize_result_target(label)
        if normalized not in seen:
            seen.add(normalized)
            targets.append(
                (
                    label,
                    re.compile(
                        r"\b"
                        + r"[\s_-]+".join(re.escape(part) for part in parts)
                        + r"\b",
                        flags=re.IGNORECASE,
                    ),
                    normalized,
                )
            )
    return targets


def _normalize_result_target(value: Any) -> str:
    normalized = re.sub(r"[\s_-]+", "", str(value).casefold())
    if normalized.startswith("product"):
        return "part" + normalized.removeprefix("product")
    return normalized


def _target_answer_segments(
    answer: str,
    targets: list[tuple[str, re.Pattern[str], str]],
) -> dict[str, list[str]]:
    mentions = sorted(
        (
            match.start(),
            match.end(),
            normalized,
        )
        for _, pattern, normalized in targets
        for match in pattern.finditer(answer)
    )
    segments: dict[str, list[str]] = {}
    for index, (start, _, normalized) in enumerate(mentions):
        end = mentions[index + 1][0] if index + 1 < len(mentions) else len(answer)
        segments.setdefault(normalized, []).append(answer[start:end].casefold())
    return segments


def _missing_requested_target_values(
    question: str,
    answer: str,
    evidence: list[dict[str, Any]],
) -> list[str]:
    groups = _requested_metric_groups(question)
    targets = _requested_result_targets(question)
    if not groups or not targets:
        return []

    rows = _sql_sample_rows(evidence)
    segments = _target_answer_segments(answer, targets)
    missing = []
    for label, _, normalized in targets:
        target_rows = [
            row
            for row in rows
            if any(_normalize_result_target(value) == normalized for value in row.values())
        ]
        if not target_rows:
            continue
        target_text = "\n".join(segments.get(normalized, []))
        for metric, columns in groups.items():
            values = [
                row[column]
                for row in target_rows
                for column in columns
                if row.get(column) is not None
            ]
            if values and not any(_value_appears_in_text(value, target_text) for value in values):
                missing.append(f"{label} {metric}")
    return missing


def _missing_requested_trend_series_values(
    question: str,
    answer: str,
    evidence: list[dict[str, Any]],
) -> list[str]:
    groups = _requested_metric_groups(question)
    targets = _requested_result_targets(question)
    if not groups or len(targets) < 2:
        return []

    summaries = [
        summary
        for item in evidence
        if item.get("source_type") == "visualization_spec"
        and item.get("metadata", {}).get("status") == "succeeded"
        for summary in item.get("metadata", {}).get("trend_summary", [])
        if isinstance(summary, dict)
    ]
    if not summaries:
        return []

    segments = _target_answer_segments(answer, targets)
    missing = []
    for label, _, target_normalized in targets:
        target_text = "\n".join(segments.get(target_normalized, []))
        for metric, columns in groups.items():
            matching = [
                summary
                for summary in summaries
                if _trend_summary_matches_target_metric(
                    summary,
                    target_normalized=target_normalized,
                    metric_columns=columns,
                )
            ]
            if not matching:
                continue
            metric_mentioned = any(
                alias.casefold() in target_text for alias in _METRIC_ALIASES[metric]
            )
            values = [
                value
                for summary in matching
                for key in (
                    "start_value",
                    "end_value",
                    "absolute_delta",
                    "percent_delta",
                )
                if (value := summary.get(key)) is not None
            ]
            value_mentioned = any(
                _value_appears_in_text(value, target_text)
                or (
                    isinstance(value, (int, float))
                    and _value_appears_in_text(abs(value), target_text)
                )
                for value in values
            )
            if not metric_mentioned or not value_mentioned:
                missing.append(f"{label} {metric}")
    return missing


def _missing_comparison_period_values(
    question: str,
    answer: str,
    evidence: list[dict[str, Any]],
) -> list[str]:
    groups = _requested_metric_groups(question)
    rows = [row for row in _sql_sample_rows(evidence) if row.get("comparison_period")]
    if not groups or len(rows) < 2:
        return []

    answer_segments = [segment for segment in re.split(r"[.\n]", answer) if segment.strip()]
    missing = []
    for row in rows:
        label = str(row["comparison_period"])
        dates = re.findall(r"\d{4}-\d{2}-\d{2}", label)
        matching_segments = [
            segment
            for segment in answer_segments
            if dates and all(value in segment for value in dates)
        ]
        if not matching_segments:
            missing.append(label)
            continue
        period_text = "\n".join(matching_segments)
        for metric, columns in groups.items():
            values = [row[column] for column in columns if row.get(column) is not None]
            if values and not any(_value_appears_in_text(value, period_text) for value in values):
                missing.append(f"{label} {metric}")
    return missing


def _trend_summary_matches_target_metric(
    summary: dict[str, Any],
    *,
    target_normalized: str,
    metric_columns: tuple[str, ...],
) -> bool:
    parts = [
        part.strip()
        for part in re.split(r"\s*(?:/|·)\s*", str(summary.get("series") or ""))
        if part.strip()
    ]
    if len(parts) < 2 or _normalize_result_target(parts[0]) != target_normalized:
        return False
    metric_name = parts[-1].casefold()
    return any(metric_name == column.casefold() for column in metric_columns)


def _without_grounded_sql(answer: str, evidence: list[dict[str, Any]]) -> str:
    """SQL limits/predicates quoted from successful execution are not result claims."""
    for item in evidence:
        metadata = item.get("metadata") or {}
        sql = metadata.get("sql")
        if metadata.get("status") != "succeeded" or not isinstance(sql, str) or not sql.strip():
            continue
        tokens = re.findall(r"'(?:''|[^'])*'|[A-Za-z_][A-Za-z_0-9]*|[0-9]+(?:\.[0-9]+)?|[^\s]", sql.strip().rstrip(";"))
        pattern = r"\s*".join(re.escape(part) for part in tokens)
        answer = re.sub(pattern + r";?", " ", answer, flags=re.IGNORECASE)
    return answer


def _unsupported_status_numeric_claims(
    question: str,
    answer: str,
    evidence: list[dict[str, Any]],
) -> list[str]:
    allowed = set(_numeric_claims(question).values())
    for row in _sql_sample_rows(evidence):
        for value in row.values():
            normalized = _numeric_cell(value)
            if normalized is not None:
                allowed.add(normalized)

    unsupported = []
    for raw, normalized in _numeric_claims(_without_grounded_sql(answer, evidence)).items():
        if not _grounded_numeric_claim(raw, normalized, allowed) and raw not in unsupported:
            unsupported.append(raw)
    return unsupported


def _unsupported_impact_numeric_claims(
    question: str,
    answer: str,
    evidence: list[dict[str, Any]],
) -> list[str]:
    allowed = set(_numeric_claims(question).values())
    for row in _sql_sample_rows(evidence):
        for value in row.values():
            normalized = _numeric_cell(value)
            if normalized is not None:
                allowed.add(normalized)
    for item in evidence:
        if item.get("source_type") != "impact_calculation":
            continue
        values = _numeric_values_from_structure(item.get("metadata", {}))
        allowed.update(values)
        allowed.update(abs(value) for value in values)

    unsupported = []
    for raw, normalized in _numeric_claims(_without_grounded_sql(answer, evidence)).items():
        if not _grounded_numeric_claim(raw, normalized, allowed) and raw not in unsupported:
            unsupported.append(raw)
    return unsupported


def _unsupported_diagnosis_numeric_claims(
    question: str,
    answer: str,
    evidence: list[dict[str, Any]],
) -> list[str]:
    allowed = set(_numeric_claims(question).values())
    for item in evidence:
        if item.get("source_type") == "text2sql_plan":
            for row in item.get("metadata", {}).get("sample_rows", []):
                if isinstance(row, dict):
                    allowed.update(_numeric_values_from_structure(row))
            continue
        allowed.update(_numeric_values_from_structure(item.get("metadata", {})))
        allowed.update(_numeric_claims(str(item.get("content") or "")).values())

    unsupported = []
    for raw, normalized in _numeric_claims(_without_grounded_sql(answer, evidence)).items():
        if not _grounded_numeric_claim(raw, normalized, allowed) and raw not in unsupported:
            unsupported.append(raw)
    return unsupported


def _unsupported_trend_numeric_claims(
    question: str,
    answer: str,
    evidence: list[dict[str, Any]],
) -> list[str]:
    allowed = set(_numeric_claims(question).values())
    for item in evidence:
        if item.get("source_type") == "text2sql_plan":
            for row in item.get("metadata", {}).get("sample_rows", []):
                if isinstance(row, dict):
                    allowed.update(_numeric_values_from_structure(row))
        elif item.get("source_type") == "visualization_spec":
            metadata = item.get("metadata", {})
            values = _numeric_values_from_structure(metadata.get("trend_summary", []))
            allowed.update(values)
            allowed.update(abs(value) for value in values)
            allowed.update(
                _numeric_values_from_structure(metadata.get("imputed_points", []))
            )
            imputed_points = metadata.get("imputed_points", [])
            if isinstance(imputed_points, list) and imputed_points:
                allowed.add(Decimal(len(imputed_points)))
            for coverage in metadata.get("series_coverage", []):
                if not isinstance(coverage, dict):
                    continue
                for key in ("point_count", "axis_point_count", "coverage_rate"):
                    normalized = _numeric_cell(coverage.get(key))
                    if normalized is not None:
                        allowed.add(normalized)
                        if key == "coverage_rate":
                            allowed.add(normalized * 100)

    unsupported = []
    for raw, normalized in _numeric_claims(_without_grounded_sql(answer, evidence)).items():
        if not _grounded_numeric_claim(raw, normalized, allowed) and raw not in unsupported:
            unsupported.append(raw)
    return unsupported


def _grounded_numeric_claim(raw: str, value: Decimal, allowed: set[Decimal]) -> bool:
    if value in allowed:
        return True
    # Display rounding is allowed at the precision actually printed, not an
    # arbitrary tolerance that could accept a different measurement or count.
    if "." not in raw:
        return False
    precision = len(raw.rsplit(".", 1)[1])
    quantum = Decimal(1).scaleb(-precision)
    for observed in allowed:
        try:
            if observed.is_finite() and observed.quantize(quantum, rounding=ROUND_HALF_UP) == value:
                return True
        except InvalidOperation:
            continue
    return False


def _numeric_values_from_structure(value: Any) -> set[Decimal]:
    normalized = _numeric_cell(value)
    if normalized is not None:
        return {normalized}
    if isinstance(value, dict):
        return {
            number
            for nested in value.values()
            for number in _numeric_values_from_structure(nested)
        }
    if isinstance(value, (list, tuple)):
        return {
            number
            for nested in value
            for number in _numeric_values_from_structure(nested)
        }
    if isinstance(value, str):
        return set(_numeric_claims(value).values())
    return set()


def _numeric_claims(text: str) -> dict[str, Decimal]:
    scrubbed = re.sub(
        r"\b(?:fab[\s_-]*1[0-3]|(?:product|part)[\s_-]*[eE]?\d+)\b",
        " ",
        text,
        flags=re.IGNORECASE,
    )
    scrubbed = re.sub(
        r"\b[A-Z][A-Za-z]{1,8}[\s_-]+[A-Z]{2}[\s_-]+\d{1,3}\b",
        " ",
        scrubbed,
    )
    scrubbed = re.sub(
        r"(?<![A-Za-z0-9_])[A-Za-z][A-Za-z0-9]*(?:[-_][A-Za-z0-9]+)+(?![A-Za-z0-9_])",
        " ",
        scrubbed,
    )
    scrubbed = re.sub(
        r"(?<!\d)(?:[01]\d|2[0-3]):[0-5]\d(?::[0-5]\d(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})?",
        " ", scrubbed,
    )
    scrubbed = re.sub(r"(?<!\d)\d{4}[-/.]\d{1,2}[-/.]\d{1,2}(?!\d)", " ", scrubbed)
    scrubbed = re.sub(r"(?<!\d)\d+\s*차", " ", scrubbed)
    # Markdown list ordinals describe document structure, not measured quantities.
    scrubbed = re.sub(r"(?m)^[ \t]{0,3}\d+[.)][ \t]+(?=\S)", "", scrubbed)
    lines = []
    for line in scrubbed.splitlines():
        ordinals = [int(value) for value in re.findall(r"\((\d+)\)[ \t]+(?=\D)", line)]
        if len(ordinals) >= 2 and ordinals == list(range(1, len(ordinals) + 1)):
            line = re.sub(r"\(\d+\)[ \t]+(?=\D)", "", line)
        lines.append(line)
    scrubbed = "\n".join(lines)
    claims: dict[str, Decimal] = {}
    for match in re.finditer(
        r"(?<![0-9A-Za-z_])[-+]?\d[\d,]*(?:\.\d+)?(?![0-9A-Za-z_])",
        scrubbed,
    ):
        raw = match.group(0)
        try:
            claims[raw] = Decimal(raw.replace(",", "")).normalize()
        except InvalidOperation:
            continue
    return claims


def _numeric_cell(value: Any) -> Decimal | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        try:
            return Decimal(str(value)).normalize()
        except InvalidOperation:
            return None
    if isinstance(value, str) and re.fullmatch(r"[-+]?\d[\d,]*(?:\.\d+)?", value.strip()):
        try:
            return Decimal(value.strip().replace(",", "")).normalize()
        except InvalidOperation:
            return None
    return None


def _value_appears_in_text(value: Any, text: str) -> bool:
    if isinstance(value, bool):
        return str(value).casefold() in text
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return str(value).casefold() in text
    normalized_text = text.replace(",", "")
    candidates = {str(value).replace(",", ""), f"{numeric:g}"}
    return any(
        re.search(rf"(?<![\d.]){re.escape(candidate)}(?![\d.])", normalized_text)
        for candidate in candidates
    )
