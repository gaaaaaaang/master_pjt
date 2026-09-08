from pathlib import Path

import pytest
from app.agents.graph import _text2sql_query_type
from app.agents.llm_nodes import compose_with_llm
from app.agents.planner import create_plan
from app.agents.supervisor import (
    Supervisor,
    review_agent_result,
    review_final_answer,
    review_plan,
)
from app.config import get_settings
from app.schemas.chat import ChatRequest, Evidence
from app.sub_agent.rag import PROCESS_BASICS, EvidenceResult
from app.sub_agent.reflection import verify_response
from app.sub_agent.text2sql import QueryPlan, Text2SQLResult


class RecordingLLM:
    def __init__(self, output) -> None:
        self.output = output
        self.calls = []

    def complete_json(self, **kwargs):
        self.calls.append(kwargs)
        return self.output


class FailingLLM:
    def complete_json(self, **kwargs):
        del kwargs
        raise RuntimeError("temporary LLM outage")


def test_planner_fallback_routes_context_metric_bare_selection_to_status() -> None:
    plan = create_plan(
        "그중 80% 이상만 상위 5개",
        fab="fab10",
        process="Dry_Etch",
        metric="util_percent",
        llm_client=FailingLLM(),
    )

    assert plan.status == "ready"
    assert plan.query_type == "status"
    assert plan.selected_sub_agents == ["text2sql"]
    assert plan.slots["metric"].value == "util_percent"


def test_deterministic_composer_preserves_question_scope(monkeypatch) -> None:
    monkeypatch.setattr("app.agents.llm_nodes.AzureAgentClient", lambda: FailingLLM())
    question = "fab10 WIP과 ontime 2020년 1분기 월별 추세 보여줘"
    plan = create_plan(question, llm_client=FailingLLM())
    evidence = [
        {
            "source_type": "text2sql_plan",
            "title": "query",
            "content": "result",
            "metadata": {
                "status": "succeeded",
                "sql": "SELECT report_month, wiplotavg, ontime_percent FROM fab10.autosched_perf",
                "row_count": 1,
                "sample_rows": [
                    {
                        "report_month": "2020-01-01",
                        "wiplotavg": 2273.83,
                        "ontime_percent": 85.39,
                    }
                ],
            },
        }
    ]
    limitations = ["AutoSched report 기준입니다."]

    answer = compose_with_llm(
        question=question,
        plan=plan,
        answer_parts=["AutoSched report 추세 기준으로 1개 행을 조회했습니다."],
        evidence=evidence,
        limitations=limitations,
        reflection={"action": "compose"},
    )
    verification = verify_response(
        answer,
        evidence=evidence,
        limitations=limitations,
        query_type="trend",
        question=question,
    )

    assert "요청 범위: fab10, WIP, Ontime, 2020년 1분기" in answer
    assert "wiplotavg=2273.83" in answer
    assert verification["is_supported"] is True


