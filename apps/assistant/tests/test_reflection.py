import pytest
from app.sub_agent.reflection import (
    reflect_agent_output,
    required_question_context,
    verify_response,
)


def test_agent_reflection_passes_evidence_backed_text2sql_result() -> None:
    result = reflect_agent_output(
        agent_name="text2sql",
        agent_intent="Query fab10 WIP.",
        planner_plan={"intent": "Check WIP", "query_type": "status"},
        agent_output={
            "status": "succeeded",
            "summary": "Returned one WIP row.",
            "sql": "SELECT wip FROM fab10.autosched_wip_fab10 LIMIT 1",
        },
        success_criteria=["Return read-only SQL and result evidence."],
        evidence=[{"source_type": "text2sql_plan"}],
        limitations=[],
        required=True,
    )

    assert result["decision"] == "pass"
    assert result["recommended_action"] == "continue"


def test_agent_reflection_sends_failed_required_agent_to_supervisor() -> None:
    result = reflect_agent_output(
        agent_name="text2sql",
        agent_intent="Query fab10 WIP.",
        planner_plan={"intent": "Check WIP", "query_type": "status"},
        agent_output={"status": "data_unavailable", "summary": "Report is not loaded."},
        success_criteria=["Return read-only SQL and result evidence."],
        evidence=[],
        limitations=["AutoSched report is unavailable."],
        required=True,
    )

    assert result["decision"] == "needs_supervisor_review"
    assert result["recommended_action"] == "supervisor_review"
    assert "Required agent" in result["reason"]


def test_reflection_warns_when_rag_only_diagnosis_confirms_root_cause() -> None:
    result = verify_response(
        "실제 원인은 병목 설비입니다.",
        evidence=[
            {
                "source_type": "rag_chunk",
                "metadata": {"knowledge_base": "incident_playbook"},
            }
        ],
        limitations=[],
        query_type="diagnosis",
    )

    assert "RAG evidence alone cannot prove the actual root cause." in result["warnings"]
    assert "Diagnosis answers require a candidate-only evidence synthesis." in result["warnings"]


def test_reflection_accepts_calibrated_diagnosis_synthesis() -> None:
    result = verify_response(
        "관측값과 문서를 함께 보면 병목 설비가 원인 후보지만 실제 원인은 확정할 수 없습니다.",
        evidence=[
            {
                "source_type": "text2sql_plan",
                "metadata": {"status": "succeeded", "sql": "SELECT 1"},
            },
            {
                "source_type": "diagnosis_synthesis",
                "metadata": {
                    "conclusion_level": "candidate_only",
                    "candidate_issue_types": ["bottleneck"],
                },
            },
        ],
        limitations=["추가 운영 로그 확인 필요"],
        query_type="diagnosis",
    )

    assert result["is_supported"] is True


def test_diagnosis_answer_requires_highest_ranked_candidate() -> None:
    evidence = [
        {
            "source_type": "diagnosis_synthesis",
            "metadata": {
                "conclusion_level": "candidate_only",
                "candidate_issue_types": ["equipment_down", "queue_time"],
                "candidate_rankings": [
                    {"issue_type": "equipment_down", "support_level": "strong_candidate"},
                    {"issue_type": "queue_time", "support_level": "weak_candidate"},
                ],
                "reliability_assessment": {"can_confirm_root_cause": False},
            },
        }
    ]

    omitted = verify_response(
        "대기 시간이 원인 후보지만 실제 원인은 확정할 수 없습니다.",
        evidence=evidence,
        query_type="diagnosis",
    )
    preserved = verify_response(
        "장비 고장이 우선 원인 후보지만 실제 원인은 확정할 수 없습니다.",
        evidence=evidence,
        query_type="diagnosis",
    )

    warning = "Diagnosis answers must preserve the highest-ranked cause candidate."
    assert warning in omitted["warnings"]
    assert warning not in preserved["warnings"]


def test_reflection_requires_simulated_case_disclosure() -> None:
    evidence = [
        {
            "source_type": "text2sql_plan",
            "metadata": {"status": "succeeded", "sql": "SELECT 1"},
        },
        {
            "source_type": "similar_case",
            "metadata": {"case_type": "simulated_reference"},
        },
            {
                "source_type": "diagnosis_synthesis",
                "metadata": {
                    "conclusion_level": "candidate_only",
                    "candidate_issue_types": ["bottleneck"],
                },
            },
    ]

    omitted = verify_response(
        "병목이 원인 후보이며 실제 원인은 확정할 수 없습니다.",
        evidence=evidence,
        limitations=["추가 로그 필요"],
        query_type="diagnosis",
    )
    disclosed = verify_response(
        "시뮬레이션 참고 사례상 병목이 원인 후보이며 실제 원인은 확정할 수 없습니다.",
        evidence=evidence,
        limitations=["추가 로그 필요"],
        query_type="diagnosis",
    )

    assert "Diagnosis answers must disclose simulated reference sources." in omitted["warnings"]
    assert disclosed["is_supported"] is True


def test_reflection_requires_simulated_playbook_disclosure_without_case() -> None:
    evidence = [
        {
            "source_type": "text2sql_plan",
            "metadata": {"status": "succeeded", "sql": "SELECT 1"},
        },
        {
            "source_type": "diagnosis_synthesis",
            "metadata": {
                "conclusion_level": "candidate_only",
                "reliability_assessment": {"simulated_knowledge_count": 1},
            },
        },
    ]

    result = verify_response(
        "병목은 원인 후보이며 실제 원인은 확정할 수 없습니다.",
        evidence=evidence,
        limitations=["추가 로그 필요"],
        query_type="diagnosis",
    )

    assert "Diagnosis answers must disclose simulated reference sources." in result["warnings"]


