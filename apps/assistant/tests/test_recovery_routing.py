from app.agents.planner import ExecutionStep, PlannerDecision
from app.agents.supervisor import Supervisor
from app.schemas.chat import ChatRequest, Evidence
from app.sub_agent.text2sql import QueryPlan, Text2SQLResult


def _recovery_decision(action: str, alternate_agent: str | None = None) -> dict:
    return {
        "action": action,
        "alternate_agent": alternate_agent,
        "reason": f"test {action}",
        "planner_feedback": "change the failed execution plan" if action == "replan" else None,
        "limitations": [],
        "prompt_version": "test",
    }


def test_recovery_retries_same_agent_once(monkeypatch) -> None:
    calls = 0
    feedback_seen = []

    def answer_question(*args, **kwargs):
        nonlocal calls
        del args
        calls += 1
        feedback_seen.append(kwargs["execution_feedback"])
        if calls == 1:
            return Text2SQLResult(
                status="failed",
                query_type="master_data_lookup",
                answer="SQL generation failed.",
                limitations=["Temporary generation failure."],
            )
        return Text2SQLResult(
            status="succeeded",
            query_type="master_data_lookup",
            answer="Toolgroup rows were retrieved.",
            sql="SELECT toolgroup FROM fab10.toolgroups LIMIT 20",
            plan=QueryPlan(
                query_type="master_data_lookup",
                template_id=None,
                fab_id="fab10",
            ),
        )

    monkeypatch.setattr("app.agents.graph.answer_question", answer_question)
    monkeypatch.setattr(
        "app.agents.graph.review_agent_result",
        lambda *args, **kwargs: _recovery_decision("retry_same_agent"),
    )

    result = Supervisor().run(ChatRequest(message="fab10 toolgroup 목록 보여줘"))

    assert calls == 2
    assert feedback_seen[0] == []
    assert feedback_seen[1][0]["action"] == "retry_same_agent"
    assert [run.agent for run in result.agent_runs] == ["text2sql", "text2sql"]
    assert result.retry_counts == {"text2sql": 1}
    assert result.supervisor_decisions[0]["action"] == "retry_same_agent"
    assert result.supervisor_reviews[0]["resolution"] == "retry_same_agent"


def test_recovery_replans_once_with_execution_feedback(monkeypatch) -> None:
    calls = 0

    def answer_question(*args, **kwargs):
        nonlocal calls
        del args, kwargs
        calls += 1
        if calls == 1:
            return Text2SQLResult(
                status="failed",
                query_type="master_data_lookup",
                answer="The first plan failed.",
                limitations=["The plan needs revision."],
            )
        return Text2SQLResult(
            status="succeeded",
            query_type="master_data_lookup",
            answer="The revised plan succeeded.",
            sql="SELECT toolgroup FROM fab10.toolgroups LIMIT 20",
            plan=QueryPlan(
                query_type="master_data_lookup",
                template_id=None,
                fab_id="fab10",
            ),
        )

    monkeypatch.setattr("app.agents.graph.answer_question", answer_question)
    monkeypatch.setattr(
        "app.agents.graph.review_agent_result",
        lambda *args, **kwargs: _recovery_decision("replan"),
    )

    result = Supervisor().run(ChatRequest(message="fab10 toolgroup 목록 보여줘"))

    assert calls == 2
    assert result.replan_count == 1
    assert result.supervisor_decisions[0]["action"] == "replan"
    assert result.supervisor_reviews[0]["resolution"] == "replan"


def test_recovery_routes_to_compatible_alternate_agent(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.agents.graph.answer_question",
        lambda *args, **kwargs: Text2SQLResult(
            status="succeeded",
            query_type="status",
            answer="Queue Time trend rows were retrieved.",
            sql="SELECT queue_time FROM fab10.autosched_status LIMIT 20",
            plan=QueryPlan(query_type="status", template_id=None, fab_id="fab10"),
        ),
    )
    monkeypatch.setattr(
        "app.agents.graph.retrieve_knowledge",
        lambda *args, **kwargs: (_ for _ in ()).throw(NotImplementedError("RAG unavailable")),
    )
    monkeypatch.setattr(
        "app.agents.graph.find_similar_cases",
        lambda *args, **kwargs: [
            Evidence(
                source_type="case",
                title="Queue Time case",
                content="A similar queue increase was reviewed.",
            )
        ],
    )
    monkeypatch.setattr(
        "app.agents.graph.review_agent_result",
        lambda *args, **kwargs: _recovery_decision("alternate_agent", "case_search"),
    )

    result = Supervisor().run(ChatRequest(message="왜 fab10 Queue Time이 늘었어?"))

    assert [run.agent for run in result.agent_runs] == ["text2sql", "rag", "case_search"]
    assert result.supervisor_decisions[0]["action"] == "alternate_agent"
    assert result.supervisor_decisions[0]["alternate_agent"] == "case_search"
    assert result.supervisor_reviews[0]["resolution"] == "alternate_agent"


