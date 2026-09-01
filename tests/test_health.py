import json

from fastapi.testclient import TestClient

from app.main import app
from app.sub_agent.text2sql import QueryPlan, Text2SQLResult

client = TestClient(app)


def test_root_describes_local_services() -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["docs"] == "/docs"
    assert response.json()["frontend"] == "http://localhost:5173"


def test_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_status_chat_works_in_mock_mode(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.agents.supervisor.answer_question",
        lambda *args, **kwargs: Text2SQLResult(
            status="failed",
            query_type="status",
            answer="LLM Text2SQL 호출을 완료하지 못했습니다.",
            limitations=["OPENAI_API_KEY is not configured."],
            plan=QueryPlan(query_type="status", template_id=None, fab_id="fab10"),
        ),
    )

    response = client.post("/api/chat", json={"message": "지금 fab10 WIP 몇 개야?"})
    assert response.status_code == 200
    body = response.json()
    assert body["query_type"] == "status"
    assert "OPENAI_API_KEY" in " ".join(body["limitations"])


def test_meta_reflects_shell_stack() -> None:
    response = client.get("/api/meta")
    assert response.status_code == 200
    assert response.json() == {
        "frontend": "react-vite",
        "backend": "fastapi",
        "agent": "langgraph-text2sql-visualization",
    }


def test_agent_trace_exposes_planner_and_supervisor_runs() -> None:
    response = client.post("/api/agent-trace", json={"message": "왜 fab10 Queue Time이 늘었어?"})
    assert response.status_code == 200
    body = response.json()
    assert body["query_type"] == "diagnosis"
    assert body["plan"]["selected_sub_agents"] == ["text2sql", "rag", "case_search"]
    assert [run["agent"] for run in body["agent_runs"]] == ["text2sql", "rag", "case_search"]
    assert body["prompt_versions"]["planner"]
    assert body["prompt_versions"]["supervisor"]


def test_agent_trace_batch_scores_default_cases() -> None:
    response = client.post("/api/agent-trace/batch", json={})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 5
    assert body["passed"] == 5
    assert all("plan" in trace for trace in body["traces"])


def test_chat_stream_exposes_real_node_order_sql_and_chart(monkeypatch) -> None:
    question = (
        "fab10의 lotrelease 테이블에서 route_product_3 건수를 날짜 기준으로 라인차트로 그려줘."
    )
    executed = Text2SQLResult(
        status="succeeded",
        query_type="trend",
        answer=(
            "Route_Product_3의 lotrelease를 start_date 기준으로 집계했습니다. "
            "총 3건이며 날짜 포인트는 1개입니다."
        ),
        sql="""
SELECT start_date::date AS release_date,
       COUNT(*)::bigint AS lot_count
FROM fab10.lotrelease
WHERE route_name = 'Route_Product_3'
GROUP BY start_date::date
ORDER BY release_date ASC
""".strip(),
        rows=[{"release_date": "2018-01-01", "lot_count": 3}],
        columns=["release_date", "lot_count"],
        row_count=1,
        confidence=0.9,
        limitations=[],
        plan=QueryPlan(
            query_type="trend",
            template_id=None,
            fab_id="fab10",
            data_source_type="release_plan",
            chart_intent={
                "type": "line",
                "x": "release_date",
                "y": "lot_count",
                "x_title": "Release date",
                "y_title": "Lot release count",
                "series": "Route_Product_3",
            },
        ),
    )
    monkeypatch.setattr("app.agents.graph.answer_question", lambda *args, **kwargs: executed)
    from app.agents.planner import create_plan

    monkeypatch.setattr("app.agents.graph.create_llm_plan", create_plan)
    monkeypatch.setattr(
        "app.agents.graph.review_plan",
        lambda plan, question: (
            plan,
            {"reason": "test", "selected_sub_agents": plan.selected_sub_agents},
        ),
    )
    monkeypatch.setattr(
        "app.agents.graph.reflect_with_llm",
        lambda **kwargs: {
            "is_supported": True,
            "warnings": [],
            "composer_instructions": [],
            "evidence_count": len(kwargs["evidence"]),
            "limitation_count": len(kwargs["limitations"]),
        },
    )
    monkeypatch.setattr(
        "app.agents.graph.compose_with_llm",
        lambda **kwargs: "Route_Product_3 release plan 집계 결과입니다.",
    )

    response = client.post("/api/chat/stream", json={"message": question})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    payloads = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert [payload["node"] for payload in payloads] == [
        "input",
        "planner",
        "supervisor",
        "text2sql",
        "visualization",
        "reflection",
        "composer",
        "supervisor",
    ]
    text2sql_event = next(payload for payload in payloads if payload["node"] == "text2sql")
    assert "GROUP BY start_date::date" in text2sql_event["data"]["sql"]
    final = payloads[-1]["data"]
    assert final["status"] == "succeeded"
    assert final["chart"]["type"] == "line"
    assert final["chart"]["rows"] == [{"release_date": "2018-01-01", "lot_count": 3}]