def test_reflection_warns_when_incident_playbook_sounds_executable() -> None:
    result = verify_response(
        "장비를 정지하고 바로 hold를 실행하세요.",
        evidence=[
            {
                "source_type": "rag_chunk",
                "metadata": {"knowledge_base": "incident_playbook"},
            }
        ],
        limitations=["운영자 검토 필요"],
        query_type="knowledge_lookup",
    )

    assert any("Incident playbook" in warning for warning in result["warnings"])


def test_reflection_warns_when_numeric_claim_lacks_sql() -> None:
    result = verify_response(
        "현재 WIP는 128개입니다.",
        evidence=[
            {
                "source_type": "rag_chunk",
                "metadata": {"knowledge_base": "process_basics"},
            }
        ],
        limitations=[],
        query_type="status",
    )

    assert "Numeric operational claims require SQL evidence." in result["warnings"]


def test_reflection_does_not_accept_failed_text2sql_as_numeric_evidence() -> None:
    result = verify_response(
        "현재 WIP는 128개입니다.",
        evidence=[
            {
                "source_type": "text2sql_plan",
                "metadata": {"status": "failed", "sql": None},
            }
        ],
        limitations=["DB 조회 실패"],
        query_type="status",
    )

    assert "Numeric operational claims require SQL evidence." in result["warnings"]


def test_reflection_does_not_accept_empty_sql_result_as_numeric_evidence() -> None:
    result = verify_response(
        "WIP는 128개입니다.",
        evidence=[
            {
                "source_type": "text2sql_plan",
                "metadata": {
                    "status": "succeeded",
                    "sql": "SELECT wiplotavg FROM fab10.autosched_perf_fab10 WHERE FALSE",
                    "row_count": 0,
                    "sample_rows": [],
                },
            }
        ],
        limitations=["조건에 맞는 행 없음"],
        query_type="status",
    )

    assert "Numeric operational claims require SQL evidence." in result["warnings"]


def test_fab_identifier_is_not_misread_as_numeric_wip_claim() -> None:
    result = verify_response(
        "fab10 WIP 데이터는 현재 조회할 수 없습니다.",
        evidence=[
            {
                "source_type": "text2sql_plan",
                "metadata": {"status": "data_unavailable", "sql": None},
            }
        ],
        limitations=["AutoSched report unavailable"],
        query_type="status",
        question="fab10 WIP 알려줘",
    )

    assert "Numeric operational claims require SQL evidence." not in result["warnings"]


def test_final_answer_alignment_detects_omitted_identifier_and_metric() -> None:
    result = verify_response(
        "현재 상태를 확인했습니다.",
        evidence=[
            {
                "source_type": "text2sql_plan",
                "metadata": {"status": "succeeded", "sql": "SELECT 1"},
            }
        ],
        limitations=[],
        query_type="status",
        question="fab10 Product_3 현재 WIP 알려줘",
    )

    assert result["is_supported"] is False
    assert result["missing_identifiers"] == ["fab10", "Product_3"]
    assert result["missing_metrics"] == ["WIP"]


def test_status_answer_preserves_threshold_and_top_n_constraints() -> None:
    evidence = [
        {
            "source_type": "text2sql_plan",
            "metadata": {
                "status": "succeeded",
                "sql": "SELECT stngrp, util_percent FROM fab10.autosched_stngrp_fab10",
                "row_count": 1,
                "sample_rows": [{"stngrp": "Dry_Etch_A", "util_percent": 82.5}],
            },
        }
    ]
    question = "fab10 Dry_Etch utilization 80% 이상 설비군 상위 5개"

    omitted = verify_response(
        "fab10 Dry_Etch utilization 첫 값은 82.5%입니다.",
        evidence=evidence,
        query_type="status",
        question=question,
    )
    preserved = verify_response(
        "fab10 Dry_Etch utilization 80% 이상 상위 5개 조건의 첫 값은 82.5%입니다.",
        evidence=evidence,
        query_type="status",
        question=question,
    )

    assert omitted["missing_selection_constraints"] == ["80% 이상", "상위 5"]
    assert omitted["quality_dimensions"]["question_alignment"] is False
    assert preserved["missing_selection_constraints"] == []
    assert preserved["is_supported"] is True


def test_status_answer_preserves_symbolic_english_and_qualitative_constraints() -> None:
    evidence = [
        {
            "source_type": "text2sql_plan",
            "metadata": {
                "status": "succeeded",
                "sql": "SELECT util_percent FROM fab10.autosched_stngrp_fab10",
                "row_count": 1,
                "sample_rows": [{"stngrp": "Dry_Etch", "util_percent": 82.5}],
            },
        }
    ]

    omitted = verify_response(
        "fab10 Dry_Etch utilization 첫 값은 82.5%입니다.",
        evidence=evidence,
        query_type="status",
        question="fab10 Dry_Etch utilization >= 80 설비군 높은 순",
    )
    equivalent = verify_response(
        "fab10 Dry_Etch utilization 80 이상 설비군을 내림차순으로 조회한 첫 값은 82.5%입니다.",
        evidence=evidence,
        query_type="status",
        question="fab10 Dry_Etch utilization >= 80 설비군 높은 순",
    )
    english_omitted = verify_response(
        "fab10 Dry_Etch utilization 첫 값은 82.5%입니다.",
        evidence=evidence,
        query_type="status",
        question="fab10 Dry_Etch utilization at least 80 percent 설비군",
    )

    assert omitted["missing_selection_constraints"] == [">= 80", "높은 순"]
    assert omitted["is_supported"] is False
    assert equivalent["missing_selection_constraints"] == []
    assert equivalent["is_supported"] is True
    assert english_omitted["missing_selection_constraints"] == ["at least 80percent"]