def test_dispatcher_follows_planner_execution_step_order(monkeypatch) -> None:
    plan = PlannerDecision(
        status="ready",
        query_type="master_data_lookup",
        intent="retrieve knowledge before database evidence",
        selected_sub_agents=["rag", "text2sql"],
        execution_steps=[
            ExecutionStep("rag", "retrieve context", False, "context first"),
            ExecutionStep("text2sql", "query toolgroups", True, "database evidence"),
        ],
        rag_knowledge_base="process_basics",
    )
    monkeypatch.setattr("app.agents.graph.create_plan", lambda *args, **kwargs: plan)
    monkeypatch.setattr(
        "app.agents.graph.review_plan",
        lambda current_plan, question: (current_plan, {"reason": "approved"}),
    )
    monkeypatch.setattr(
        "app.agents.graph.retrieve_knowledge",
        lambda *args, **kwargs: [
            Evidence(source_type="rag_chunk", title="context", content="toolgroup context")
        ],
    )
    monkeypatch.setattr(
        "app.agents.graph.answer_question",
        lambda *args, **kwargs: Text2SQLResult(
            status="succeeded",
            query_type="master_data_lookup",
            answer="Toolgroups retrieved.",
            sql="SELECT toolgroup FROM fab10.toolgroups LIMIT 20",
        ),
    )

    result = Supervisor().run(ChatRequest(message="fab10 toolgroup 목록 보여줘"))

    assert [run.agent for run in result.agent_runs] == ["rag", "text2sql"]
    assert result.termination_reason == "completed"


def test_final_reflection_retry_target_stops_after_agent_budget(monkeypatch) -> None:
    calls = 0

    def answer_question(*args, **kwargs):
        nonlocal calls
        del args, kwargs
        calls += 1
        return Text2SQLResult(
            status="succeeded",
            query_type="master_data_lookup",
            answer="Toolgroups retrieved.",
            sql="SELECT toolgroup FROM fab10.toolgroups LIMIT 20",
        )

    monkeypatch.setattr("app.agents.graph.answer_question", answer_question)
    monkeypatch.setattr(
        "app.agents.graph.reflect_with_llm",
        lambda **kwargs: {
            "is_supported": False,
            "warnings": ["retry for semantic verification"],
            "composer_instructions": [],
            "action": "retry_target",
            "retry_target": "text2sql",
            "reason": "verify the query one more time",
        },
    )

    result = Supervisor().run(ChatRequest(message="fab10 toolgroup 목록 보여줘"))

    assert calls == 2
    assert [item["action"] for item in result.reflection_decisions] == [
        "retry_target",
        "human_review",
    ]
    assert result.retry_counts == {"text2sql": 1}
    assert result.termination_reason == "human_review_required"


def test_final_reflection_can_replan_then_compose(monkeypatch) -> None:
    reflection_calls = 0

    monkeypatch.setattr(
        "app.agents.graph.answer_question",
        lambda *args, **kwargs: Text2SQLResult(
            status="succeeded",
            query_type="master_data_lookup",
            answer="Toolgroups retrieved.",
            sql="SELECT toolgroup FROM fab10.toolgroups LIMIT 20",
        ),
    )

    def reflect(**kwargs):
        nonlocal reflection_calls
        del kwargs
        reflection_calls += 1
        return {
            "is_supported": reflection_calls > 1,
            "warnings": [] if reflection_calls > 1 else ["plan needs revision"],
            "composer_instructions": [],
            "action": "compose" if reflection_calls > 1 else "replan",
            "retry_target": None,
            "reason": "accepted" if reflection_calls > 1 else "revise the plan",
        }

    monkeypatch.setattr("app.agents.graph.reflect_with_llm", reflect)

    result = Supervisor().run(ChatRequest(message="fab10 toolgroup 목록 보여줘"))

    assert reflection_calls == 2
    assert result.replan_count == 1
    assert [item["action"] for item in result.reflection_decisions] == ["replan", "compose"]
    assert result.termination_reason == "completed"
