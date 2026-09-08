from app.agents.graph import _composer_node, _rag_node, _reflection_node, _supervisor_node
from app.agents.planner import PlannerDecision
from app.agents.supervisor import review_plan
from app.schemas.chat import ChatRequest
from app.sub_agent.rag import EvidenceResult


def plan(**kwargs):
    return PlannerDecision(
        status="ready",
        query_type="knowledge_lookup",
        intent="manual",
        selected_sub_agents=["rag"],
        execution_steps=[],
        **kwargs,
    )


def test_empty_degraded_search_retains_trace_in_run_and_sse(monkeypatch):
    trace = {
        "retrieval_mode": "hybrid_degraded",
        "dense_errors": ["RuntimeError"],
        "selected_count": 0,
    }
    monkeypatch.setattr(
        "app.agents.graph.retrieve_evidence",
        lambda *a, **k: EvidenceResult(
            [], trace, ["벡터 검색 실패로 로컬 키워드 검색 결과만 사용했습니다."]
        ),
    )
    result = _rag_node(
        {"request": ChatRequest(message="없는 지식"), "plan": plan(), "status": "ready"}
    )
    assert result["status"] == "data_unavailable"
    assert result["agent_runs"][0]["metadata"]["retrieval_trace"] == trace
    assert result["stream_event"]["data"]["retrieval_trace"] == trace
    assert any("벡터 검색 실패" in text for text in result["limitations"])


def test_supervisor_stop_replaces_status_and_preserves_answer_without_more_calls(monkeypatch):
    stopped = PlannerDecision(
        status="needs_clarification",
        query_type="knowledge_lookup",
        intent="manual",
        selected_sub_agents=[],
        execution_steps=[],
        limitations=["승인 문서 범위 필요"],
    )
    monkeypatch.setattr(
        "app.agents.graph.review_plan",
        lambda *a: (
            stopped,
            {
                "proceed": False,
                "reason": "scope missing",
                "answer": "어느 승인 문서를 기준으로 할까요?",
            },
        ),
    )
    state = {"request": ChatRequest(message="절차 알려줘"), "plan": plan(), "status": "ready"}
    patch = _supervisor_node(state)
    state.update(patch)
    assert state["status"] == "needs_clarification"
    assert state["answer"] == "어느 승인 문서를 기준으로 할까요?"
    assert state["limitations"] == ["승인 문서 범위 필요"]

    def unexpected(**kwargs):
        raise AssertionError("Halted request must not spend another LLM call")

    monkeypatch.setattr("app.agents.graph.compose_with_llm", unexpected)
    monkeypatch.setattr("app.agents.graph.reflect_with_llm", unexpected)
    assert _composer_node(state) == {"stream_event": None}
    assert _reflection_node(state) == {"stream_event": None}


def test_ready_without_supervisor_approval_cannot_execute_tools():
    class Client:
        def complete_json(self, **kwargs):
            return {
                "proceed": False,
                "status": "ready",
                "selected_sub_agents": ["rag"],
                "reason": "not approved",
                "answer": None,
                "limitations": [],
            }

    reviewed, decision = review_plan(plan(), "manual", llm_client=Client())
    assert reviewed.status == "unsupported"
    assert reviewed.selected_sub_agents == []
    assert decision["proceed"] is False


def test_structured_fab_scope_reaches_retrieval_and_conflict_stops_composition(monkeypatch):
    from app.rag.query import QueryScopeError

    def retrieval(query, **kwargs):
        assert kwargs["fab_id"] == "fab11"
        raise QueryScopeError("선택한 FAB과 질문의 FAB 범위를 확인해 주세요.")

    monkeypatch.setattr("app.agents.graph.retrieve_evidence", retrieval)
    state = {
        "request": ChatRequest(message="fab10 PM 대응", fab="fab11"),
        "plan": plan(),
        "status": "ready",
    }
    state.update(_rag_node(state))
    assert state["status"] == "needs_clarification"
    assert state["halted"]
    assert "FAB" in state["answer"]
    assert _composer_node(state) == {"stream_event": None}
