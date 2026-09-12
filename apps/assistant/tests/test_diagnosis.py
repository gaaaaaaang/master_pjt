from app.sub_agent.diagnosis import synthesize_diagnosis

VERIFICATION = {
    "incident_at": "2026-08-31T14:00:00+09:00",
    "verified_at": "2026-09-01T09:00:00+09:00",
    "verified_by": "fab-incident-review-board",
    "evidence_refs": ["INCIDENT-001", "EVENT-LOG-001"],
}


def test_synthesis_separates_observations_candidates_and_cases() -> None:
    evidence = [
        {
            "source_type": "text2sql_plan",
            "title": "query",
            "content": "result",
            "metadata": {
                "status": "succeeded",
                "row_count": 1,
                "columns": ["stn", "down_percent"],
                "sample_rows": [{"stn": "DE_BE_11", "down_percent": 12.5}],
                "sql": "SELECT stn, down_percent FROM fab10.autosched_stn_fab10",
            },
        },
        {
            "source_type": "rag_chunk",
            "title": "Down playbook",
            "content": "PM 지연과 고장 이력을 점검한다.",
            "metadata": {
                "source_document": "down.md",
                "issue_types": "equipment_down,pm_delay",
            },
        },
        {
            "source_type": "similar_case",
            "title": "INC-001",
            "content": "검증 사례: PM 지연 후 down 증가",
            "metadata": {
                "case_id": "INC-001",
                "case_type": "verified",
                "source": "incident-db",
                "issue_type": "station_down",
                "verification": VERIFICATION,
            },
        },
    ]

    result = synthesize_diagnosis(evidence)

    assert result["metadata"]["conclusion_level"] == "candidate_only"
    assert result["metadata"]["source_coverage"] == {
        "operational_sql": True,
        "incident_playbook": True,
        "similar_cases": True,
    }
    assert result["metadata"]["observations"][0]["sample_rows"][0]["stn"] == "DE_BE_11"
    assert result["metadata"]["candidate_causes"][0]["source_document"] == "down.md"
    assert result["metadata"]["similar_cases"][0]["case_id"] == "INC-001"
    assert result["metadata"]["similar_cases"][0]["reliability"] == "verified_analogy"
    assert result["metadata"]["similar_cases"][0]["verification"] == VERIFICATION
    assert result["metadata"]["reliability_assessment"]["can_confirm_root_cause"] is False
    assert "확정할 수 없습니다" in result["content"]


def test_synthesis_reports_missing_evidence_without_inventing_candidates() -> None:
    result = synthesize_diagnosis([])

    assert result["metadata"]["candidate_causes"] == []
    assert result["metadata"]["missing_evidence"] == [
        "operational_sql_observation",
        "incident_playbook_candidate",
        "similar_incident",
    ]
    assert "실제 원인을 확정할 수 없습니다" in result["content"]


def test_empty_successful_sql_result_is_not_an_operational_observation() -> None:
    result = synthesize_diagnosis(
        [
            {
                "source_type": "text2sql_plan",
                "title": "empty query",
                "metadata": {
                    "status": "succeeded",
                    "row_count": 0,
                    "sample_rows": [],
                    "sql": "SELECT * FROM fab10.autosched_perf_fab10 WHERE FALSE",
                },
            }
        ]
    )

    assert result["metadata"]["observations"] == []
    assert result["metadata"]["source_coverage"]["operational_sql"] is False
    assert "operational_sql_observation" in result["metadata"]["missing_evidence"]


def test_simulated_cases_do_not_raise_corroboration_and_multiple_issues_stay_separate() -> None:
    result = synthesize_diagnosis(
        [
            {
                "source_type": "rag_chunk",
                "title": "playbook",
                "content": "시뮬레이션 RAG 참고용 병목 및 PM 점검",
                "metadata": {"issue_types": "bottleneck,pm_delay"},
            },
            {
                "source_type": "similar_case",
                "title": "SIM-1",
                "content": "시뮬레이션 사례",
                "metadata": {
                    "case_id": "SIM-1",
                    "case_type": "simulated_reference",
                    "issue_type": "station_down",
                    "source": "synthetic",
                },
            },
        ]
    )

    reliability = result["metadata"]["reliability_assessment"]
    assert reliability["corroboration_level"] == "simulated_reference_only"
    assert reliability["evidence_consistency"] == "competing_hypotheses"
    assert reliability["verified_case_count"] == 0
    assert reliability["simulated_knowledge_count"] == 1
    assert result["metadata"]["candidate_issue_types"] == [
        "bottleneck",
        "equipment_down",
        "pm_delay",
    ]
    assert "verified_incident_match" in result["metadata"]["required_verification"]
    assert "시뮬레이션 참고 사례" in result["content"]
    assert "실제 FAB SOP가 아닙니다" in result["content"]
    assert "복수 원인 후보" in result["content"]


