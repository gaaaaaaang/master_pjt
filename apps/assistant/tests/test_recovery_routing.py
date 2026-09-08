from app.agents.graph import _impact_node
from app.agents.planner import ExecutionStep, PlannerDecision
from app.agents.supervisor import Supervisor
from app.schemas.chat import ChatRequest, Evidence
from app.sub_agent.rag import EvidenceResult
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


def _patch_compound_graph(monkeypatch, plan: PlannerDecision, result: Text2SQLResult) -> None:
    monkeypatch.setattr("app.agents.graph.create_plan", lambda *args, **kwargs: plan)
    monkeypatch.setattr(
        "app.agents.graph.review_plan",
        lambda current_plan, question: (current_plan, {"reason": "approved", "proceed": True}),
    )
    monkeypatch.setattr("app.agents.graph.answer_question", lambda *args, **kwargs: result)
    monkeypatch.setattr(
        "app.agents.graph.retrieve_evidence",
        lambda *args, **kwargs: EvidenceResult([Evidence(source_type='rag_chunk', title='WIP playbook', content='WIP 증가 원인 후보 점검', metadata={'knowledge_base': 'incident_playbook', 'issue_types': 'wip'})], {}, []),
    )
    monkeypatch.setattr(
        "app.agents.graph.find_similar_cases",
        lambda *args, **kwargs: [
            Evidence(
                source_type="similar_case",
                title="SIM-WIP",
                content="시뮬레이션 참고 사례",
                metadata={
                    "case_id": "SIM-WIP",
                    "case_type": "simulated_reference",
                    "source": "synthetic-test",
                    "issue_type": "wip",
                },
            )
        ],
    )
    monkeypatch.setattr(
        "app.agents.graph.reflect_with_llm",
        lambda **kwargs: {
            "is_supported": True,
            "warnings": [],
            "composer_instructions": [],
            "action": "compose",
            "retry_target": None,
        },
    )
    monkeypatch.setattr(
        "app.agents.graph.compose_with_llm",
        lambda **kwargs: "fab10 WIP 원인 후보와 요청 결과입니다. 시뮬레이션 참고이며 실제 원인은 확정할 수 없습니다.",
    )
    monkeypatch.setattr(
        "app.agents.graph.review_final_answer",
        lambda **kwargs: {
            "approved": True,
            "issues": [],
            "correction_applied": False,
        },
    )


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
            sql="SELECT toolgroup FROM fab10.toolgroups_fab10 LIMIT 20",
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
            sql="SELECT toolgroup FROM fab10.toolgroups_fab10 LIMIT 20",
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
            sql="SELECT queue_time FROM fab10.autosched_status_fab10 LIMIT 20",
            plan=QueryPlan(query_type="status", template_id=None, fab_id="fab10"),
        ),
    )
    monkeypatch.setattr(
        "app.agents.graph.retrieve_evidence",
        lambda *args, **kwargs: EvidenceResult((_ for _ in ()).throw(NotImplementedError('RAG unavailable')), {}, []),
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
        lambda current_plan, question: (current_plan, {"reason": "approved", "proceed": True}),
    )
    monkeypatch.setattr(
        "app.agents.graph.retrieve_evidence",
        lambda *args, **kwargs: EvidenceResult([Evidence(source_type='rag_chunk', title='context', content='toolgroup context')], {}, []),
    )
    monkeypatch.setattr(
        "app.agents.graph.answer_question",
        lambda *args, **kwargs: Text2SQLResult(
            status="succeeded",
            query_type="master_data_lookup",
            answer="Toolgroups retrieved.",
            sql="SELECT toolgroup FROM fab10.toolgroups_fab10 LIMIT 20",
        ),
    )

    result = Supervisor().run(ChatRequest(message="fab10 toolgroup 목록 보여줘"))

    assert [run.agent for run in result.agent_runs] == ["rag", "text2sql"]
    assert result.termination_reason == "completed"