def test_deterministic_composer_covers_multi_equipment_trend_series(monkeypatch) -> None:
    monkeypatch.setattr("app.agents.llm_nodes.AzureAgentClient", lambda: FailingLLM())
    question = "fab10 DE_BE_11과 DE_BE_12 utilization과 down 추세 알려줘"
    plan = create_plan(question, llm_client=FailingLLM())
    rows = [
        {"report_date": "2020-01-01", "stn": "DE_BE_11", "util_percent": 80, "down_percent": 5},
        {"report_date": "2020-01-02", "stn": "DE_BE_11", "util_percent": 82, "down_percent": 4},
        {"report_date": "2020-01-01", "stn": "DE_BE_12", "util_percent": 75, "down_percent": 8},
        {"report_date": "2020-01-02", "stn": "DE_BE_12", "util_percent": 73, "down_percent": 9},
    ]
    evidence = [
        {
            "source_type": "text2sql_plan",
            "metadata": {
                "status": "succeeded",
                "sql": "SELECT report_date, stn, util_percent, down_percent FROM fab10.autosched_stn",
                "row_count": 4,
                "sample_rows": rows,
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

    answer = compose_with_llm(
        question=question,
        plan=plan,
        answer_parts=["AutoSched report 장비별 추세를 조회했습니다."],
        evidence=evidence,
        limitations=[],
        reflection={"action": "compose"},
    )
    verification = verify_response(
        answer,
        evidence=evidence,
        query_type="trend",
        question=question,
    )

    assert plan.selected_sub_agents == ["text2sql", "visualization"]
    assert verification["missing_trend_series"] == []
    assert verification["is_supported"] is True


def test_planner_uses_chat_completions_structured_output() -> None:
    llm = RecordingLLM(
        {
            "status": "ready",
            "query_type": "master_data_lookup",
            "intent": "lookup toolgroups",
            "fab_id": "fab10",
            "rag_knowledge_base": None,
            "missing_slots": [],
            "selected_sub_agents": ["text2sql"],
            "execution_steps": [
                {
                    "agent": "text2sql",
                    "action": "generate SQL",
                    "required": True,
                    "reason": "database evidence is required",
                }
            ],
            "clarification_question": None,
            "limitations": [],
        }
    )

    plan = create_plan("fab10 toolgroup 조회", llm_client=llm)

    assert plan.query_type == "master_data_lookup"
    assert llm.calls[0]["schema_name"] == "fab_planner_decision"
    assert llm.calls[0]["input_data"]["question"] == "fab10 toolgroup 조회"


def test_planner_uses_deterministic_route_when_llm_is_unavailable() -> None:
    plan = create_plan("왜 fab10 Queue Time이 늘었어?", llm_client=FailingLLM())

    assert plan.execution_mode == "deterministic_fallback"
    assert plan.query_type == "diagnosis"
    assert plan.selected_sub_agents == ["text2sql", "rag", "case_search"]
    assert all(step.required is False for step in plan.execution_steps)


@pytest.mark.parametrize(
    ("question", "expected_type", "expected_agents"),
    [
        ("지금 fab10 WIP 몇 개야?", "status", ["text2sql"]),
        ("fab10 현재 WIP가 뭐야?", "status", ["text2sql"]),
        (
            "왜 fab10 Dry_Etch 병목이 생겼어?",
            "diagnosis",
            ["text2sql", "rag", "case_search"],
        ),
        ("fab10 utilization이 5%p 떨어지면 capacity 영향은?", "impact", ["text2sql", "impact"]),
        (
            "fab10 Product_3와 Product_4를 비교해줘",
            "trend",
            ["text2sql", "visualization"],
        ),
        ("CMP 공정이 뭐야?", "knowledge_lookup", ["rag"]),
    ],
)
def test_deterministic_fallback_covers_primary_scenario_routes(
    question: str,
    expected_type: str,
    expected_agents: list[str],
) -> None:
    plan = create_plan(question, llm_client=FailingLLM())

    assert plan.status == "ready"
    assert plan.query_type == expected_type
    assert plan.selected_sub_agents == expected_agents


def test_deterministic_fallback_clarifies_ambiguous_release_date_basis() -> None:
    plan = create_plan(
        "fab10 Product_3 lotrelease 일별 추세 보여줘",
        llm_client=FailingLLM(),
    )

    assert plan.status == "needs_clarification"
    assert plan.missing_slots == ["date_basis"]
    assert plan.selected_sub_agents == []


@pytest.mark.parametrize(
    ("question", "expected_agents"),
    [
        (
            "fab10 Dry_Etch 병목 원인과 utilization 5%p 감소 영향도 알려줘",
            ["text2sql", "rag", "case_search", "impact"],
        ),
        (
            "fab10 WIP 추세와 증가 원인을 같이 분석해줘",
            ["text2sql", "rag", "case_search", "visualization"],
        ),
        (
            "fab10 지난 분기 WIP 흐름과 병목 이유, capacity 영향까지 보여줘",
            ["text2sql", "rag", "case_search", "impact", "visualization"],
        ),
    ],
)
def test_deterministic_fallback_preserves_explicit_compound_agents(
    question: str, expected_agents: list[str]
) -> None:
    plan = create_plan(question, llm_client=FailingLLM())

    assert plan.query_type == "diagnosis"
    assert plan.selected_sub_agents == expected_agents


def test_deterministic_impact_fallback_adds_requested_visualization() -> None:
    plan = create_plan(
        "fab10 utilization 5%p 감소 영향을 전후 비교 차트로 보여줘",
        llm_client=FailingLLM(),
    )

    assert plan.query_type == "impact"
    assert plan.selected_sub_agents == ["text2sql", "impact", "visualization"]


def test_compound_diagnosis_selects_downstream_compatible_text2sql_intent() -> None:
    assert (
        _text2sql_query_type(
            "diagnosis", ["text2sql", "rag", "case_search", "visualization"]
        )
        == "trend"
    )
    assert (
        _text2sql_query_type(
            "diagnosis", ["text2sql", "rag", "case_search", "impact"]
        )
        == "status"
    )


def test_planner_receives_execution_feedback_for_replan() -> None:
    llm = RecordingLLM(
        {
            "status": "ready",
            "query_type": "master_data_lookup",
            "intent": "revised toolgroup lookup",
            "fab_id": "fab10",
            "rag_knowledge_base": None,
            "missing_slots": [],
            "selected_sub_agents": ["text2sql"],
            "execution_steps": [
                {
                    "agent": "text2sql",
                    "action": "generate a narrower SQL query",
                    "required": True,
                    "reason": "the first query failed",
                }
            ],
            "clarification_question": None,
            "limitations": [],
        }
    )
    feedback = [{"agent_name": "text2sql", "planner_feedback": "narrow the query"}]

    create_plan("fab10 toolgroup 조회", execution_feedback=feedback, llm_client=llm)

    assert llm.calls[0]["input_data"]["execution_feedback"] == feedback


def test_planner_receives_follow_up_request_context() -> None:
    llm = RecordingLLM(
        {
            "status": "ready",
            "query_type": "status",
            "intent": "retrieve process WIP",
            "fab_id": "fab10",
            "rag_knowledge_base": None,
            "missing_slots": [],
            "selected_sub_agents": ["text2sql"],
            "execution_steps": [
                {
                    "agent": "text2sql",
                    "action": "query process WIP",
                    "required": True,
                    "reason": "database evidence",
                }
            ],
            "clarification_question": None,
            "limitations": [],
        }
    )

    plan = create_plan(
        "그 공정 WIP도 보여줘",
        fab="fab10",
        line="M10",
        process="Dry_Etch",
        product="Product_3",
        route="Route_Product_3",
        equipment="DE_BE_11",
        date_basis="due_date",
        llm_client=llm,
    )

    assert llm.calls[0]["input_data"]["request_fab"] == "fab10"
    assert llm.calls[0]["input_data"]["request_line"] == "M10"
    assert llm.calls[0]["input_data"]["request_process"] == "Dry_Etch"
    assert llm.calls[0]["input_data"]["request_product"] == "Product_3"
    assert llm.calls[0]["input_data"]["request_route"] == "Route_Product_3"
    assert llm.calls[0]["input_data"]["request_equipment"] == "DE_BE_11"
    assert llm.calls[0]["input_data"]["request_date_basis"] == "due_date"
    assert plan.slots["process"].value == "Dry_Etch"


def test_planner_receives_negative_feedback_in_history() -> None:
    llm = RecordingLLM(
        {
            "status": "ready",
            "query_type": "status",
            "intent": "revise WIP answer with period context",
            "fab_id": "fab10",
            "rag_knowledge_base": None,
            "missing_slots": [],
            "selected_sub_agents": ["text2sql"],
            "execution_steps": [
                {
                    "agent": "text2sql",
                    "action": "query WIP with report period",
                    "required": True,
                    "reason": "negative feedback requested period context",
                }
            ],
            "clarification_question": None,
            "limitations": [],
        }
    )
    history = [
        {
            "role": "assistant",
            "content": "fab10 WIP은 2264.75입니다.",
            "metadata": {
                "user_feedback": [
                    {"helpful": False, "comment": "기준 기간이 없습니다.", "trace_id": None}
                ]
            },
        }
    ]

    create_plan(
        "다시 설명해줘",
        fab="fab10",
        conversation_history=history,
        llm_client=llm,
    )

    assert llm.calls[0]["input_data"]["conversation_history"] == history


def test_planner_restores_required_diagnosis_agents_when_llm_omits_them() -> None:
    llm = RecordingLLM(
        {
            "status": "ready",
            "query_type": "diagnosis",
            "intent": "diagnose queue increase",
            "fab_id": "fab10",
            "rag_knowledge_base": "incident_playbook",
            "missing_slots": [],
            "selected_sub_agents": ["text2sql"],
            "execution_steps": [
                {
                    "agent": "text2sql",
                    "action": "query metrics",
                    "required": True,
                    "reason": "SQL evidence",
                }
            ],
            "clarification_question": None,
            "limitations": [],
        }
    )

    plan = create_plan("왜 fab10 Queue Time이 늘었어?", llm_client=llm)

    assert plan.selected_sub_agents == ["text2sql", "rag", "case_search"]
    assert [step.agent for step in plan.execution_steps] == plan.selected_sub_agents
    assert all(step.required is False for step in plan.execution_steps)


def test_planner_preserves_compatible_compound_agent_from_llm_plan() -> None:
    llm = RecordingLLM(
        {
            "status": "ready",
            "query_type": "diagnosis",
            "intent": "diagnose bottleneck and calculate utilization impact",
            "fab_id": "fab10",
            "rag_knowledge_base": "incident_playbook",
            "missing_slots": [],
            "selected_sub_agents": ["impact", "case_search", "text2sql", "rag"],
            "execution_steps": [
                {
                    "agent": "impact",
                    "action": "calculate requested utilization change",
                    "required": True,
                    "reason": "compound impact request",
                }
            ],
            "clarification_question": None,
            "limitations": [],
        }
    )

    plan = create_plan(
        "fab10 병목 원인과 utilization 5%p 감소 영향을 알려줘",
        llm_client=llm,
    )

    assert plan.selected_sub_agents == ["text2sql", "rag", "case_search", "impact"]
    assert [step.agent for step in plan.execution_steps] == plan.selected_sub_agents
    assert plan.execution_steps[-1].required is True


def test_planner_restores_explicit_compound_agents_omitted_by_llm() -> None:
    llm = RecordingLLM(
        {
            "status": "ready",
            "query_type": "diagnosis",
            "intent": "diagnose bottleneck",
            "fab_id": "fab10",
            "rag_knowledge_base": "incident_playbook",
            "missing_slots": [],
            "selected_sub_agents": ["text2sql", "rag", "case_search"],
            "execution_steps": [],
            "clarification_question": None,
            "limitations": [],
        }
    )

    plan = create_plan(
        "fab10 지난 분기 WIP 흐름과 병목 이유, capacity 영향까지 보여줘",
        llm_client=llm,
    )

    assert plan.selected_sub_agents == [
        "text2sql",
        "rag",
        "case_search",
        "impact",
        "visualization",
    ]
    assert [step.agent for step in plan.execution_steps] == plan.selected_sub_agents
    assert plan.execution_steps[-2].required is True
    assert plan.execution_steps[-1].required is True


def test_supervisor_uses_independent_chat_completions_review() -> None:
    plan = create_plan("fab10 toolgroup 조회")
    llm = RecordingLLM(
        {
            "proceed": True,
            "status": "ready",
            "selected_sub_agents": ["text2sql"],
            "reason": "plan is executable",
            "answer": None,
            "limitations": [],
        }
    )

    reviewed, decision = review_plan(plan, "fab10 toolgroup 조회", llm_client=llm)

    assert reviewed.selected_sub_agents == ["text2sql"]
    assert decision["proceed"] is True
    assert llm.calls[0]["schema_name"] == "fab_supervisor_decision"


def test_supervisor_cannot_silently_drop_agents_from_approved_plan() -> None:
    plan = create_plan("왜 fab10 Queue Time이 늘었어?")
    llm = RecordingLLM(
        {
            "proceed": True,
            "status": "ready",
            "selected_sub_agents": ["text2sql"],
            "reason": "approved but incomplete selection",
            "answer": None,
            "limitations": [],
        }
    )

    reviewed, _ = review_plan(plan, "왜 fab10 Queue Time이 늘었어?", llm_client=llm)

    assert reviewed.selected_sub_agents == ["text2sql", "rag", "case_search"]
    assert [step.agent for step in reviewed.execution_steps] == reviewed.selected_sub_agents


def test_answer_supervisor_rejects_omitted_request_terms_and_accepts_correction() -> None:
    plan = create_plan("fab10 Product_3 현재 WIP 알려줘")
    llm = RecordingLLM(
        {
            "approved": True,
            "issues": [],
            "corrected_answer": "fab10 Product_3의 현재 WIP는 조회 결과 기준으로 확인했습니다.",
            "reason": "The draft omitted the requested target and metric.",
        }
    )

    review = review_final_answer(
        question="fab10 Product_3 현재 WIP 알려줘",
        answer="현재 상태를 확인했습니다.",
        plan=plan,
        evidence=[
            {
                "source_type": "text2sql_plan",
                "metadata": {"status": "succeeded", "sql": "SELECT wiplotcur FROM fab10.autosched_part"},
            }
        ],
        limitations=[],
        llm_client=llm,
    )

    assert review["original_approved"] is False
    assert review["approved"] is True
    assert review["correction_applied"] is True
    assert review["correction_check"]["is_supported"] is True
    assert llm.calls[0]["schema_name"] == "fab_answer_supervisor_decision"


def test_answer_supervisor_does_not_apply_unsupported_correction() -> None:
    plan = create_plan("fab10 Product_3 현재 WIP 알려줘")
    llm = RecordingLLM(
        {
            "approved": False,
            "issues": ["The answer omits the requested product and metric."],
            "corrected_answer": "현재 상태를 확인했습니다.",
            "reason": "The proposed correction is still incomplete.",
        }
    )

    review = review_final_answer(
        question="fab10 Product_3 현재 WIP 알려줘",
        answer="조회 결과가 없습니다.",
        plan=plan,
        evidence=[],
        limitations=["AutoSched 데이터가 없습니다."],
        llm_client=llm,
    )

    assert review["approved"] is False
    assert review["correction_applied"] is False
    assert review["correction_check"]["is_supported"] is False


def test_recovery_policy_does_not_retry_data_unavailable() -> None:
    plan = create_plan("fab10 toolgroup 조회")
    llm = RecordingLLM(
        {
            "action": "retry_same_agent",
            "alternate_agent": None,
            "reason": "try again",
            "planner_feedback": "change the data source",
            "limitations": [],
        }
    )

    decision = review_agent_result(
        plan,
        {"agent_name": "text2sql", "status": "data_unavailable"},
        retry_count=0,
        retry_budget_remaining=2,
        replan_budget_remaining=1,
        alternate_budget_remaining=1,
        allowed_alternate_agents=[],
        llm_client=llm,
    )

    assert decision["action"] == "replan"
    assert "bounded recovery policy" in decision["reason"]


def test_planner_routes_master_lookup_to_text2sql() -> None:
    plan = create_plan("fab10 Dry_Etch toolgroup 목록 보여줘")

    assert plan.status == "ready"
    assert plan.query_type == "master_data_lookup"
    assert plan.selected_sub_agents == ["text2sql"]
    assert plan.execution_steps[0].agent == "text2sql"


def test_planner_routes_diagnosis_to_agent_combination() -> None:
    plan = create_plan("왜 fab10 Queue Time이 늘었어?")

    assert plan.status == "ready"
    assert plan.query_type == "diagnosis"
    assert plan.selected_sub_agents == ["text2sql", "rag", "case_search"]
    assert plan.rag_knowledge_base == "incident_playbook"


def test_planner_routes_process_basics_to_rag_only_without_fab() -> None:
    plan = create_plan("CMP 공정이 뭐야?")

    assert plan.status == "ready"
    assert plan.query_type == "knowledge_lookup"
    assert plan.selected_sub_agents == ["rag"]
    assert plan.rag_knowledge_base == PROCESS_BASICS


def test_planner_routes_release_count_chart_to_text2sql_and_visualization() -> None:
    plan = create_plan(
        "fab10의 lotrelease 테이블에서 route_product_3 건수를 날짜 기준으로 라인차트로 그려줘."
    )

    assert plan.status == "ready"
    assert plan.query_type == "trend"
    assert plan.selected_sub_agents == ["text2sql", "visualization"]


def test_planner_missing_fab_returns_clarification_plan() -> None:
    plan = create_plan("Dry_Etch toolgroup 목록 보여줘")

    assert plan.status == "needs_clarification"
    assert plan.missing_slots == ["fab_id"]
    assert plan.clarification_question is not None


def test_supervisor_status_stops_on_data_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.agents.graph.answer_question",
        lambda *args, **kwargs: Text2SQLResult(
            status="data_unavailable",
            query_type="status",
            answer="AutoSched report 적재 후 활성화해야 합니다.",
            limitations=[
                "현재 PostgreSQL에는 AutoSched report table(autosched_*)이 적재되어 있지 않습니다."
            ],
            plan=QueryPlan(
                query_type="status",
                template_id=None,
                fab_id="fab10",
                data_source_type="operational_report",
            ),
        ),
    )

    result = Supervisor().run(ChatRequest(message="지금 fab10 WIP 몇 개야?"))

    assert result.status == "data_unavailable"
    assert result.query_type == "status"
    assert result.sql is None
    assert any(run.agent == "text2sql" for run in result.agent_runs)
    assert result.agent_reflections[0]["agent_name"] == "text2sql"
    assert result.agent_reflections[0]["decision"] == "needs_supervisor_review"
    assert result.supervisor_reviews[0]["agent_name"] == "text2sql"
    assert result.supervisor_reviews[0]["resolution"] == "continue"
    assert result.supervisor_decisions[0]["action"] == "continue"
    assert result.reflection["agent_reflections"] == result.agent_reflections
    assert "AutoSched" in " ".join(result.limitations)


def test_graph_returns_structured_result_when_all_orchestration_llm_calls_fail(
    monkeypatch,
) -> None:
    def fail_completion(*args, **kwargs):
        del args, kwargs
        raise RuntimeError("temporary LLM outage")

    monkeypatch.setattr("app.agents.llm.AzureAgentClient.complete_json", fail_completion)
    monkeypatch.setattr(
        "app.agents.graph.answer_question",
        lambda *args, **kwargs: Text2SQLResult(
            status="data_unavailable",
            query_type="status",
            answer="fab10의 현재 WIP는 AutoSched 데이터가 없어 확인할 수 없습니다.",
            limitations=["AutoSched operational report is unavailable."],
            plan=QueryPlan(
                query_type="status",
                template_id=None,
                fab_id="fab10",
                data_source_type="operational_report",
            ),
        ),
    )

    result = Supervisor().run(ChatRequest(message="지금 fab10 WIP 몇 개야?"))

    assert result.status == "data_unavailable"
    assert result.plan is not None
    assert result.plan.execution_mode == "deterministic_fallback"
    assert result.supervisor_decisions[0]["fallback_used"] is True
    assert result.reflection["fallback_used"] is True
    assert result.answer_review["fallback_used"] is True
    assert "fab10" in result.answer
    assert "WIP" in result.answer


def test_supervisor_master_lookup_returns_planner_and_text2sql_evidence(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.agents.graph.answer_question",
        lambda *args, **kwargs: Text2SQLResult(
            status="succeeded",
            query_type="master_data_lookup",
            answer="LLM이 read-only SQL을 생성했습니다.",
            sql="SELECT area, toolgroup FROM fab10.toolgroups ORDER BY area, toolgroup LIMIT 50",
            confidence=0.82,
            limitations=[
                "현재 결과는 SMT2020 General Data 기반 simulation/model input 기준입니다."
            ],
            plan=QueryPlan(
                query_type="master_data_lookup",
                template_id=None,
                fab_id="fab10",
                data_source_type="model_master",
                source_tables=["fab10.toolgroups"],
            ),
        ),
    )

    result = Supervisor().run(ChatRequest(message="fab10 Dry_Etch toolgroup 목록 보여줘"))

    assert result.status == "succeeded"
    assert result.query_type == "master_data_lookup"
    assert result.sql is not None
    assert [item.source_type for item in result.evidence] == ["planner_plan", "text2sql_plan"]
    assert result.agent_reflections[0]["decision"] == "pass"
    assert result.supervisor_reviews == []


def test_supervisor_diagnosis_exposes_placeholder_limitations(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("RAG_LOCAL_STORE_PATH", str(tmp_path / "missing.jsonl"))
    monkeypatch.setenv("VECTOR_DB_URL", "")
    get_settings.cache_clear()

    result = Supervisor().run(ChatRequest(message="왜 fab10 Queue Time이 늘었어?"))

    assert result.query_type == "diagnosis"
    assert any(run.agent == "rag" and run.status == "data_unavailable" for run in result.agent_runs)
    assert any("RAG store has no chunks" in item for item in result.limitations)
    get_settings.cache_clear()


def test_supervisor_process_basics_runs_rag_without_text2sql(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.agents.graph.retrieve_evidence",
        lambda *args, **kwargs: EvidenceResult(
            [
                Evidence(
                    source_type="rag_chunk",
                    title="CMP 기본",
                    content="CMP는 wafer 표면을 평탄화하는 공정입니다.",
                    metadata={
                        "knowledge_base": PROCESS_BASICS,
                        "score": 0.91,
                        "chunk_id": "test-cmp",
                    },
                )
            ],
            {},
            [],
        ),
    )

    result = Supervisor().run(ChatRequest(message="CMP 공정이 뭐야?"))

    assert result.status == "succeeded"
    assert result.query_type == "knowledge_lookup"
    assert [run.agent for run in result.agent_runs] == ["rag"]
    assert result.evidence[-1].metadata["knowledge_base"] == PROCESS_BASICS
    assert "CMP는 wafer" in result.answer


def test_supervisor_does_not_mark_empty_rag_retrieval_as_succeeded(monkeypatch) -> None:
    monkeypatch.setattr("app.agents.graph.retrieve_evidence", lambda *args, **kwargs: EvidenceResult([], {}, []))

    result = Supervisor().run(ChatRequest(message="CMP 공정이 뭐야?"))

    assert result.status == "data_unavailable"
    assert result.agent_runs[0].agent == "rag"
    assert result.agent_runs[0].status == "data_unavailable"
    assert any("관련성이 확인된 지식 근거가 없습니다" in item for item in result.limitations)


def test_supervisor_diagnosis_continues_to_rag_when_text2sql_fails(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.agents.graph.retrieve_evidence",
        lambda *args, **kwargs: EvidenceResult(
            [
                Evidence(
                    source_type="rag_chunk",
                    title="Queue Time 대응",
                    content="Queue Time 증가는 병목 설비와 WIP 증가를 함께 검토합니다.",
                    metadata={"knowledge_base": "incident_playbook", "score": 0.88},
                )
            ],
            {},
            [],
        ),
    )

    result = Supervisor().run(ChatRequest(message="왜 fab10 Queue Time이 늘었어?"))

    assert result.query_type == "diagnosis"
    assert [run.agent for run in result.agent_runs] == ["text2sql", "rag", "case_search"]
    assert any(run.agent == "rag" and run.status == "succeeded" for run in result.agent_runs)
    assert any("실제 원인을 확정할 수 없" in item for item in result.limitations)


def test_rag_empty_result_is_not_reported_as_success(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.agents.graph.retrieve_evidence", lambda *args, **kwargs: EvidenceResult([], {}, [])
    )
    result = Supervisor().run(ChatRequest(message="CMP 공정이 뭐야?"))
    assert result.status == "data_unavailable"
    assert any(run.agent == "rag" and run.status == "data_unavailable" for run in result.agent_runs)
    assert any("근거를 찾지 못" in item for item in result.limitations)


def test_rag_simulation_provenance_reaches_answer_limitations(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.agents.graph.retrieve_evidence",
        lambda *args, **kwargs: EvidenceResult(
            [
                Evidence(
                    source_type="rag_chunk",
                    title="시뮬레이션 자료",
                    content="교육 목적 내용",
                    metadata={
                        "knowledge_base": PROCESS_BASICS,
                        "score": 1,
                        "reliability": "simulation_reference",
                    },
                )
            ],
            {},
            [],
        ),
    )
    result = Supervisor().run(ChatRequest(message="CMP 공정이 뭐야?"))
    assert any("실제 사내 SOP가 아닙니다" in item for item in result.limitations)


def test_corrupt_rag_corpus_marks_overall_request_failed(monkeypatch):
    def invalid(*args, **kwargs):
        raise ValueError("invalid corpus")

    monkeypatch.setattr("app.agents.graph.retrieve_evidence", invalid)
    result = Supervisor().run(ChatRequest(message="CMP 공정이 뭐야?"))
    assert result.status == "failed"
    assert any(run.agent == "rag" and run.status == "failed" for run in result.agent_runs)