def test_final_answer_alignment_detects_omitted_explicit_and_relative_dates() -> None:
    explicit = verify_response(
        "fab10 WIP 일별 추세입니다.",
        evidence=[{"source_type": "text2sql_plan", "metadata": {"status": "succeeded"}}],
        limitations=[],
        query_type="trend",
        question="fab10 WIP 2020-01-01부터 2020-01-07까지 일별 추세",
    )
    relative = verify_response(
        "fab10 Product_3 cycle time 비교입니다.",
        evidence=[{"source_type": "text2sql_plan", "metadata": {"status": "succeeded"}}],
        limitations=[],
        query_type="trend",
        question="fab10 Product_3 최근 7일 cycle time 비교",
    )

    assert explicit["missing_dates"] == ["2020-01-01", "2020-01-07"]
    assert relative["missing_dates"] == ["최근 7일"]
    assert explicit["quality_dimensions"]["question_alignment"] is False


def test_alignment_normalizes_spaced_identifiers_and_slash_dates() -> None:
    grounded = verify_response(
        "fab10 DE_BE_11 WIP의 2020-01-01부터 2020-01-07까지 추세입니다.",
        evidence=[{"source_type": "text2sql_plan", "metadata": {"status": "succeeded"}}],
        limitations=[],
        query_type="trend",
        question="FAB 10 DE-BE-11 WIP 2020/01/01부터 2020/01/07까지 추세",
    )
    omitted = verify_response(
        "WIP 추세입니다.",
        evidence=[{"source_type": "text2sql_plan", "metadata": {"status": "succeeded"}}],
        limitations=[],
        query_type="trend",
        question="FAB 10 DE-BE-11 WIP 2020/01/01부터 2020/01/07까지 추세",
    )

    assert grounded["missing_identifiers"] == []
    assert grounded["missing_dates"] == []
    assert omitted["missing_identifiers"] == ["fab10", "DE_BE_11"]
    assert omitted["missing_dates"] == ["2020/01/01", "2020/01/07"]


def test_alignment_requires_last_week_phrase() -> None:
    result = verify_response(
        "fab10 Product_3 cycle time 비교입니다.",
        evidence=[{"source_type": "text2sql_plan", "metadata": {"status": "succeeded"}}],
        limitations=[],
        query_type="trend",
        question="FAB-10 Product 3 지난 일주일 cycle time 비교",
    )

    assert result["missing_dates"] == ["지난 일주일"]


def test_alignment_requires_calendar_month_and_quarter_context() -> None:
    month = verify_response(
        "fab10 WIP 추세입니다.",
        evidence=[{"source_type": "text2sql_plan", "metadata": {"status": "succeeded"}}],
        limitations=[],
        query_type="trend",
        question="fab10 2020년 2월 WIP 추세",
    )
    quarter = verify_response(
        "fab10 WIP 월별 추세입니다.",
        evidence=[{"source_type": "text2sql_plan", "metadata": {"status": "succeeded"}}],
        limitations=[],
        query_type="trend",
        question="fab10 2020년 1분기부터 2분기까지 WIP 월별 추세",
    )

    assert month["missing_dates"] == ["2020년 2월"]
    assert quarter["missing_dates"] == ["2020년 1분기", "2분기"]


def test_alignment_accepts_equivalent_quarter_notation() -> None:
    result = verify_response(
        "fab10 WIP의 2020 Q1부터 Q2까지 월별 추세입니다.",
        evidence=[{"source_type": "text2sql_plan", "metadata": {"status": "succeeded"}}],
        limitations=[],
        query_type="trend",
        question="fab10 2020년 1분기부터 2분기까지 WIP 월별 추세",
    )

    assert result["missing_dates"] == []


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("fab10 지난 분기 WIP 추세", "지난 분기"),
        ("fab10 this quarter WIP trend", "this quarter"),
        ("fab10 last month WIP trend", "last month"),
    ],
)
def test_alignment_requires_relative_month_and_quarter_context(
    question: str, expected: str
) -> None:
    result = verify_response(
        "fab10 WIP 추세입니다.",
        evidence=[{"source_type": "text2sql_plan", "metadata": {"status": "succeeded"}}],
        limitations=[],
        query_type="trend",
        question=question,
    )

    assert result["missing_dates"] == [expected]


def test_required_question_context_reuses_alignment_canonicalization() -> None:
    context = required_question_context(
        "FAB-10 DE-BE-11 WIP과 ontime 2020년 1분기 월별 추세"
    )

    assert context == ["fab10", "DE_BE_11", "WIP", "Ontime", "2020년 1분기"]


def test_status_answer_requires_a_requested_metric_value_from_rows() -> None:
    evidence = [
        {
            "source_type": "text2sql_plan",
            "metadata": {
                "status": "succeeded",
                "sql": "SELECT wiplotavg FROM fab10.autosched_perf_fab10",
                "row_count": 1,
                "sample_rows": [{"wiplotavg": 128.5}],
            },
        }
    ]
    omitted = verify_response(
        "fab10 WIP를 조회했습니다.",
        evidence=evidence,
        limitations=[],
        query_type="status",
        question="fab10 WIP 알려줘",
    )
    grounded = verify_response(
        "fab10 WIP는 128.5입니다.",
        evidence=evidence,
        limitations=[],
        query_type="status",
        question="fab10 WIP 알려줘",
    )

    assert any("requested metric value" in warning for warning in omitted["warnings"])
    assert omitted["quality_dimensions"]["evidence_grounding"] is False
    assert grounded["is_supported"] is True
    assert grounded["quality_score"] == 1.0