def test_candidates_are_ranked_by_corroboration_reliability_and_observation() -> None:
    result = synthesize_diagnosis(
        [
            {
                "source_type": "text2sql_plan",
                "metadata": {
                    "status": "succeeded",
                    "row_count": 1,
                    "columns": ["stn", "down_percent"],
                    "sample_rows": [{"stn": "DE_BE_11", "down_percent": 12.5}],
                    "sql": "SELECT stn, down_percent FROM fab10.autosched_stn_fab10",
                },
            },
            {
                "source_type": "rag_chunk",
                "title": "queue playbook",
                "content": "Queue 점검",
                "metadata": {"issue_types": "queue_time", "score": 9.0},
            },
            {
                "source_type": "rag_chunk",
                "title": "down playbook",
                "content": "Down 점검",
                "metadata": {"issue_types": "equipment_down", "score": 1.0},
            },
            {
                "source_type": "similar_case",
                "content": "verified down incident",
                "metadata": {
                    "case_id": "INC-DOWN",
                    "case_type": "verified",
                    "source": "incident-db",
                    "issue_type": "equipment_down",
                    "score": 0.1,
                    "verification": VERIFICATION,
                },
            },
            {
                "source_type": "similar_case",
                "content": "simulated queue incident",
                "metadata": {
                    "case_id": "SIM-QUEUE",
                    "case_type": "simulated_reference",
                    "source": "synthetic",
                    "issue_type": "queue_time",
                    "score": 10.0,
                },
            },
        ]
    )

    rankings = result["metadata"]["candidate_rankings"]
    assert result["metadata"]["ranked_candidate_issue_types"] == [
        "equipment_down",
        "queue_time",
    ]
    assert rankings[0]["support_level"] == "strong_candidate"
    assert rankings[0]["operational_alignment"] is True
    assert rankings[0]["verified_case_count"] == 1
    assert rankings[0]["source_types"] == ["incident_playbook", "similar_case"]
    assert result["metadata"]["reliability_assessment"]["evidence_consistency"] == (
        "partially_aligned"
    )
    assert "우선 검증 후보는 equipment_down" in result["content"]
    assert "우선순위는 잠정적" in result["content"]


def test_invalid_taxonomy_is_filtered_before_top_k_selection() -> None:
    invalid_rag = [
        {
            "source_type": "rag_chunk",
            "content": "structure",
            "metadata": {"issue_types": "references"},
        }
        for _ in range(3)
    ]
    invalid_cases = [
        {
            "source_type": "similar_case",
            "content": "structure",
            "metadata": {
                "case_id": f"INVALID-{index}",
                "case_type": "verified",
                "source": "incident-db",
                "issue_type": "communication",
            },
        }
        for index in range(3)
    ]
    result = synthesize_diagnosis(
        [
            *invalid_rag,
            {
                "source_type": "rag_chunk",
                "content": "PM delay playbook",
                "metadata": {"issue_types": "pm_delay"},
            },
            *invalid_cases,
            {
                "source_type": "similar_case",
                "content": "verified down incident",
                "metadata": {
                    "case_id": "INC-VALID",
                    "case_type": "verified",
                    "source": "incident-db",
                    "issue_type": "equipment_down",
                    "verification": VERIFICATION,
                },
            },
        ]
    )

    assert result["metadata"]["candidate_issue_types"] == [
        "equipment_down",
        "pm_delay",
    ]
    assert result["metadata"]["excluded_evidence"] == {
        "rag_without_diagnostic_issue": 3,
        "rag_issue_mismatch": 0,
        "case_without_diagnostic_issue": 3,
    }
    assert result["metadata"]["truncated_evidence"] == {
        "eligible_rag_after_top_k": 0,
        "eligible_cases_after_top_k": 0,
    }