def test_compound_diagnosis_trend_executes_visualization_with_trend_rows(monkeypatch) -> None:
    plan = PlannerDecision(
        status="ready",
        query_type="diagnosis",
        intent="diagnose WIP increase and show trend",
        selected_sub_agents=["text2sql", "rag", "case_search", "visualization"],
        execution_steps=[
            ExecutionStep(agent, f"run {agent}", False, "compound request")
            for agent in ("text2sql", "rag", "case_search", "visualization")
        ],
        rag_knowledge_base="incident_playbook",
    )
    text2sql_result = Text2SQLResult(
        status="succeeded",
        query_type="trend",
        answer="WIP trend rows",
        sql="SELECT report_date, stngrp, wiplotavg FROM fab10.autosched_stngrp_fab10",
        rows=[
            {"report_date": "2020-01-03", "stngrp": "Dry_Etch", "wiplotavg": 12},
            {"report_date": "2020-01-01", "stngrp": "Dry_Etch", "wiplotavg": 10},
            {"report_date": "2020-01-01", "stngrp": "Photo", "wiplotavg": 8},
            {"report_date": "2020-01-02", "stngrp": "Photo", "wiplotavg": 9},
            {"report_date": "2020-01-03", "stngrp": "Photo", "wiplotavg": 11},
        ],
        columns=["report_date", "stngrp", "wiplotavg"],
        row_count=5,
        plan=QueryPlan(
            query_type="trend",
            template_id="compound_trend",
            fab_id="fab10",
            chart_intent={
                "type": "line",
                "x": "report_date",
                "y": "wiplotavg",
                "series": "stngrp",
            },
        ),
    )
    _patch_compound_graph(monkeypatch, plan, text2sql_result)

    result = Supervisor().run(ChatRequest(message="fab10 WIP 추세와 증가 원인을 같이 분석해줘"))

    assert [run.agent for run in result.agent_runs] == [
        "text2sql",
        "rag",
        "case_search",
        "visualization",
    ]
    assert result.chart is not None
    assert [row["report_date"] for row in result.chart["rows"]] == [
        "2020-01-01",
        "2020-01-01",
        "2020-01-02",
        "2020-01-03",
        "2020-01-03",
    ]
    assert result.chart["series_gaps"] == [
        {"series": "Dry_Etch", "missing_x": ["2020-01-02"]}
    ]
    visualization_evidence = next(
        item for item in result.evidence if item.source_type == "visualization_spec"
    )
    assert visualization_evidence.metadata["series_gaps"] == result.chart["series_gaps"]
    assert any(
        "Dry_Etch" in limitation and "2020-01-02" in limitation
        for limitation in result.limitations
    )


def test_compound_diagnosis_impact_executes_calculation_from_status_baseline(monkeypatch) -> None:
    plan = PlannerDecision(
        status="ready",
        query_type="diagnosis",
        intent="diagnose bottleneck and calculate utilization impact",
        selected_sub_agents=["text2sql", "rag", "case_search", "impact"],
        execution_steps=[
            ExecutionStep(agent, f"run {agent}", False, "compound request")
            for agent in ("text2sql", "rag", "case_search", "impact")
        ],
        rag_knowledge_base="incident_playbook",
    )
    text2sql_result = Text2SQLResult(
        status="succeeded",
        query_type="status",
        answer="utilization baseline",
        sql="SELECT util_percent FROM fab10.autosched_stngrp_fab10",
        rows=[{"util_percent": 80.0}],
        columns=["util_percent"],
        row_count=1,
        plan=QueryPlan(query_type="status", template_id="compound_impact", fab_id="fab10"),
    )
    _patch_compound_graph(monkeypatch, plan, text2sql_result)

    result = Supervisor().run(
        ChatRequest(
            message="fab10 Dry_Etch 병목 원인과 utilization 5%p 감소 영향도 알려줘"
        )
    )

    assert [run.agent for run in result.agent_runs] == [
        "text2sql",
        "rag",
        "case_search",
        "impact",
    ]
    impact = next(item for item in result.evidence if item.source_type == "impact_calculation")
    assert impact.metadata["estimates"]["capacity_delta_percent"] == -6.25