def test_metric_value_does_not_match_digits_inside_fab_identifier() -> None:
    result = verify_response(
        "fab10 WIP를 조회했습니다.",
        evidence=[
            {
                "source_type": "text2sql_plan",
                "metadata": {
                    "status": "succeeded",
                    "sql": "SELECT wiplotavg FROM fab10.autosched_perf_fab10",
                    "row_count": 1,
                    "sample_rows": [{"wiplotavg": 1}],
                },
            }
        ],
        limitations=[],
        query_type="status",
        question="fab10 WIP 알려줘",
    )

    assert any("requested metric value" in warning for warning in result["warnings"])


def test_status_answer_requires_every_requested_metric_value() -> None:
    evidence = [
        {
            "source_type": "text2sql_plan",
            "metadata": {
                "status": "succeeded",
                "sample_rows": [
                    {"part": "part_3", "wiplotavg": 128.5, "cycleavg": 10.2}
                ],
            },
        }
    ]

    result = verify_response(
        "fab10 Product_3 현재 WIP 128.5이며 cycle time도 조회했습니다.",
        evidence=evidence,
        limitations=[],
        query_type="status",
        question="fab10 Product_3 현재 WIP과 cycle time 알려줘",
    )

    assert result["missing_result_metrics"] == ["Cycle Time"]
    assert result["missing_result_targets"] == ["Product_3 Cycle Time"]
    assert result["is_supported"] is False


def test_status_answer_requires_metric_values_for_each_requested_target() -> None:
    evidence = [
        {
            "source_type": "text2sql_plan",
            "metadata": {
                "status": "succeeded",
                "sample_rows": [
                    {"part": "part_3", "wiplotavg": 128.5, "cycleavg": 10.2},
                    {"part": "part_4", "wiplotavg": 140.0, "cycleavg": 11.4},
                ],
            },
        }
    ]
    answer = (
        "fab10 Product_3 WIP 128.5, cycle time 10.2입니다. "
        "Product_4 WIP 140.0이며 cycle time도 조회했습니다."
    )

    result = verify_response(
        answer,
        evidence=evidence,
        limitations=[],
        query_type="status",
        question="fab10 Product_3과 Product_4 현재 WIP과 cycle time 알려줘",
    )

    assert result["missing_result_metrics"] == []
    assert result["missing_result_targets"] == ["Product_4 Cycle Time"]
    assert result["is_supported"] is False


def test_status_answer_rejects_numeric_claim_absent_from_sql_results() -> None:
    result = verify_response(
        "fab10 Product_3 WIP는 128.5이고 전일 대비 999% 증가했습니다. AutoSched report 기준입니다.",
        evidence=[
            {
                "source_type": "text2sql_plan",
                "metadata": {
                    "status": "succeeded",
                    "sql": "SELECT part, wiplotavg FROM fab10.autosched_part_fab10",
                    "row_count": 1,
                    "sample_rows": [{"part": "part_3", "wiplotavg": 128.5}],
                },
            }
        ],
        limitations=["AutoSched report 기준입니다."],
        query_type="status",
        question="fab10 Product_3 현재 WIP 알려줘",
    )

    assert result["unsupported_numeric_claims"] == ["999"]
    assert result["quality_dimensions"]["evidence_grounding"] is False
    assert result["is_supported"] is False


def test_status_answer_allows_numeric_condition_supplied_by_question() -> None:
    result = verify_response(
        "fab10 Product_3 WIP는 128.5이며 요청한 100 이상 조건을 충족합니다. AutoSched report 기준입니다.",
        evidence=[
            {
                "source_type": "text2sql_plan",
                "metadata": {
                    "status": "succeeded",
                    "sql": "SELECT part, wiplotavg FROM fab10.autosched_part_fab10",
                    "row_count": 1,
                    "sample_rows": [{"part": "part_3", "wiplotavg": 128.5}],
                },
            }
        ],
        limitations=["AutoSched report 기준입니다."],
        query_type="status",
        question="fab10 Product_3 현재 WIP가 100 이상인지 알려줘",
    )

    assert result["unsupported_numeric_claims"] == []
    assert result["is_supported"] is True


def test_chart_claim_requires_successful_visualization_evidence() -> None:
    sql_evidence = {
        "source_type": "text2sql_plan",
        "metadata": {
            "status": "succeeded",
            "sql": "SELECT report_time, wiplotavg FROM fab10.autosched_perf_fab10",
            "row_count": 2,
            "sample_rows": [
                {"report_time": "2020-01-01", "wiplotavg": 10},
                {"report_time": "2020-01-02", "wiplotavg": 11},
            ],
        },
    }
    ungrounded = verify_response(
        "fab10 WIP 그래프를 생성했습니다. AutoSched report 기준입니다.",
        evidence=[sql_evidence],
        limitations=["AutoSched report 기준입니다."],
        query_type="trend",
        question="fab10 WIP 그래프로 보여줘",
    )
    grounded = verify_response(
        "fab10 WIP 그래프를 생성했습니다. AutoSched report 기준입니다.",
        evidence=[
            sql_evidence,
            {
                "source_type": "visualization_spec",
                "metadata": {"status": "succeeded", "chart_type": "line"},
            },
        ],
        limitations=["AutoSched report 기준입니다."],
        query_type="trend",
        question="fab10 WIP 그래프로 보여줘",
    )

    assert ungrounded["chart_claim_grounded"] is False
    assert ungrounded["is_supported"] is False
    assert grounded["chart_claim_grounded"] is True
    assert grounded["is_supported"] is True


