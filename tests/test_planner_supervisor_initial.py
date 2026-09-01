from app.agents.planner import create_plan
from app.agents.supervisor import Supervisor
from app.schemas.chat import ChatRequest


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


def test_supervisor_status_stops_on_data_unavailable() -> None:
    result = Supervisor().run(ChatRequest(message="지금 fab10 WIP 몇 개야?"))

    assert result.status == "data_unavailable"
    assert result.query_type == "status"
    assert result.sql is None
    assert any(run.agent == "text2sql" for run in result.agent_runs)
    assert "AutoSched" in " ".join(result.limitations)


def test_supervisor_master_lookup_returns_planner_and_text2sql_evidence() -> None:
    result = Supervisor().run(ChatRequest(message="fab10 Dry_Etch toolgroup 목록 보여줘"))

    assert result.status == "succeeded"
    assert result.query_type == "master_data_lookup"
    assert result.sql is not None
    assert [item.source_type for item in result.evidence] == ["planner_plan", "text2sql_plan"]


def test_supervisor_diagnosis_exposes_placeholder_limitations() -> None:
    result = Supervisor().run(ChatRequest(message="왜 fab10 Queue Time이 늘었어?"))

    assert result.query_type == "diagnosis"
    assert any(run.agent == "rag" and run.status == "data_unavailable" for run in result.agent_runs)
    assert "RAG retrieval is not implemented yet." in result.limitations
