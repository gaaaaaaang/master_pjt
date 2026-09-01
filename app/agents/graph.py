from __future__ import annotations

from dataclasses import asdict
from typing import Any, TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph

from app.agents.planner import PlannerDecision, create_plan
from app.schemas.chat import ChatRequest
from app.sub_agent.case_search import find_similar_cases
from app.sub_agent.impact import estimate_output_delta
from app.sub_agent.rag import retrieve_knowledge
from app.sub_agent.reflection import verify_response
from app.sub_agent.text2sql import Text2SQLResult, answer_question
from app.sub_agent.visualization import build_chart_spec


class AgentState(TypedDict, total=False):
    request: ChatRequest
    conversation_id: str
    plan: PlannerDecision
    status: str
    halted: bool
    answer: str
    answer_parts: list[str]
    limitations: list[str]
    evidence: list[dict[str, Any]]
    agent_runs: list[dict[str, Any]]
    text2sql_result: Text2SQLResult
    sql: str | None
    chart: dict[str, Any] | None
    confidence: float | None
    reflection: dict[str, Any]
    stream_event: dict[str, Any]


def _planner_node(state: AgentState) -> dict[str, Any]:
    request = state["request"]
    plan = create_plan(request.message, fab=request.fab)
    metadata = {
        "status": plan.status,
        "query_type": plan.query_type,
        "selected_sub_agents": plan.selected_sub_agents,
        "missing_slots": plan.missing_slots,
        "execution_steps": [asdict(step) for step in plan.execution_steps],
        "slots": {key: asdict(value) for key, value in plan.slots.items()},
    }
    evidence = {
        "source_type": "planner_plan",
        "title": "Planner plan",
        "content": plan.intent,
        "metadata": metadata,
    }
    return {
        "plan": plan,
        "status": plan.status,
        "limitations": list(plan.limitations),
        "evidence": [evidence],
        "answer_parts": [],
        "agent_runs": [],
        "stream_event": {
            "type": "node_completed",
            "node": "planner",
            "message": f"{plan.query_type} 질의로 분류하고 실행 계획을 만들었습니다.",
            "data": metadata,
        },
    }


def _supervisor_node(state: AgentState) -> dict[str, Any]:
    plan = state["plan"]
    halted = plan.status != "ready"
    answer = ""
    if plan.status == "needs_clarification":
        answer = plan.clarification_question or "추가 정보가 필요합니다."
    elif plan.status == "data_unavailable":
        answer = plan.limitations[0] if plan.limitations else "필요한 데이터가 없습니다."
    elif plan.status == "unsupported":
        answer = "현재 지원 범위 밖의 질문입니다."
    return {
        "halted": halted,
        "answer": answer,
        "stream_event": {
            "type": "node_completed",
            "node": "supervisor",
            "message": (
                " → ".join(plan.selected_sub_agents)
                if plan.selected_sub_agents
                else f"실행 중단: {plan.status}"
            ),
            "data": {
                "status": plan.status,
                "selected_sub_agents": plan.selected_sub_agents,
            },
        },
    }