def test_chart_failure_passes_only_when_answer_discloses_unavailability() -> None:
    result = verify_response(
        "fab10 WIP 그래프는 데이터 형식 문제로 생성할 수 없습니다. AutoSched report 기준입니다.",
        evidence=[
            {
                "source_type": "text2sql_plan",
                "metadata": {
                    "status": "succeeded",
                    "sql": "SELECT report_time, wiplotavg FROM fab10.autosched_perf_fab10",
                    "row_count": 1,
                    "sample_rows": [{"report_time": "bad", "wiplotavg": 10}],
                },
            }
        ],
        limitations=["데이터 형식 문제로 차트를 생성할 수 없습니다."],
        query_type="trend",
        question="fab10 WIP 그래프로 보여줘",
    )

    assert result["chart_claim_grounded"] is True
    assert result["is_supported"] is True


def test_trend_answer_allows_deterministic_summary_values() -> None:
    result = verify_response(
        "fab10 WIP는 2020-01-01의 10에서 2020-01-02의 12로 20% 증가했습니다. "
        "line 그래프이며 AutoSched report 기준입니다.",
        evidence=[
            {
                "source_type": "text2sql_plan",
                "metadata": {
                    "status": "succeeded",
                    "sql": "SELECT report_date, wiplotavg FROM fab10.autosched_perf_fab10",
                    "row_count": 2,
                    "sample_rows": [
                        {"report_date": "2020-01-01", "wiplotavg": 10},
                        {"report_date": "2020-01-02", "wiplotavg": 12},
                    ],
                },
            },
            {
                "source_type": "visualization_spec",
                "metadata": {
                    "status": "succeeded",
                    "chart_type": "line",
                    "trend_summary": [
                        {
                            "series": "wiplotavg",
                            "start_value": 10,
                            "end_value": 12,
                            "absolute_delta": 2,
                            "percent_delta": 20,
                        }
                    ],
                },
            },
        ],
        limitations=["AutoSched report 기준입니다."],
        query_type="trend",
        question="fab10 WIP를 2020-01-01부터 2020-01-02까지 그래프로 보여줘",
    )

    assert result["unsupported_numeric_claims"] == []
    assert result["is_supported"] is True


def test_trend_answer_rejects_numeric_claim_absent_from_rows_and_summary() -> None:
    result = verify_response(
        "fab10 WIP는 2020-01-01의 10에서 2020-01-02의 12로 999% 증가했습니다. "
        "line 그래프이며 AutoSched report 기준입니다.",
        evidence=[
            {
                "source_type": "text2sql_plan",
                "metadata": {
                    "status": "succeeded",
                    "sql": "SELECT report_date, wiplotavg FROM fab10.autosched_perf_fab10",
                    "row_count": 2,
                    "sample_rows": [
                        {"report_date": "2020-01-01", "wiplotavg": 10},
                        {"report_date": "2020-01-02", "wiplotavg": 12},
                    ],
                },
            },
            {
                "source_type": "visualization_spec",
                "metadata": {
                    "status": "succeeded",
                    "chart_type": "line",
                    "trend_summary": [
                        {
                            "series": "wiplotavg",
                            "start_value": 10,
                            "end_value": 12,
                            "absolute_delta": 2,
                            "percent_delta": 20,
                        }
                    ],
                },
            },
        ],
        limitations=["AutoSched report 기준입니다."],
        query_type="trend",
        question="fab10 WIP를 2020-01-01부터 2020-01-02까지 그래프로 보여줘",
    )

    assert result["unsupported_numeric_claims"] == ["999"]
    assert result["is_supported"] is False


def test_trend_answer_rejects_direction_opposite_to_percent_delta() -> None:
    result = verify_response(
        "fab10 WIP는 2020-01-01의 10에서 2020-01-02의 12로 20% 감소했습니다. "
        "line 그래프이며 AutoSched report 기준입니다.",
        evidence=[
            {
                "source_type": "text2sql_plan",
                "metadata": {
                    "status": "succeeded",
                    "sql": "SELECT report_date, wiplotavg FROM fab10.autosched_perf_fab10",
                    "row_count": 2,
                    "sample_rows": [
                        {"report_date": "2020-01-01", "wiplotavg": 10},
                        {"report_date": "2020-01-02", "wiplotavg": 12},
                    ],
                },
            },
            {
                "source_type": "visualization_spec",
                "metadata": {
                    "status": "succeeded",
                    "chart_type": "line",
                    "trend_summary": [
                        {
                            "series": "wiplotavg",
                            "start_value": 10,
                            "end_value": 12,
                            "absolute_delta": 2,
                            "percent_delta": 20,
                        }
                    ],
                },
            },
        ],
        limitations=["AutoSched report 기준입니다."],
        query_type="trend",
        question="fab10 WIP를 2020-01-01부터 2020-01-02까지 그래프로 보여줘",
    )

    assert result["trend_direction_conflicts"] == [
        "wiplotavg percent_delta=20 described as decrease"
    ]
    assert result["is_supported"] is False


def test_impact_answer_rejects_numeric_claim_absent_from_calculation() -> None:
    result = verify_response(
        "fab10 입력 baseline utilization 80%를 기준으로 계산한 capacity 변화는 -6.25%이며 "
        "생산량도 999% 증가합니다. utilization 1차 비례 가정이며 실제 인과 모델에는 한계가 있습니다.",
        evidence=[
            {
                "source_type": "text2sql_plan",
                "metadata": {
                    "status": "succeeded",
                    "sql": "SELECT util_percent FROM fab10.autosched_stngrp_fab10",
                    "row_count": 1,
                    "sample_rows": [{"util_percent": 80}],
                },
            },
            {
                "source_type": "impact_calculation",
                "metadata": {
                    "status": "succeeded",
                    "inputs": {"baseline_util_percent": 80},
                    "estimates": {"capacity_delta_percent": -6.25},
                    "formulae": ["capacity_delta_percent = delta / baseline * 100"],
                    "assumptions": ["first order"],
                },
            },
        ],
        limitations=["utilization 1차 비례 가정입니다."],
        query_type="impact",
        question="fab10 utilization이 5%p 떨어지면 capacity 영향은?",
    )

    assert result["unsupported_numeric_claims"] == ["999"]
    assert result["quality_dimensions"]["evidence_grounding"] is False
    assert result["is_supported"] is False