def test_impact_node_exposes_mixed_baseline_dimensions_in_trace() -> None:
    plan = PlannerDecision(
        status="ready",
        query_type="impact",
        intent="calculate utilization impact",
        selected_sub_agents=["text2sql", "impact"],
        execution_steps=[
            ExecutionStep("text2sql", "query baseline", True, "impact input"),
            ExecutionStep("impact", "calculate impact", True, "impact request"),
        ],
    )
    text2sql_result = Text2SQLResult(
        status="succeeded",
        query_type="status",
        answer="station-group baselines",
        rows=[
            {"stngrp": "Dry_Etch_A", "util_percent": 80.0},
            {"stngrp": "Dry_Etch_B", "util_percent": 60.0},
        ],
        columns=["stngrp", "util_percent"],
        row_count=2,
        plan=QueryPlan(query_type="status", template_id="mixed", fab_id="fab10"),
    )

    patch = _impact_node(
        {
            "request": ChatRequest(
                message="fab10 가동률이 5%p 감소하면 capacity 영향은?"
            ),
            "plan": plan,
            "text2sql_result": text2sql_result,
        }
    )

    assert patch["status"] == "data_unavailable"
    assert patch["agent_runs"][0]["metadata"]["mixed_dimensions"] == ["stngrp"]
    assert patch["stream_event"]["data"]["mixed_dimensions"] == ["stngrp"]
    assert patch["evidence"][0]["metadata"]["baseline"]["mixed_dimensions"] == [
        "stngrp"
    ]


def test_diagnosis_continues_other_evidence_sources_when_required_sql_fails(
    monkeypatch,
) -> None:
    plan = PlannerDecision(
        status="ready",
        query_type="diagnosis",
        intent="investigate queue increase from independent evidence sources",
        selected_sub_agents=["text2sql", "rag", "case_search"],
        execution_steps=[
            ExecutionStep("text2sql", "query operational metrics", True, "preferred evidence"),
            ExecutionStep("rag", "retrieve incident guidance", True, "independent evidence"),
            ExecutionStep("case_search", "retrieve similar cases", False, "supporting evidence"),
        ],
        rag_knowledge_base="incident_playbook",
    )
    monkeypatch.setattr("app.agents.graph.create_plan", lambda *args, **kwargs: plan)
    monkeypatch.setattr(
        "app.agents.graph.review_plan",
        lambda current_plan, question: (current_plan, {"reason": "approved", "proceed": True}),
    )
    monkeypatch.setattr(
        "app.agents.graph.answer_question",
        lambda *args, **kwargs: Text2SQLResult(
            status="data_unavailable",
            query_type="status",
            answer="Queue Time metric is unavailable.",
            limitations=["No direct Queue Time metric."],
        ),
    )
    monkeypatch.setattr(
        "app.agents.graph.retrieve_evidence",
        lambda *args, **kwargs: EvidenceResult([Evidence(source_type='rag_chunk', title='Queue review', content='Review WIP, utilization, and downtime as hypotheses.', metadata={'knowledge_base': 'incident_playbook'})], {}, []),
    )
    monkeypatch.setattr(
        "app.agents.graph.find_similar_cases",
        lambda *args, **kwargs: [
            Evidence(
                source_type="similar_case",
                title="Reviewed queue case",
                content="A prior reviewed case checked utilization and downtime.",
            )
        ],
    )
    monkeypatch.setattr(
        "app.agents.graph.review_agent_result",
        lambda *args, **kwargs: _recovery_decision("continue"),
    )

    result = Supervisor().run(ChatRequest(message="왜 fab10 Queue Time이 늘었어?"))

    assert [run.agent for run in result.agent_runs] == ["text2sql", "rag", "case_search"]
    assert result.agent_runs[0].status == "data_unavailable"
    assert result.agent_runs[1].status == "succeeded"
    assert result.agent_runs[2].status == "succeeded"


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
            sql="SELECT toolgroup FROM fab10.toolgroups_fab10 LIMIT 20",
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
            sql="SELECT toolgroup FROM fab10.toolgroups_fab10 LIMIT 20",
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