def _text2sql_node(state: AgentState) -> dict[str, Any]:
    plan = state["plan"]
    if state.get("halted") or "text2sql" not in plan.selected_sub_agents:
        return _skipped("text2sql", "Planner가 Text2SQL을 선택하지 않았습니다.")

    request = state["request"]
    result = answer_question(request.message, fab=request.fab)
    run = {
        "agent": "text2sql",
        "status": result.status,
        "summary": result.answer,
        "metadata": {"row_count": result.row_count},
    }
    query_plan = asdict(result.plan) if result.plan else None
    evidence = list(state.get("evidence", []))
    evidence.append(
        {
            "source_type": "text2sql_plan",
            "title": "Text2SQL query and result",
            "content": result.answer,
            "metadata": {
                "status": result.status,
                "query_type": result.query_type,
                "row_count": result.row_count,
                "query_plan": query_plan,
                "sql": result.sql,
            },
        }
    )
    failures = {"needs_clarification", "data_unavailable", "unsupported", "failed"}
    text2sql_required = next(
        (step.required for step in plan.execution_steps if step.agent == "text2sql"),
        False,
    )
    return {
        "text2sql_result": result,
        "sql": result.sql,
        "confidence": result.confidence,
        "limitations": [*state.get("limitations", []), *result.limitations],
        "evidence": evidence,
        "agent_runs": [*state.get("agent_runs", []), run],
        "answer_parts": [*state.get("answer_parts", []), result.answer],
        "halted": result.status in failures and text2sql_required,
        "status": result.status,
        "stream_event": {
            "type": "tool_completed",
            "node": "text2sql",
            "message": result.answer,
            "data": {
                "query_plan": query_plan,
                "sql": result.sql,
                "row_count": result.row_count,
                "columns": result.columns,
                "sample_rows": result.rows[:5],
            },
        },
    }


def _rag_node(state: AgentState) -> dict[str, Any]:
    if state.get("halted") or "rag" not in state["plan"].selected_sub_agents:
        return _skipped("rag", "Planner가 RAG를 선택하지 않았거나 필수 단계가 실패했습니다.")
    request = state["request"]
    evidence = list(state.get("evidence", []))
    limitations = list(state.get("limitations", []))
    try:
        items = retrieve_knowledge(request.message)
    except NotImplementedError as exc:
        status = "data_unavailable"
        summary = str(exc)
        limitations.append(summary)
    else:
        status = "succeeded"
        summary = f"{len(items)}개 지식 근거를 조회했습니다."
        evidence.extend(item.model_dump() for item in items)
    run = {"agent": "rag", "status": status, "summary": summary, "metadata": {}}
    return {
        "evidence": evidence,
        "limitations": limitations,
        "agent_runs": [*state.get("agent_runs", []), run],
        "stream_event": {
            "type": "tool_completed",
            "node": "rag",
            "message": summary,
            "data": {"status": status},
        },
    }


def _case_search_node(state: AgentState) -> dict[str, Any]:
    if state.get("halted") or "case_search" not in state["plan"].selected_sub_agents:
        return _skipped(
            "case_search",
            "Planner가 유사 사례 검색을 선택하지 않았거나 필수 단계가 실패했습니다.",
        )
    items = find_similar_cases(state["request"].message)
    evidence = [*state.get("evidence", []), *(item.model_dump() for item in items)]
    summary = f"{len(items)}개 유사 사례를 조회했습니다."
    run = {"agent": "case_search", "status": "succeeded", "summary": summary, "metadata": {}}
    return {
        "evidence": evidence,
        "agent_runs": [*state.get("agent_runs", []), run],
        "stream_event": {
            "type": "tool_completed",
            "node": "case_search",
            "message": summary,
            "data": {"evidence_count": len(items)},
        },
    }


def _impact_node(state: AgentState) -> dict[str, Any]:
    if state.get("halted") or "impact" not in state["plan"].selected_sub_agents:
        return _skipped("impact", "Planner가 영향도 계산을 선택하지 않았거나 필수 단계가 실패했습니다.")
    request = state["request"]
    impact = estimate_output_delta(
        baseline={},
        scenario={"question": request.message, "fab": request.fab},
    )
    limitations = [*state.get("limitations", []), *impact.get("limitations", [])]
    summary = "영향도 모델이 아직 operational metric과 연결되지 않았습니다."
    run = {"agent": "impact", "status": "data_unavailable", "summary": summary, "metadata": {}}
    return {
        "limitations": limitations,
        "answer_parts": [*state.get("answer_parts", []), summary],
        "agent_runs": [*state.get("agent_runs", []), run],
        "stream_event": {
            "type": "tool_completed",
            "node": "impact",
            "message": summary,
            "data": {"status": "data_unavailable"},
        },
    }