def test_impact_answer_allows_formula_constant_and_model_order() -> None:
    result = verify_response(
        "fab10 입력 baseline utilization 80%와 100 분모를 기준으로 계산한 capacity 변화는 "
        "-6.25%입니다. utilization 1차 비례 가정이며 실제 인과 모델에는 한계가 있습니다.",
        evidence=[
            {
                "source_type": "text2sql_plan",
                "metadata": {
                    "status": "succeeded",
                    "sql": "SELECT util_percent FROM fab10.autosched_stngrp_fab10",
                    "row_count": 1,
                    "sample_rows": [{"util_percent": 80}],
                },
            },
            {
                "source_type": "impact_calculation",
                "metadata": {
                    "status": "succeeded",
                    "inputs": {"baseline_util_percent": 80},
                    "estimates": {"capacity_delta_percent": -6.25},
                    "formulae": ["capacity_delta_percent = delta / baseline * 100"],
                    "assumptions": ["first order"],
                },
            },
        ],
        limitations=["utilization 1차 비례 가정입니다."],
        query_type="impact",
        question="fab10 utilization이 5%p 떨어지면 capacity 영향은?",
    )

    assert result["unsupported_numeric_claims"] == []
    assert result["is_supported"] is True


def test_impact_answer_rejects_direction_opposite_to_estimate_sign() -> None:
    evidence = [
        {
            "source_type": "text2sql_plan",
            "metadata": {
                "status": "succeeded",
                "sql": "SELECT util_percent FROM fab10.autosched_stngrp_fab10",
                "row_count": 1,
                "sample_rows": [{"util_percent": 80}],
            },
        },
        {
            "source_type": "impact_calculation",
            "metadata": {
                "status": "succeeded",
                "inputs": {"baseline_util_percent": 80},
                "estimates": {"capacity_delta_percent": -6.25},
                "formulae": ["capacity_delta_percent = delta / baseline * 100"],
                "assumptions": ["first order"],
            },
        },
    ]
    result = verify_response(
        "fab10 입력 baseline utilization 80%를 기준으로 계산한 capacity는 -6.25% 증가합니다. "
        "utilization 1차 비례 가정이며 실제 인과 모델에는 한계가 있습니다.",
        evidence=evidence,
        limitations=["utilization 1차 비례 가정입니다."],
        query_type="impact",
        question="fab10 utilization이 5%p 떨어지면 capacity 영향은?",
    )

    assert result["impact_direction_conflicts"] == [
        "capacity_delta_percent=-6.25 described as increase"
    ]
    assert result["is_supported"] is False


def test_impact_answer_accepts_unsigned_magnitude_with_matching_direction() -> None:
    result = verify_response(
        "fab10 입력 baseline utilization 80%를 기준으로 계산한 capacity는 6.25% 감소합니다. "
        "utilization 1차 비례 가정이며 실제 인과 모델에는 한계가 있습니다.",
        evidence=[
            {
                "source_type": "text2sql_plan",
                "metadata": {
                    "status": "succeeded",
                    "sql": "SELECT util_percent FROM fab10.autosched_stngrp_fab10",
                    "row_count": 1,
                    "sample_rows": [{"util_percent": 80}],
                },
            },
            {
                "source_type": "impact_calculation",
                "metadata": {
                    "status": "succeeded",
                    "inputs": {"baseline_util_percent": 80},
                    "estimates": {"capacity_delta_percent": -6.25},
                    "formulae": ["capacity_delta_percent = delta / baseline * 100"],
                    "assumptions": ["first order"],
                },
            },
        ],
        limitations=["utilization 1차 비례 가정입니다."],
        query_type="impact",
        question="fab10 utilization이 5%p 떨어지면 capacity 영향은?",
    )

    assert result["impact_direction_conflicts"] == []
    assert result["is_supported"] is True


def test_impact_direction_uses_nearest_term_in_compound_sentence() -> None:
    evidence = [
        {
            "source_type": "text2sql_plan",
            "metadata": {
                "status": "succeeded",
                "sql": "SELECT util_percent FROM fab10.autosched_stngrp_fab10",
                "row_count": 1,
                "sample_rows": [{"util_percent": 80}],
            },
        },
        {
            "source_type": "impact_calculation",
            "metadata": {
                "status": "succeeded",
                "inputs": {"baseline_util_percent": 80},
                "estimates": {"capacity_delta_percent": -6.25},
                "formulae": ["capacity_delta_percent = delta / baseline * 100"],
                "assumptions": ["first order"],
            },
        },
    ]
    result = verify_response(
        "fab10 입력 baseline utilization 80%에서 5%p 감소하면 capacity는 -6.25% 증가합니다. "
        "1차 비례 계산 가정이며 실제 인과 모델에는 한계가 있습니다.",
        evidence=evidence,
        limitations=["1차 비례 가정"],
        query_type="impact",
        question="fab10 utilization이 5%p 떨어지면 capacity 영향은?",
    )

    assert result["impact_direction_conflicts"] == [
        "capacity_delta_percent=-6.25 described as increase"
    ]
    assert result["is_supported"] is False