def test_document_structure_tags_are_not_promoted_to_cause_candidates() -> None:
    result = synthesize_diagnosis(
        [
            {
                "source_type": "rag_chunk",
                "title": "mixed metadata",
                "content": "reference section",
                "metadata": {
                    "issue_types": "equipment_down,example,references,all,communication"
                },
            }
        ]
    )

    assert result["metadata"]["candidate_issue_types"] == ["equipment_down"]
    assert result["metadata"]["candidate_causes"][0]["issue_types"] == [
        "equipment_down"
    ]


def test_diagnosis_uses_query_matched_issue_types_from_compound_rag_chunk() -> None:
    result = synthesize_diagnosis(
        [
            {
                "source_type": "rag_chunk",
                "title": "Hot lot and queue playbooks",
                "content": "issue_type hot_lot; issue_type queue_time_risk",
                "metadata": {
                    "issue_type": "hot_lot",
                    "declared_issue_types": ["hot_lot", "queue_time_risk"],
                    "query_issue_intents": ["queue_time"],
                    "matched_issue_types": ["queue_time_risk"],
                    "issue_aligned": True,
                },
            }
        ]
    )

    assert result["metadata"]["candidate_issue_types"] == ["queue_time_risk"]
    assert result["metadata"]["candidate_causes"][0]["issue_types"] == [
        "queue_time_risk"
    ]


def test_diagnosis_excludes_rag_evidence_marked_as_issue_mismatch() -> None:
    result = synthesize_diagnosis(
        [
            {
                "source_type": "rag_chunk",
                "title": "Mismatched playbook",
                "content": "equipment down guide",
                "metadata": {
                    "issue_types": "equipment_down",
                    "issue_aligned": False,
                },
            }
        ]
    )

    assert result["metadata"]["candidate_causes"] == []
    assert result["metadata"]["excluded_evidence"]["rag_issue_mismatch"] == 1


def test_case_structure_tags_are_not_promoted_to_cause_candidates() -> None:
    result = synthesize_diagnosis(
        [
            {
                "source_type": "similar_case",
                "title": "invalid taxonomy",
                "content": "reference",
                "metadata": {
                    "case_id": "INC-STRUCTURE",
                    "case_type": "verified",
                    "source": "incident-db",
                    "issue_type": "communication",
                },
            }
        ]
    )

    assert result["metadata"]["candidate_issue_types"] == []
    assert result["metadata"]["similar_cases"] == []
    assert result["metadata"]["source_coverage"]["similar_cases"] is False
    assert result["metadata"]["excluded_evidence"]["case_without_diagnostic_issue"] == 1


def test_synthetic_verified_label_is_not_treated_as_verified_corroboration() -> None:
    result = synthesize_diagnosis(
        [
            {
                "source_type": "similar_case",
                "title": "mislabeled synthetic case",
                "content": "generated case",
                "metadata": {
                    "case_id": "SYN-001",
                    "case_type": "verified",
                    "source": "synthetic-evaluation-corpus-v1",
                    "issue_type": "station_down",
                    "verification": VERIFICATION,
                },
            }
        ]
    )

    reliability = result["metadata"]["reliability_assessment"]
    assert result["metadata"]["candidate_issue_types"] == ["equipment_down"]
    assert reliability["corroboration_level"] == "unverified_reference_only"
    assert reliability["verified_case_count"] == 0
    assert reliability["unverified_case_count"] == 1
    assert reliability["can_confirm_root_cause"] is False
    assert "verified_incident_match" in result["metadata"]["required_verification"]


def test_self_declared_verified_case_without_verification_is_downgraded() -> None:
    result = synthesize_diagnosis(
        [
            {
                "source_type": "similar_case",
                "title": "unverified incident label",
                "content": "incident case without review provenance",
                "metadata": {
                    "case_id": "INC-NO-REVIEW",
                    "case_type": "verified",
                    "source": "incident-system",
                    "issue_type": "equipment_down",
                },
            }
        ]
    )

    reliability = result["metadata"]["reliability_assessment"]
    assert reliability["corroboration_level"] == "unverified_reference_only"
    assert reliability["verified_case_count"] == 0
    assert reliability["unverified_case_count"] == 1
    assert "verified_incident_match" in result["metadata"]["required_verification"]
