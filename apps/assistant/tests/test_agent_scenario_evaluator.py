import sys
from dataclasses import replace

from app.agents.planner import create_plan
from app.agents.supervisor import AgentRun, SupervisorResult
from app.schemas.chat import Evidence
from scripts.evaluate_agent_scenarios import evaluate_result, main, summarize


def _result(**overrides):
    result = SupervisorResult(
        conversation_id="test",
        status="succeeded",
        query_type="trend",
        answer="fab10 Product_3 WIP 추세입니다.",
        sql="SELECT report_time, wiplotcur FROM fab10.autosched_part_fab10",
        chart={"type": "line"},
        plan=create_plan("fab10 Product_3 WIP 추세 보여줘"),
        agent_runs=[
            AgentRun(agent="text2sql", status="succeeded", summary="ok"),
            AgentRun(agent="visualization", status="succeeded", summary="ok"),
        ],
        agent_reflections=[
            {"agent_name": "text2sql", "decision": "pass"},
            {"agent_name": "visualization", "decision": "pass"},
        ],
        answer_review={"approved": True, "issues": []},
        evidence=[
            Evidence(
                source_type="text2sql_plan",
                title="query",
                content="rows",
                metadata={
                    "status": "succeeded",
                    "sql": "SELECT report_time, wiplotcur FROM fab10.autosched_part_fab10",
                    "row_count": 1,
                    "sample_rows": [{"wiplotcur": 12}],
                },
            )
        ],
    )
    return replace(result, **overrides)


def test_evaluate_result_splits_routing_execution_and_answer_quality() -> None:
    case = {
        "id": "trend",
        "question": "fab10 Product_3 WIP 추세 보여줘",
        "expected_query_type": "trend",
        "expected_agents": ["text2sql", "visualization"],
        "expected_current_status": "succeeded",
        "expected_sql_contains": ["autosched_part"],
        "target_chart_type": "line",
    }

    evaluation = evaluate_result(case, _result())

    assert evaluation["passed"] is True
    assert evaluation["routing_ok"] is True
    assert evaluation["execution_ok"] is True
    assert evaluation["answer_ok"] is True
    assert evaluation["answer_quality"]["score"] == 1.0


def test_evaluate_result_checks_semantic_and_evidence_contracts() -> None:
    case = {
        "id": "diagnosis",
        "question": "fab10 Product_3 ontime 원인",
        "expected_query_type": "trend",
        "expected_agents": ["text2sql", "visualization"],
        "expected_current_status": "succeeded",
        "target_source_tables": ["fab10.autosched_part_fab10"],
        "target_columns": ["ontime_percent"],
        "target_evidence_types": ["text2sql_plan", "rag_chunk"],
        "target_date_basis": "due_date",
    }
    query_plan = {
        "slots": {"date_basis": {"value": "due_date"}},
        "source_tables": ["fab10.autosched_part_fab10"],
    }
    result = _result(
        evidence=[
            Evidence(
                source_type="text2sql_plan",
                title="query",
                content="rows",
                metadata={"query_plan": query_plan, "columns": ["ontime_percent"]},
            )
        ]
    )

    evaluation = evaluate_result(case, result)

    assert evaluation["execution_ok"] is False
    assert any(
        "evidence type expected=rag_chunk" in failure
        for failure in evaluation["execution_failures"]
    )


def test_summarize_reports_agent_success_and_reflection_rates() -> None:
    result = _result()
    evaluation = {
        "routing_ok": True,
        "execution_ok": True,
        "answer_ok": True,
        "passed": True,
    }

    summary = summarize([evaluation], [result])

    assert summary["metrics"]["answer"]["rate"] == 1.0
    assert summary["agents"]["text2sql"]["success_rate"] == 1.0
    assert summary["agents"]["visualization"]["reflection_pass_rate"] == 1.0
    assert summary["average_answer_quality_score"] == 0.0


def test_evaluate_result_enforces_impact_calculation_contract() -> None:
    case = {
        "id": "impact",
        "question": "utilization 5%p 감소 영향",
        "expected_query_type": "impact",
        "expected_agents": ["text2sql", "impact"],
        "expected_current_status": "succeeded",
    }
    result = _result(
        query_type="impact",
        plan=create_plan("utilization이 5%p 감소하면 capacity 영향은?", fab="fab10"),
        answer="입력 기준으로 계산한 capacity 영향이며 1차 비례 가정입니다.",
        agent_runs=[
            AgentRun(agent="text2sql", status="succeeded", summary="ok"),
            AgentRun(agent="impact", status="succeeded", summary="ok"),
        ],
        evidence=[
            Evidence(
                source_type="impact_calculation",
                title="impact",
                content="calculation",
                metadata={"status": "succeeded", "estimates": {"capacity": -5}},
            )
        ],
    )

    evaluation = evaluate_result(case, result)

    assert evaluation["execution_ok"] is False
    assert "impact calculation missing inputs" in evaluation["execution_failures"]
    assert "impact calculation missing formulae" in evaluation["execution_failures"]


def test_evaluate_result_requires_diagnosis_reliability_assessment() -> None:
    case = {
        "id": "diagnosis",
        "question": "fab10 병목 원인",
        "expected_query_type": "diagnosis",
        "expected_agents": ["text2sql", "rag", "case_search"],
        "expected_current_status": "succeeded",
    }
    result = _result(
        query_type="diagnosis",
        plan=create_plan("fab10 병목 원인"),
        answer="병목은 원인 후보이며 실제 원인은 확정할 수 없습니다.",
        agent_runs=[
            AgentRun(agent="text2sql", status="succeeded", summary="ok"),
            AgentRun(agent="rag", status="succeeded", summary="ok"),
            AgentRun(agent="case_search", status="succeeded", summary="ok"),
        ],
        evidence=[
            Evidence(
                source_type="diagnosis_synthesis",
                title="synthesis",
                content="candidate only",
                metadata={"conclusion_level": "candidate_only"},
            )
        ],
    )

    evaluation = evaluate_result(case, result)

    assert evaluation["execution_ok"] is False
    assert "diagnosis reliability assessment is missing" in evaluation["execution_failures"]


def test_evaluator_rejects_false_positive_answer_supervisor_approval() -> None:
    case = {
        "id": "status",
        "question": "fab10 Product_3 현재 WIP 알려줘",
        "expected_query_type": "status",
        "expected_agents": ["text2sql"],
        "expected_current_status": "succeeded",
    }
    result = _result(
        query_type="status",
        plan=create_plan(case["question"]),
        answer="현재 상태를 확인했습니다.",
        agent_runs=[AgentRun(agent="text2sql", status="succeeded", summary="ok")],
        answer_review={"approved": True, "issues": []},
    )

    evaluation = evaluate_result(case, result)

    assert evaluation["answer_ok"] is False
    assert evaluation["answer_quality"]["dimensions"]["question_alignment"] is False
    assert any("omits requested identifiers" in item for item in evaluation["answer_failures"])


def test_scenario_evaluator_requires_explicit_live_opt_in(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["evaluate_agent_scenarios.py"])

    assert main() == 2
    assert "without --live" in capsys.readouterr().err