def test_diagnosis_answer_rejects_numeric_claim_absent_from_evidence() -> None:
    result = verify_response(
        "fab10 DE_BE_11 down 12.5%는 관측값입니다. SIM-001 참고 사례상 equipment down은 "
        "원인 후보지만 추가 이상률 999%는 확인됐고 실제 원인은 확정할 수 없습니다.",
        evidence=[
            {
                "source_type": "text2sql_plan",
                "metadata": {
                    "status": "succeeded",
                    "sql": "SELECT down_percent FROM fab10.autosched_stn_fab10",
                    "row_count": 1,
                    "sample_rows": [{"stn": "DE_BE_11", "down_percent": 12.5}],
                },
            },
            {
                "source_type": "similar_case",
                "content": "simulation",
                "metadata": {
                    "case_id": "SIM-001",
                    "case_type": "simulated_reference",
                },
            },
            {
                "source_type": "diagnosis_synthesis",
                "content": "candidate",
                "metadata": {
                    "conclusion_level": "candidate_only",
                    "candidate_issue_types": ["equipment_down"],
                    "reliability_assessment": {
                        "simulated_case_count": 1,
                        "can_confirm_root_cause": False,
                    },
                },
            },
        ],
        limitations=["실제 이벤트 로그 확인이 필요합니다."],
        query_type="diagnosis",
        question="fab10 DE_BE_11 down 원인을 찾아줘",
    )

    assert result["unsupported_numeric_claims"] == ["999"]
    assert result["is_supported"] is False


def test_diagnosis_answer_allows_evidence_value_and_case_identifier() -> None:
    result = verify_response(
        "fab10 DE_BE_11 down 12.5%는 관측값입니다. SIM-001 시뮬레이션 참고 사례상 "
        "equipment down은 원인 후보지만 실제 원인은 확정할 수 없습니다.",
        evidence=[
            {
                "source_type": "text2sql_plan",
                "metadata": {
                    "status": "succeeded",
                    "sql": "SELECT down_percent FROM fab10.autosched_stn_fab10",
                    "row_count": 1,
                    "sample_rows": [{"stn": "DE_BE_11", "down_percent": 12.5}],
                },
            },
            {
                "source_type": "similar_case",
                "content": "simulation",
                "metadata": {
                    "case_id": "SIM-001",
                    "case_type": "simulated_reference",
                },
            },
            {
                "source_type": "diagnosis_synthesis",
                "content": "candidate",
                "metadata": {
                    "conclusion_level": "candidate_only",
                    "candidate_issue_types": ["equipment_down"],
                    "reliability_assessment": {
                        "simulated_case_count": 1,
                        "can_confirm_root_cause": False,
                    },
                },
            },
        ],
        limitations=["실제 이벤트 로그 확인이 필요합니다."],
        query_type="diagnosis",
        question="fab10 DE_BE_11 down 원인을 찾아줘",
    )

    assert result["unsupported_numeric_claims"] == []
    assert result["is_supported"] is True


def test_impact_limitation_array_cannot_replace_visible_answer_boundary() -> None:
    result = verify_response(
        "capacity는 6.25% 감소합니다.",
        evidence=[
            {
                "source_type": "impact_calculation",
                "metadata": {
                    "status": "succeeded",
                    "inputs": {"baseline_util_percent": 80},
                    "estimates": {"capacity_delta_percent": -6.25},
                    "formulae": ["delta formula"],
                },
            },
            {
                "source_type": "text2sql_plan",
                "metadata": {
                    "status": "succeeded",
                    "sql": "SELECT util_percent FROM fab10.autosched_stngrp_fab10",
                    "row_count": 1,
                    "sample_rows": [{"util_percent": 80}],
                },
            },
        ],
        limitations=["1차 비례 가정"],
        query_type="impact",
        question="capacity 영향은?",
    )

    assert "Impact answers must include input-data and calculation limitations." in result["warnings"]
    assert "Material limitations must be visible in the final answer." in result["warnings"]
    assert result["quality_dimensions"]["limitation_visibility"] is False


def test_impact_answer_requires_mixed_baseline_dimension_and_remediation() -> None:
    evidence = [
        {
            "source_type": "impact_calculation",
            "metadata": {
                "status": "data_unavailable",
                "baseline": {"mixed_dimensions": ["product"]},
                "provenance": {"mixed_dimensions": ["product"]},
                "estimates": {},
                "limitations": ["서로 다른 product baseline이 섞여 있습니다."],
            },
        }
    ]
    limitations = ["서로 다른 product baseline이 섞여 있습니다."]

    hidden = verify_response(
        "fab10 Product_3과 Product_4 utilization 데이터가 부족해 capacity 영향은 계산할 수 없습니다.",
        evidence=evidence,
        limitations=limitations,
        query_type="impact",
        question="fab10 Product_3과 Product_4 utilization의 capacity 영향은?",
    )
    disclosed = verify_response(
        "fab10 Product_3과 Product_4의 product별 utilization baseline을 분리해야 해서 "
        "단일 capacity 영향은 계산할 수 없습니다.",
        evidence=evidence,
        limitations=limitations,
        query_type="impact",
        question="fab10 Product_3과 Product_4 utilization의 capacity 영향은?",
    )

    assert hidden["missing_mixed_impact_dimensions"] == ["product"]
    assert hidden["is_supported"] is False
    assert disclosed["missing_mixed_impact_dimensions"] == []
    assert disclosed["is_supported"] is True


