from pathlib import Path

from app.agents.planner import create_plan
from app.agents.supervisor import Supervisor, review_plan
from app.config import get_settings
from app.schemas.chat import ChatRequest, Evidence
from app.sub_agent.rag import PROCESS_BASICS
from app.sub_agent.text2sql import QueryPlan, Text2SQLResult


class RecordingLLM:
    def __init__(self, output) -> None:
        self.output = output
        self.calls = []

    def complete_json(self, **kwargs):
        self.calls.append(kwargs)
        return self.output


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
            limitations=["현재 PostgreSQL에는 AutoSched report table(autosched_*)이 적재되어 있지 않습니다."],
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
    assert "AutoSched" in " ".join(result.limitations)


def test_supervisor_master_lookup_returns_planner_and_text2sql_evidence(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.agents.graph.answer_question",
        lambda *args, **kwargs: Text2SQLResult(
            status="succeeded",
            query_type="master_data_lookup",
            answer="LLM이 read-only SQL을 생성했습니다.",
            sql="SELECT area, toolgroup FROM fab10.toolgroups ORDER BY area, toolgroup LIMIT 50",
            confidence=0.82,
            limitations=["현재 결과는 SMT2020 General Data 기반 simulation/model input 기준입니다."],
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
        "app.agents.graph.retrieve_knowledge",
        lambda *args, **kwargs: [
            Evidence(
                source_type="rag_chunk",
                title="CMP 기본",
                content="CMP는 wafer 표면을 평탄화하는 공정입니다.",
                metadata={"knowledge_base": PROCESS_BASICS, "score": 0.91},
            )
        ],
    )

    result = Supervisor().run(ChatRequest(message="CMP 공정이 뭐야?"))

    assert result.status == "succeeded"
    assert result.query_type == "knowledge_lookup"
    assert [run.agent for run in result.agent_runs] == ["rag"]
    assert result.evidence[-1].metadata["knowledge_base"] == PROCESS_BASICS
    assert "CMP는 wafer" in result.answer


def test_supervisor_diagnosis_continues_to_rag_when_text2sql_fails(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.agents.graph.retrieve_knowledge",
        lambda *args, **kwargs: [
            Evidence(
                source_type="rag_chunk",
                title="Queue Time 대응",
                content="Queue Time 증가는 병목 설비와 WIP 증가를 함께 검토합니다.",
                metadata={"knowledge_base": "incident_playbook", "score": 0.88},
            )
        ],
    )

    result = Supervisor().run(ChatRequest(message="왜 fab10 Queue Time이 늘었어?"))

    assert result.query_type == "diagnosis"
    assert [run.agent for run in result.agent_runs] == ["text2sql", "rag", "case_search"]
    assert any(run.agent == "rag" and run.status == "succeeded" for run in result.agent_runs)
    assert any("실제 원인을 확정할 수 없" in item for item in result.limitations)