def _visualization_node(state: AgentState) -> dict[str, Any]:
    plan = state["plan"]
    result = state.get("text2sql_result")
    if "visualization" not in plan.selected_sub_agents:
        return _skipped("visualization", "Planner가 시각화를 선택하지 않았습니다.")
    if state.get("halted") or not result or not result.rows:
        run = {
            "agent": "visualization",
            "status": "skipped",
            "summary": "조회 결과가 없어 차트를 생성하지 않았습니다.",
            "metadata": {},
        }
        return {
            "agent_runs": [*state.get("agent_runs", []), run],
            "stream_event": {
                "type": "tool_skipped",
                "node": "visualization",
                "message": run["summary"],
                "data": {},
            },
        }

    intent = result.plan.chart_intent if result.plan else None
    chart = build_chart_spec("Route lot releases by date", result.rows, intent=intent)
    run = {
        "agent": "visualization",
        "status": "succeeded",
        "summary": f"{chart['type']} chart를 생성했습니다.",
        "metadata": {"encoding": chart["encoding"]},
    }
    return {
        "chart": chart,
        "agent_runs": [*state.get("agent_runs", []), run],
        "stream_event": {
            "type": "tool_completed",
            "node": "visualization",
            "message": run["summary"],
            "data": {"chart": chart},
        },
    }


def _composer_node(state: AgentState) -> dict[str, Any]:
    answer = state.get("answer") or "\n\n".join(dict.fromkeys(state.get("answer_parts", [])))
    if not answer:
        answer = "요청을 처리할 실행 결과가 없습니다."
    status = state.get("status", "succeeded")
    if status == "ready":
        status = "succeeded"
    return {
        "answer": answer,
        "status": status,
        "stream_event": {
            "type": "node_completed",
            "node": "composer",
            "message": "조회 결과를 근거로 최종 답변을 구성했습니다.",
            "data": {"answer": answer},
        },
    }


def _reflection_node(state: AgentState) -> dict[str, Any]:
    limitations = list(dict.fromkeys(state.get("limitations", [])))
    reflection = verify_response(
        state.get("answer", ""),
        evidence=state.get("evidence", []),
        limitations=limitations,
        query_type=state["plan"].query_type,
    )
    for warning in reflection.get("warnings", []):
        if warning not in limitations:
            limitations.append(warning)
    return {
        "limitations": limitations,
        "reflection": reflection,
        "stream_event": {
            "type": "node_completed",
            "node": "reflection",
            "message": "근거, 의도 일치, 데이터 한계를 검증했습니다.",
            "data": reflection,
        },
    }


def _skipped(node: str, message: str) -> dict[str, Any]:
    del node, message
    return {"stream_event": None}


def build_agent_graph():
    builder = StateGraph(AgentState)
    builder.add_node("planner", _planner_node)
    builder.add_node("supervisor", _supervisor_node)
    builder.add_node("text2sql", _text2sql_node)
    builder.add_node("rag", _rag_node)
    builder.add_node("case_search", _case_search_node)
    builder.add_node("impact", _impact_node)
    builder.add_node("visualization", _visualization_node)
    builder.add_node("composer", _composer_node)
    builder.add_node("reflection", _reflection_node)
    builder.add_edge(START, "planner")
    builder.add_edge("planner", "supervisor")
    builder.add_edge("supervisor", "text2sql")
    builder.add_edge("text2sql", "rag")
    builder.add_edge("rag", "case_search")
    builder.add_edge("case_search", "impact")
    builder.add_edge("impact", "visualization")
    builder.add_edge("visualization", "composer")
    builder.add_edge("composer", "reflection")
    builder.add_edge("reflection", END)
    return builder.compile()


def initial_graph_state(request: ChatRequest) -> AgentState:
    return {
        "request": request,
        "conversation_id": request.conversation_id or str(uuid4()),
        "status": "running",
        "halted": False,
        "answer_parts": [],
        "limitations": [],
        "evidence": [],
        "agent_runs": [],
    }