def test_supervisor_requires_each_supported_compound_impact_estimate() -> None:
    evidence = [
        {
            "source_type": "text2sql_plan",
            "metadata": {
                "status": "succeeded",
                "sql": "SELECT util_percent, cycleavg FROM fab10.autosched_perf_fab10",
                "row_count": 1,
                "sample_rows": [{"util_percent": 80, "cycleavg": 10}],
            },
        },
        {
            "source_type": "impact_calculation",
            "metadata": {
                "status": "succeeded",
                "scenario": {
                    "parsed_changes": [
                        {"metric": "utilization", "value": 5, "unit": "percentage_point", "direction": -1},
                        {"metric": "cycle_time", "value": 8, "unit": "percent", "direction": 1},
                    ]
                },
                "inputs": {"baseline_util_percent": 80, "baseline_cycle_time": 10},
                "estimates": {
                    "capacity_delta_percent": -6.25,
                    "projected_cycle_time": 10.8,
                },
                "formulae": ["capacity formula", "cycle formula"],
                "assumptions": ["first order"],
                "limitations": ["ontime elasticity unavailable"],
            },
        },
    ]
    question = (
        "fab10 utilization이 5%p 감소하고 cycle time이 8% 증가하면 "
        "capacity와 납기 영향은?"
    )
    complete_answer = (
        "fab10 입력 baseline utilization 80%, cycle time 10 기준으로 capacity는 6.25% "
        "감소하고 projected cycle time은 10.8입니다. 1차 계산 가정이며 ontime 탄력성 "
        "모델이 없어 납기 영향은 산출할 수 없습니다."
    )
    incomplete_answer = (
        "fab10 입력 baseline utilization 80%, cycle time 10 기준으로 capacity는 6.25% "
        "감소합니다. 1차 계산 가정이며 ontime 탄력성 모델이 없어 납기 영향은 "
        "산출할 수 없습니다."
    )

    complete = verify_response(
        complete_answer,
        evidence=evidence,
        limitations=["ontime elasticity unavailable"],
        query_type="impact",
        question=question,
    )
    incomplete = verify_response(
        incomplete_answer,
        evidence=evidence,
        limitations=["ontime elasticity unavailable"],
        query_type="impact",
        question=question,
    )

    assert complete["is_supported"] is True
    assert complete["missing_impact_estimates"] == []
    assert incomplete["is_supported"] is False
    assert incomplete["missing_impact_estimates"] == ["Cycle Time"]


def test_supervisor_validates_each_long_equipment_target_metric_value() -> None:
    evidence = [
        {
            "source_type": "text2sql_plan",
            "metadata": {
                "status": "succeeded",
                "sql": "SELECT stn, util_percent, down_percent FROM fab10.autosched_stn_fab10",
                "row_count": 2,
                "sample_rows": [
                    {"stn": "DE_BE_11_2", "util_percent": 80, "down_percent": 5},
                    {"stn": "DE_BE_12_3", "util_percent": 70, "down_percent": 9},
                ],
            },
        }
    ]
    question = "fab10 DE_BE_11_2와 DE_BE_12_3 utilization과 down 알려줘"

    complete = verify_response(
        "fab10 DE_BE_11_2 utilization 80%, down 5%, DE_BE_12_3 utilization 70%, down 9%입니다.",
        evidence=evidence,
        query_type="status",
        question=question,
    )
    incomplete = verify_response(
        "fab10 DE_BE_11_2 utilization 80%, down 5%, DE_BE_12_3 utilization 70%입니다.",
        evidence=evidence,
        query_type="status",
        question=question,
    )

    assert complete["is_supported"] is True
    assert complete["missing_result_targets"] == []
    assert incomplete["is_supported"] is False
    assert incomplete["missing_result_targets"] == ["DE_BE_12_3 Down"]


def test_supervisor_requires_each_equipment_metric_in_multi_series_trend_answer() -> None:
    evidence = [
        {
            "source_type": "text2sql_plan",
            "metadata": {
                "status": "succeeded",
                "sql": "SELECT report_date, stn, util_percent, down_percent FROM fab10.autosched_stn_fab10",
                "row_count": 4,
                "sample_rows": [
                    {"stn": "DE_BE_11", "util_percent": 80, "down_percent": 5},
                    {"stn": "DE_BE_11", "util_percent": 82, "down_percent": 4},
                    {"stn": "DE_BE_12", "util_percent": 75, "down_percent": 8},
                    {"stn": "DE_BE_12", "util_percent": 73, "down_percent": 9},
                ],
            },
        },
        {
            "source_type": "visualization_spec",
            "metadata": {
                "status": "succeeded",
                "chart_type": "line",
                "trend_summary": [
                    {"series": "DE_BE_11 / util_percent", "start_value": 80, "end_value": 82, "absolute_delta": 2, "percent_delta": 2.5},
                    {"series": "DE_BE_11 / down_percent", "start_value": 5, "end_value": 4, "absolute_delta": -1, "percent_delta": -20},
                    {"series": "DE_BE_12 / util_percent", "start_value": 75, "end_value": 73, "absolute_delta": -2, "percent_delta": -2.6667},
                    {"series": "DE_BE_12 / down_percent", "start_value": 8, "end_value": 9, "absolute_delta": 1, "percent_delta": 12.5},
                ],
            },
        },
    ]
    question = "fab10 DE_BE_11과 DE_BE_12 utilization과 down 추세 알려줘"

    complete = verify_response(
        "fab10 추세에서 DE_BE_11 utilization은 80에서 82로 증가했고 down은 5에서 4로 감소했습니다. DE_BE_12 utilization은 75에서 73으로 감소했고 down은 8에서 9로 증가했습니다.",
        evidence=evidence,
        query_type="trend",
        question=question,
    )
    incomplete = verify_response(
        "fab10 추세에서 DE_BE_11 utilization은 80에서 82로 증가했고 down은 5에서 4로 감소했습니다. DE_BE_12 utilization은 75에서 73으로 감소했고 down 추세도 확인했습니다.",
        evidence=evidence,
        query_type="trend",
        question=question,
    )

    assert complete["is_supported"] is True
    assert complete["missing_trend_series"] == []
    assert incomplete["is_supported"] is False
    assert incomplete["missing_trend_series"] == ["DE_BE_12 Down"]
