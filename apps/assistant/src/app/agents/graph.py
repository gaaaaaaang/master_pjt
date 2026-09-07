from __future__ import annotations

from dataclasses import asdict
from functools import partial
from typing import Any, TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph

from app.agents.llm_nodes import compose_with_llm, reflect_with_llm
from app.agents.planner import PlannerDecision, create_plan
from app.agents.supervisor import review_agent_result, review_final_answer, review_plan
from app.config import get_settings
from app.schemas.chat import ChatRequest
from app.sub_agent.case_search import find_similar_cases
from app.sub_agent.diagnosis import synthesize_diagnosis
from app.sub_agent.impact import estimate_output_delta
from app.sub_agent.rag import INCIDENT_PLAYBOOK, retrieve_knowledge
from app.sub_agent.reflection import reflect_agent_output
from app.sub_agent.text2sql import QueryType, Text2SQLResult, answer_question
from app.sub_agent.visualization import build_chart_spec


class AgentState(TypedDict, total=False):
    request: ChatRequest
    conversation_id: str
    conversation_history: list[dict[str, Any]]
    plan: PlannerDecision
    status: str
    halted: bool
    answer: str
    answer_parts: list[str]
    limitations: list[str]
    evidence: list[dict[str, Any]]
    reasoning_state: list[dict[str, Any]]
    agent_runs: list[dict[str, Any]]
    agent_reflections: list[dict[str, Any]]
    current_reflection: dict[str, Any]
    supervisor_reviews: list[dict[str, Any]]
    supervisor_decisions: list[dict[str, Any]]
    current_agent: str
    current_review_id: int
    recovery_action: str
    alternate_agent: str | None
    forced_agent: str | None
    retry_counts: dict[str, int]
    retry_budget_remaining: int
    replan_count: int
    replan_budget_remaining: int
    alternate_budget_remaining: int
    replan_feedback: list[dict[str, Any]]
    execution_cursor: int
    next_agent: str | None
    termination_reason: str | None
    text2sql_result: Text2SQLResult | None
    sql: str | None
    chart: dict[str, Any] | None
    confidence: float | None
    reflection: dict[str, Any]
    reflection_decisions: list[dict[str, Any]]
    answer_review: dict[str, Any]
    stream_event: dict[str, Any]


def _planner_node(state: AgentState) -> dict[str, Any]:
    request = state["request"]
    plan = create_plan(
        request.message,
        fab=request.fab,
        line=request.line,
        process=request.process,
        product=request.product,
        route=request.route,
        equipment=request.equipment,
        date_basis=request.date_basis,
        metric=request.metric,
        conversation_history=state.get("conversation_history", []),
        execution_feedback=state.get("replan_feedback", []),
    )
    model = get_settings().openai_model
    metadata = {
        "execution_mode": plan.execution_mode,
        "model": model,
        "status": plan.status,
        "query_type": plan.query_type,
        "selected_sub_agents": plan.selected_sub_agents,
        "rag_knowledge_base": plan.rag_knowledge_base,
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
    reasoning = _reasoning_entry(
        "planner",
        f"{plan.query_type}로 분류하고 {len(plan.execution_steps)}개 실행 단계를 만들었습니다.",
        {
            "history_turns": len(state.get("conversation_history", [])),
            "execution_feedback_count": len(state.get("replan_feedback", [])),
            "selected_sub_agents": plan.selected_sub_agents,
            "missing_slots": plan.missing_slots,
        },
    )
    return {
        "plan": plan,
        "status": plan.status,
        "limitations": list(
            dict.fromkeys([*state.get("limitations", []), *plan.limitations])
        ),
        "evidence": [evidence],
        "answer_parts": [],
        "halted": False,
        "forced_agent": None,
        "sql": None,
        "chart": None,
        "confidence": None,
        "text2sql_result": None,
        "execution_cursor": 0,
        "next_agent": None,
        "termination_reason": None,
        "reasoning_state": [*state.get("reasoning_state", []), reasoning],
        "stream_event": {
            "type": "node_completed",
            "node": "planner",
            "message": f"{plan.query_type} 질의로 분류하고 실행 계획을 만들었습니다.",
            "data": {**metadata, "reasoning": reasoning},
        },
    }


def _supervisor_node(state: AgentState) -> dict[str, Any]:
    request = state["request"]
    plan, decision = review_plan(state["plan"], request.message)
    halted = plan.status != "ready"
    answer = ""
    if plan.status == "needs_clarification":
        answer = plan.clarification_question or "추가 정보가 필요합니다."
    elif plan.status == "data_unavailable":
        answer = plan.limitations[0] if plan.limitations else "필요한 데이터가 없습니다."
    elif plan.status == "unsupported":
        answer = "현재 지원 범위 밖의 질문입니다."
    reasoning = _reasoning_entry(
        "supervisor",
        (
            f"계획 상태가 {plan.status}라서 "
            f"{'실행을 승인했습니다' if not halted else '실행을 중단했습니다'}."
        ),
        {
            "status": plan.status,
            "halted": halted,
            "selected_sub_agents": plan.selected_sub_agents,
            "reason": decision["reason"],
        },
    )
    return {
        "plan": plan,
        "halted": halted,
        "answer": answer,
        "reasoning_state": [*state.get("reasoning_state", []), reasoning],
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
                "reason": decision["reason"],
                "execution_mode": (
                    "deterministic_fallback"
                    if decision.get("fallback_used")
                    else "llm_chat_completions"
                ),
                "model": get_settings().openai_model,
                "reasoning": reasoning,
            },
        },
    }


def _dispatcher_node(state: AgentState) -> dict[str, Any]:
    if state.get("halted"):
        reasoning = _reasoning_entry(
            "dispatcher",
            "이전 단계가 중단 상태라 추가 agent 실행 없이 reflection으로 넘깁니다.",
            {"halted": True, "status": state.get("status")},
        )
        return {
            "next_agent": None,
            "reasoning_state": [*state.get("reasoning_state", []), reasoning],
            "stream_event": {
                "type": "node_completed",
                "node": "dispatcher",
                "message": "중단 상태를 확인하고 실행 단계를 종료했습니다.",
                "data": {"reasoning": reasoning},
            },
        }
    steps = state["plan"].execution_steps
    cursor = state.get("execution_cursor", 0)
    if cursor >= len(steps):
        reasoning = _reasoning_entry(
            "dispatcher",
            "계획된 실행 단계를 모두 소진해 최종 reflection으로 이동합니다.",
            {"cursor": cursor, "total_steps": len(steps)},
        )
        return {
            "next_agent": None,
            "reasoning_state": [*state.get("reasoning_state", []), reasoning],
            "stream_event": {
                "type": "node_completed",
                "node": "dispatcher",
                "message": "계획된 agent 실행을 모두 마쳤습니다.",
                "data": {"reasoning": reasoning},
            },
        }
    next_agent = steps[cursor].agent
    reasoning = _reasoning_entry(
        "dispatcher",
        f"계획 cursor {cursor}의 다음 agent로 {next_agent}를 선택했습니다.",
        {"cursor": cursor, "next_agent": next_agent, "total_steps": len(steps)},
    )
    return {
        "next_agent": next_agent,
        "execution_cursor": cursor + 1,
        "reasoning_state": [*state.get("reasoning_state", []), reasoning],
        "stream_event": {
            "type": "node_completed",
            "node": "dispatcher",
            "message": f"다음 실행 agent를 {next_agent}로 정했습니다.",
            "data": {"reasoning": reasoning},
        },
    }


def _text2sql_node(state: AgentState) -> dict[str, Any]:
    plan = state["plan"]
    if not _should_run_agent(state, "text2sql"):
        return _skipped("text2sql", "Planner가 Text2SQL을 선택하지 않았습니다.")

    request = state["request"]
    result = answer_question(
        request.message,
        fab=request.fab,
        process=request.process,
        product=request.product,
        route=request.route,
        equipment=request.equipment,
        date_basis=request.date_basis,
        metric=request.metric,
        query_type=_text2sql_query_type(
            plan.query_type, plan.selected_sub_agents
        ),
        conversation_history=state.get("conversation_history", []),
        execution_feedback=[
            decision
            for decision in [
                *state.get("supervisor_decisions", []),
                *state.get("reflection_decisions", []),
            ]
            if (
                decision.get("agent_name") == "text2sql"
                and decision.get("action") == "retry_same_agent"
            )
            or (
                decision.get("retry_target") == "text2sql"
                and decision.get("action") == "retry_target"
            )
        ],
    )
    run = {
        "agent": "text2sql",
        "status": result.status,
        "summary": result.answer,
        "metadata": {"row_count": result.row_count},
    }
    query_plan = asdict(result.plan) if result.plan else None
    evidence = list(state.get("evidence", []))
    result_evidence = {
        "source_type": "text2sql_plan",
        "title": "Text2SQL query and result",
        "content": result.answer,
        "metadata": {
            "status": result.status,
            "query_type": result.query_type,
            "row_count": result.row_count,
            "columns": result.columns,
            "sample_rows": result.rows[:20],
            "query_plan": query_plan,
            "sql": result.sql,
        },
    }
    evidence.append(result_evidence)
    failures = {"needs_clarification", "data_unavailable", "unsupported", "failed"}
    text2sql_required = next(
        (step.required for step in plan.execution_steps if step.agent == "text2sql"),
        False,
    )
    limitations = [*state.get("limitations", []), *result.limitations]
    reflection_patch = _agent_reflection_patch(
        state,
        run,
        agent_output={
            **run,
            "sql": result.sql,
            "row_count": result.row_count,
            "columns": result.columns,
        },
        evidence=[result_evidence],
        limitations=result.limitations,
    )
    reasoning = _reasoning_entry(
        "text2sql",
        f"{result.query_type} 질의를 {result.status} 상태로 처리했습니다.",
        {
            "fab": request.fab,
            "required": text2sql_required,
            "row_count": result.row_count,
            "has_sql": bool(result.sql),
            "feedback_count": len(
                [
                    item
                    for item in [
                        *state.get("supervisor_decisions", []),
                        *state.get("reflection_decisions", []),
                    ]
                    if item.get("agent_name") == "text2sql"
                    or item.get("retry_target") == "text2sql"
                ]
            ),
        },
    )
    return {
        "text2sql_result": result,
        "sql": result.sql,
        "confidence": result.confidence,
        "limitations": limitations,
        "evidence": evidence,
        "agent_runs": [*state.get("agent_runs", []), run],
        "answer_parts": [*state.get("answer_parts", []), result.answer],
        "halted": (
            result.status in failures
            and text2sql_required
            and plan.query_type != "diagnosis"
        ),
        "status": result.status,
        **reflection_patch,
        "reasoning_state": [*state.get("reasoning_state", []), reasoning],
        "stream_event": {
            "type": "tool_completed",
            "node": "text2sql",
            "message": result.answer,
            "data": {
                "status": result.status,
                "query_plan": query_plan,
                "sql": result.sql,
                "row_count": result.row_count,
                "columns": result.columns,
                "sample_rows": result.rows[:5],
                "reflection": reflection_patch["current_reflection"],
                "reasoning": reasoning,
            },
        },
    }


def _rag_node(state: AgentState) -> dict[str, Any]:
    if not _should_run_agent(state, "rag"):
        return _skipped("rag", "Planner가 RAG를 선택하지 않았거나 필수 단계가 실패했습니다.")
    request = state["request"]
    evidence = list(state.get("evidence", []))
    limitations = list(state.get("limitations", []))
    items = []
    try:
        knowledge_base = state["plan"].rag_knowledge_base
        items = retrieve_knowledge(request.message, knowledge_base=knowledge_base)
    except NotImplementedError as exc:
        status = "data_unavailable"
        summary = str(exc)
        limitations.append(summary)
    else:
        status = "succeeded" if items else "data_unavailable"
        summary = (
            f"{len(items)}개 지식 근거를 조회했습니다."
            if items
            else "질문과 관련성이 확인된 지식 근거가 없습니다."
        )
        if items:
            evidence.extend(item.model_dump() for item in items)
        else:
            limitations.append(summary)
        if items and state["plan"].query_type == "diagnosis" and knowledge_base == INCIDENT_PLAYBOOK:
            limitation = "RAG 근거만으로 실제 원인을 확정할 수 없으며 SQL/운영 로그 확인이 필요합니다."
            if limitation not in limitations:
                limitations.append(limitation)
    run = {
        "agent": "rag",
        "status": status,
        "summary": summary,
        "metadata": {"knowledge_base": state["plan"].rag_knowledge_base},
    }
    agent_evidence = [item.model_dump() for item in items] if status == "succeeded" else []
    reflection_patch = _agent_reflection_patch(
        state,
        run,
        agent_output={**run, "retrieved_count": len(items)},
        evidence=agent_evidence,
        limitations=limitations,
    )
    reasoning = _reasoning_entry(
        "rag",
        summary,
        {
            "status": status,
            "knowledge_base": state["plan"].rag_knowledge_base,
            "retrieved_count": len(items),
        },
    )
    return {
        "status": status,
        "evidence": evidence,
        "limitations": limitations,
        "agent_runs": [*state.get("agent_runs", []), run],
        **reflection_patch,
        "reasoning_state": [*state.get("reasoning_state", []), reasoning],
        "answer_parts": [
            *state.get("answer_parts", []),
            *_rag_answer_parts(items if status == "succeeded" else []),
        ],
        "stream_event": {
            "type": "tool_completed",
            "node": "rag",
            "message": summary,
            "data": {
                "status": status,
                "knowledge_base": state["plan"].rag_knowledge_base,
                "reflection": reflection_patch["current_reflection"],
                "reasoning": reasoning,
            },
        },
    }


def _case_search_node(state: AgentState) -> dict[str, Any]:
    if not _should_run_agent(state, "case_search"):
        return _skipped(
            "case_search",
            "Planner가 유사 사례 검색을 선택하지 않았거나 필수 단계가 실패했습니다.",
        )
    limitations = list(state.get("limitations", []))
    try:
        items = find_similar_cases(state["request"].message)
    except (NotImplementedError, TypeError, ValueError) as exc:
        items = []
        status = "data_unavailable"
        summary = str(exc)
        limitations.append(summary)
    else:
        status = "succeeded" if items else "data_unavailable"
        summary = (
            f"{len(items)}개 유사 사례를 조회했습니다."
            if items
            else "질문과 일치하는 검증된 유사 사례가 없습니다."
        )
        if not items:
            limitations.append(summary)
    evidence = [*state.get("evidence", []), *(item.model_dump() for item in items)]
    diagnosis_synthesis = None
    if state["plan"].query_type == "diagnosis":
        diagnosis_synthesis = synthesize_diagnosis(evidence)
        evidence.append(diagnosis_synthesis)
    run = {"agent": "case_search", "status": status, "summary": summary, "metadata": {}}
    agent_evidence = [item.model_dump() for item in items]
    reflection_patch = _agent_reflection_patch(
        state,
        run,
        agent_output={**run, "retrieved_count": len(items)},
        evidence=agent_evidence,
        limitations=limitations,
    )
    reasoning = _reasoning_entry(
        "case_search",
        summary,
        {"evidence_count": len(items)},
    )
    return {
        "evidence": evidence,
        "limitations": limitations,
        "agent_runs": [*state.get("agent_runs", []), run],
        **reflection_patch,
        "reasoning_state": [*state.get("reasoning_state", []), reasoning],
        "answer_parts": [
            *state.get("answer_parts", []),
            *(
                [diagnosis_synthesis["content"]]
                if diagnosis_synthesis is not None
                else []
            ),
        ],
        "stream_event": {
            "type": "tool_completed",
            "node": "case_search",
            "message": summary,
            "data": {
                "status": status,
                "evidence_count": len(items),
                "reflection": reflection_patch["current_reflection"],
                "reasoning": reasoning,
            },
        },
    }


def _impact_node(state: AgentState) -> dict[str, Any]:
    if not _should_run_agent(state, "impact"):
        return _skipped("impact", "Planner가 영향도 계산을 선택하지 않았거나 필수 단계가 실패했습니다.")
    request = state["request"]
    text2sql_result = state.get("text2sql_result")
    impact = estimate_output_delta(
        baseline={
            "rows": text2sql_result.rows if text2sql_result else [],
            "columns": text2sql_result.columns if text2sql_result else [],
            "query_plan": (
                asdict(text2sql_result.plan)
                if text2sql_result and text2sql_result.plan
                else None
            ),
        },
        scenario={"question": request.message, "fab": request.fab},
    )
    limitations = [*state.get("limitations", []), *impact.get("limitations", [])]
    summary = impact["summary"]
    status = impact["status"]
    mixed_dimensions = impact.get("baseline", {}).get("mixed_dimensions", [])
    run = {
        "agent": "impact",
        "status": status,
        "summary": summary,
        "metadata": {
            "estimate_keys": sorted(impact.get("estimates", {})),
            "mixed_dimensions": mixed_dimensions,
        },
    }
    impact_evidence = {
        "source_type": "impact_calculation",
        "title": "Impact sensitivity calculation",
        "content": summary,
        "metadata": impact,
    }
    reflection_patch = _agent_reflection_patch(
        state,
        run,
        agent_output={**run, "calculation": impact},
        evidence=[impact_evidence],
        limitations=impact.get("limitations", []),
    )
    reasoning = _reasoning_entry(
        "impact",
        summary,
        {
            "status": status,
            "has_baseline": bool(text2sql_result and text2sql_result.rows),
            "estimate_keys": sorted(impact.get("estimates", {})),
            "mixed_dimensions": mixed_dimensions,
        },
    )
    return {
        "status": status,
        "limitations": limitations,
        "evidence": [*state.get("evidence", []), impact_evidence],
        "answer_parts": [*state.get("answer_parts", []), summary],
        "agent_runs": [*state.get("agent_runs", []), run],
        **reflection_patch,
        "reasoning_state": [*state.get("reasoning_state", []), reasoning],
        "stream_event": {
            "type": "tool_completed",
            "node": "impact",
            "message": summary,
            "data": {
                "status": status,
                "mixed_dimensions": mixed_dimensions,
                "calculation": impact,
                "reflection": reflection_patch["current_reflection"],
                "reasoning": reasoning,
            },
        },
    }


def _visualization_node(state: AgentState) -> dict[str, Any]:
    result = state.get("text2sql_result")
    if not _should_run_agent(state, "visualization"):
        return _skipped("visualization", "Planner가 시각화를 선택하지 않았습니다.")
    if state.get("halted") or not result or not result.rows:
        run = {
            "agent": "visualization",
            "status": "skipped",
            "summary": "조회 결과가 없어 차트를 생성하지 않았습니다.",
            "metadata": {},
        }
        reflection_patch = _agent_reflection_patch(
            state,
            run,
            agent_output={**run, "chart": None},
            evidence=[],
            limitations=[],
        )
        reasoning = _reasoning_entry(
            "visualization",
            "조회 결과 row가 없어 차트 생성을 건너뛰었습니다.",
            {"has_text2sql_result": bool(result), "row_count": 0},
        )
        return {
            "agent_runs": [*state.get("agent_runs", []), run],
            **reflection_patch,
            "reasoning_state": [*state.get("reasoning_state", []), reasoning],
            "stream_event": {
                "type": "tool_skipped",
                "node": "visualization",
                "message": run["summary"],
                "data": {
                    "reflection": reflection_patch["current_reflection"],
                    "reasoning": reasoning,
                },
            },
        }

    intent = result.plan.chart_intent if result.plan else None
    try:
        chart = build_chart_spec(state["request"].message, result.rows, intent=intent)
    except ValueError as exc:
        summary = f"차트 생성 계약을 충족하지 못했습니다: {exc}"
        limitations = [*state.get("limitations", []), summary]
        run = {
            "agent": "visualization",
            "status": "data_unavailable",
            "summary": summary,
            "metadata": {"chart_error": str(exc)},
        }
        reflection_patch = _agent_reflection_patch(
            state,
            run,
            agent_output={**run, "chart": None},
            evidence=[],
            limitations=limitations,
        )
        reasoning = _reasoning_entry(
            "visualization",
            summary,
            {"row_count": len(result.rows), "chart_error": str(exc)},
        )
        return {
            "chart": None,
            "limitations": limitations,
            "agent_runs": [*state.get("agent_runs", []), run],
            **reflection_patch,
            "reasoning_state": [*state.get("reasoning_state", []), reasoning],
            "stream_event": {
                "type": "tool_completed",
                "node": "visualization",
                "message": summary,
                "data": {
                    "status": "data_unavailable",
                    "reflection": reflection_patch["current_reflection"],
                    "reasoning": reasoning,
                },
            },
        }
    insufficient_series = {
        str(item["series"])
        for item in chart.get("series_coverage", [])
        if item.get("assessment") == "insufficient"
    }
    coverage_limitations = [
        (
            f"차트 series '{item['series']}'는 전체 {item['axis_point_count']}시점 중 "
            f"{item['point_count']}시점만 관측되어 추세를 계산할 수 없습니다."
            if item.get("reason") == "coverage_below_0.5"
            else f"차트 series '{item['series']}'는 관측치가 "
            f"{item['point_count']}개뿐이어서 추세를 계산할 수 없습니다."
        )
        for item in chart.get("series_coverage", [])
        if item.get("assessment") == "insufficient"
    ]
    gap_limitations = [
        "차트 series "
        f"'{gap['series']}'에 관측 누락 시점이 있습니다: "
        + ", ".join(str(value) for value in gap.get("missing_x", []))
        for gap in chart.get("series_gaps", [])
        if str(gap.get("series")) not in insufficient_series
    ]
    chart_limitations = [*coverage_limitations, *gap_limitations]
    limitations = [*state.get("limitations", []), *chart_limitations]
    run = {
        "agent": "visualization",
        "status": "succeeded",
        "summary": f"{chart['type']} chart를 생성했습니다.",
        "metadata": {
            "encoding": chart["encoding"],
            "series_gap_count": len(chart.get("series_gaps", [])),
            "insufficient_series_count": len(insufficient_series),
        },
    }
    visualization_evidence = {
        "source_type": "visualization_spec",
        "title": f"{chart['type']} chart",
        "content": "SQL query rows로 생성한 chart specification입니다.",
        "metadata": {
            "status": "succeeded",
            "chart_type": chart["type"],
            "encoding": chart["encoding"],
            "trend_summary": chart.get("trend_summary", []),
            "series_gaps": chart.get("series_gaps", []),
            "series_coverage": chart.get("series_coverage", []),
            "imputed_points": chart.get("imputed_points", []),
        },
    }
    reflection_patch = _agent_reflection_patch(
        state,
        run,
        agent_output={**run, "chart": chart},
        evidence=[],
        limitations=limitations,
    )
    reasoning = _reasoning_entry(
        "visualization",
        f"{len(result.rows)}개 row와 chart intent를 사용해 {chart['type']} chart를 만들었습니다.",
        {
            "row_count": len(result.rows),
            "chart_type": chart["type"],
            "encoding": chart["encoding"],
        },
    )
    return {
        "chart": chart,
        "limitations": limitations,
        "evidence": [*state.get("evidence", []), visualization_evidence],
        "agent_runs": [*state.get("agent_runs", []), run],
        **reflection_patch,
        "reasoning_state": [*state.get("reasoning_state", []), reasoning],
        "stream_event": {
            "type": "tool_completed",
            "node": "visualization",
            "message": run["summary"],
            "data": {
                "chart": chart,
                "reflection": reflection_patch["current_reflection"],
                "reasoning": reasoning,
            },
        },
    }


def _composer_node(state: AgentState) -> dict[str, Any]:
    request = state["request"]
    answer = compose_with_llm(
        question=request.message,
        plan=state["plan"],
        answer_parts=state.get("answer_parts", []),
        evidence=state.get("evidence", []),
        limitations=state.get("limitations", []),
        reflection=state.get("reflection", {}),
        conversation_history=state.get("conversation_history", []),
    )
    status = state.get("status", "succeeded")
    if status == "ready":
        status = "succeeded"
    reasoning = _reasoning_entry(
        "composer",
        "수집된 tool summary, evidence, limitation, reflection을 기준으로 최종 답변을 작성했습니다.",
        {
            "answer_part_count": len(state.get("answer_parts", [])),
            "evidence_count": len(state.get("evidence", [])),
            "limitation_count": len(state.get("limitations", [])),
        },
    )
    return {
        "answer": answer,
        "status": status,
        "reasoning_state": [*state.get("reasoning_state", []), reasoning],
        "stream_event": {
            "type": "node_completed",
            "node": "composer",
            "message": "조회 결과를 근거로 최종 답변을 구성했습니다.",
            "data": {
                "answer": answer,
                "execution_mode": (
                    "deterministic_fallback"
                    if state.get("reflection", {}).get("fallback_used")
                    else "llm_chat_completions"
                ),
                "model": get_settings().openai_model,
                "reasoning": reasoning,
            },
        },
    }


def _answer_supervisor_node(state: AgentState) -> dict[str, Any]:
    answer = state.get("answer", "")
    review = review_final_answer(
        question=state["request"].message,
        answer=answer,
        plan=state["plan"],
        evidence=state.get("evidence", []),
        limitations=state.get("limitations", []),
    )
    revised = bool(review.get("correction_applied"))
    if revised:
        answer = str(review["corrected_answer"])

    limitations = list(state.get("limitations", []))
    if not review["approved"]:
        for issue in review["issues"]:
            if issue not in limitations:
                limitations.append(issue)
    termination_reason = state.get("termination_reason")
    status = state.get("status", "succeeded")
    if not review["approved"] and termination_reason != "human_review_required":
        termination_reason = "answer_review_failed"
        status = "failed"

    reasoning = _reasoning_entry(
        "answer_supervisor",
        "최종 답변을 원 질문과 근거에 대조해 검증했습니다.",
        {
            "approved": review["approved"],
            "revised": revised,
            "issue_count": len(review["issues"]),
        },
    )
    return {
        "answer": answer,
        "answer_review": review,
        "limitations": limitations,
        "status": status,
        "termination_reason": termination_reason,
        "reasoning_state": [*state.get("reasoning_state", []), reasoning],
        "stream_event": {
            "type": "node_completed",
            "node": "answer_supervisor",
            "message": "최종 답변과 사용자 질문의 정합성을 검증했습니다.",
            "data": {
                "approved": review["approved"],
                "revised": revised,
                "issues": review["issues"],
                "execution_mode": (
                    "deterministic_fallback"
                    if review.get("fallback_used")
                    else "llm_chat_completions"
                ),
                "reasoning": reasoning,
            },
        },
    }


def _reflection_node(state: AgentState) -> dict[str, Any]:
    limitations = list(dict.fromkeys(state.get("limitations", [])))
    reflection = reflect_with_llm(
        question=state["request"].message,
        answer_parts=state.get("answer_parts", []),
        evidence=state.get("evidence", []),
        limitations=limitations,
        query_type=state["plan"].query_type,
        agent_reflections=state.get("agent_reflections", []),
        supervisor_reviews=state.get("supervisor_reviews", []),
        conversation_history=state.get("conversation_history", []),
    )
    reflection["execution_mode"] = (
        "deterministic_fallback"
        if reflection.get("fallback_used")
        else "llm_chat_completions"
    )
    reflection["model"] = get_settings().openai_model
    action = str(reflection.get("action") or "compose")
    retry_target = reflection.get("retry_target")
    retry_counts = dict(state.get("retry_counts", {}))
    retry_budget = state.get("retry_budget_remaining", 0)
    replan_count = state.get("replan_count", 0)
    replan_budget = state.get("replan_budget_remaining", 0)
    replan_feedback = list(state.get("replan_feedback", []))
    forced_agent = None

    if action == "retry_target":
        target_reflection = next(
            (
                item
                for item in reversed(state.get("agent_reflections", []))
                if item.get("agent_name") == retry_target
            ),
            None,
        )
        invalid_retry = (
            retry_target not in AGENT_NEXT_NODE
            or retry_budget <= 0
            or retry_counts.get(str(retry_target), 0) >= 1
            or not target_reflection
            or target_reflection.get("status")
            in {"data_unavailable", "unsupported", "needs_clarification", "skipped"}
        )
        if invalid_retry:
            action = "human_review"
            retry_target = None
            reflection["reason"] = (
                f"{reflection.get('reason', '')} Retry target was rejected by the bounded policy."
            ).strip()
        else:
            retry_counts[str(retry_target)] = retry_counts.get(str(retry_target), 0) + 1
            retry_budget -= 1
            forced_agent = str(retry_target)
    elif action == "replan":
        if replan_budget <= 0:
            action = "human_review"
            reflection["reason"] = (
                f"{reflection.get('reason', '')} Replan budget is exhausted."
            ).strip()
        else:
            replan_count += 1
            replan_budget -= 1
            replan_feedback.append(
                {
                    "source": "final_reflection",
                    "warnings": reflection.get("warnings", []),
                    "reason": reflection.get("reason"),
                }
            )

    termination_reason = None
    if action == "compose":
        termination_reason = (
            "completed" if reflection.get("is_supported") else "composed_with_limitations"
        )
    elif action == "human_review":
        termination_reason = "human_review_required"

    reflection["action"] = action
    reflection["retry_target"] = retry_target if action == "retry_target" else None
    decision = {
        "action": action,
        "retry_target": reflection["retry_target"],
        "reason": reflection.get("reason", ""),
        "retry_budget_remaining": retry_budget,
        "replan_budget_remaining": replan_budget,
        "termination_reason": termination_reason,
    }
    for warning in reflection.get("warnings", []):
        if warning not in limitations:
            limitations.append(warning)
    action_label = {
        "compose": "최종 답변 작성",
        "replan": "재계획",
        "retry_target": "대상 agent 재시도",
        "human_review": "사람 검토",
    }.get(action, action)
    reasoning = _reasoning_entry(
        "reflection",
        f"최종 검증 결과: {action_label}.",
        {
            "is_supported": reflection.get("is_supported"),
            "warning_count": len(reflection.get("warnings", [])),
            "retry_target": reflection["retry_target"],
            "termination_reason": termination_reason,
        },
    )
    return {
        "limitations": limitations,
        "reflection": reflection,
        "reflection_decisions": [*state.get("reflection_decisions", []), decision],
        "retry_counts": retry_counts,
        "retry_budget_remaining": retry_budget,
        "replan_count": replan_count,
        "replan_budget_remaining": replan_budget,
        "replan_feedback": replan_feedback,
        "forced_agent": forced_agent,
        "termination_reason": termination_reason,
        "reasoning_state": [*state.get("reasoning_state", []), reasoning],
        "stream_event": {
            "type": "node_completed",
            "node": "reflection",
            "message": "근거, 의도 일치, 데이터 한계를 검증했습니다.",
            "data": {**reflection, "reasoning": reasoning},
        },
    }


def _agent_supervisor_node(state: AgentState) -> dict[str, Any]:
    reflection = state["current_reflection"]
    agent_name = state["current_agent"]
    retry_counts = dict(state.get("retry_counts", {}))
    allowed_alternates = _allowed_alternate_agents(state, agent_name)
    decision = review_agent_result(
        state["plan"],
        reflection,
        retry_count=retry_counts.get(agent_name, 0),
        retry_budget_remaining=state.get("retry_budget_remaining", 0),
        replan_budget_remaining=state.get("replan_budget_remaining", 0),
        alternate_budget_remaining=state.get("alternate_budget_remaining", 0),
        allowed_alternate_agents=allowed_alternates,
    )
    action = decision["action"]
    retry_budget = state.get("retry_budget_remaining", 0)
    replan_count = state.get("replan_count", 0)
    replan_budget = state.get("replan_budget_remaining", 0)
    alternate_budget = state.get("alternate_budget_remaining", 0)
    forced_agent = None
    halted = state.get("halted", False)
    replan_feedback = list(state.get("replan_feedback", []))
    execution_cursor = state.get("execution_cursor", 0)

    if action == "retry_same_agent":
        retry_counts[agent_name] = retry_counts.get(agent_name, 0) + 1
        retry_budget -= 1
        halted = False
    elif action == "replan":
        replan_count += 1
        replan_budget -= 1
        halted = False
        replan_feedback.append(
            {
                "agent_name": agent_name,
                "reflection": reflection,
                "supervisor_reason": decision["reason"],
                "planner_feedback": decision.get("planner_feedback"),
            }
        )
    elif action == "alternate_agent":
        alternate_budget -= 1
        forced_agent = decision["alternate_agent"]
        halted = False
        for index in range(execution_cursor, len(state["plan"].execution_steps)):
            if state["plan"].execution_steps[index].agent == forced_agent:
                execution_cursor = index + 1
                break

    reviews = [dict(item) for item in state.get("supervisor_reviews", [])]
    review_id = state["current_review_id"]
    for review in reversed(reviews):
        if review.get("review_id") == review_id:
            review["resolution"] = action
            review["supervisor_reason"] = decision["reason"]
            review["alternate_agent"] = decision.get("alternate_agent")
            break

    supervisor_decision = {
        **decision,
        "agent_name": agent_name,
        "review_id": review_id,
        "retry_budget_remaining": retry_budget,
        "replan_budget_remaining": replan_budget,
        "alternate_budget_remaining": alternate_budget,
    }
    reasoning = _reasoning_entry(
        "agent_supervisor",
        f"{agent_name} 검토 후 recovery action을 {action}으로 결정했습니다.",
        {
            "agent_name": agent_name,
            "review_id": review_id,
            "retry_budget_remaining": retry_budget,
            "replan_budget_remaining": replan_budget,
            "alternate_budget_remaining": alternate_budget,
            "reason": decision["reason"],
        },
    )
    return {
        "recovery_action": action,
        "alternate_agent": decision.get("alternate_agent"),
        "forced_agent": forced_agent,
        "halted": halted,
        "retry_counts": retry_counts,
        "retry_budget_remaining": retry_budget,
        "replan_count": replan_count,
        "replan_budget_remaining": replan_budget,
        "alternate_budget_remaining": alternate_budget,
        "replan_feedback": replan_feedback,
        "execution_cursor": execution_cursor,
        "supervisor_reviews": reviews,
        "supervisor_decisions": [
            *state.get("supervisor_decisions", []),
            supervisor_decision,
        ],
        "limitations": list(
            dict.fromkeys(
                [*state.get("limitations", []), *decision.get("limitations", [])]
            )
        ),
        "reasoning_state": [*state.get("reasoning_state", []), reasoning],
        "stream_event": {
            "type": "node_completed",
            "node": "agent_supervisor",
            "message": f"{agent_name} 검토 결과: {action}",
            "data": {**supervisor_decision, "reasoning": reasoning},
        },
    }


def _agent_reflection_patch(
    state: AgentState,
    run: dict[str, Any],
    *,
    agent_output: dict[str, Any],
    evidence: list[dict[str, Any]],
    limitations: list[str],
) -> dict[str, Any]:
    plan = state["plan"]
    agent_name = str(run["agent"])
    step = next((item for item in plan.execution_steps if item.agent == agent_name), None)
    agent_intent = plan.intent
    required = False
    if step:
        agent_intent = f"{step.action} ({step.reason})"
        required = step.required

    reflection = reflect_agent_output(
        agent_name=agent_name,
        agent_intent=agent_intent,
        planner_plan=asdict(plan),
        agent_output=agent_output,
        success_criteria=_agent_success_criteria(agent_name, step.action if step else None),
        evidence=evidence,
        limitations=limitations,
        required=required,
    )
    agent_reflections = [*state.get("agent_reflections", []), reflection]
    supervisor_reviews = list(state.get("supervisor_reviews", []))
    current_review_id = len(supervisor_reviews) + 1
    if reflection["decision"] == "needs_supervisor_review":
        supervisor_reviews.append(
            {
                "review_id": current_review_id,
                "agent_name": agent_name,
                "status": run["status"],
                "reason": reflection["reason"],
                "warnings": reflection["warnings"],
                "recommended_action": reflection["recommended_action"],
                "resolution": "pending",
            }
        )
    return {
        "agent_reflections": agent_reflections,
        "current_reflection": reflection,
        "supervisor_reviews": supervisor_reviews,
        "current_agent": agent_name,
        "current_review_id": current_review_id,
        "forced_agent": None,
    }


def _agent_success_criteria(agent_name: str, planned_action: str | None) -> list[str]:
    criteria = []
    if planned_action:
        criteria.append(f"Complete planner action: {planned_action}")
    criteria.extend(
        {
            "text2sql": [
                "Return a succeeded status with read-only SQL.",
                "Record query/result evidence and limitations.",
            ],
            "rag": [
                "Return retrieved knowledge evidence.",
                "Keep diagnosis claims within the retrieved evidence boundary.",
            ],
            "case_search": ["Return at least one traceable similar-case evidence item."],
            "impact": ["Return calculation inputs, result, and calculation limitations."],
            "visualization": ["Return a chart specification backed by query rows."],
        }.get(agent_name, ["Return a non-empty, evidence-backed result."])
    )
    return criteria


def _skipped(node: str, message: str) -> dict[str, Any]:
    del node, message
    return {"stream_event": None}


def _reasoning_entry(
    node: str,
    summary: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "node": node,
        "summary": summary,
        "details": details or {},
    }


def _should_run_agent(state: AgentState, agent_name: str) -> bool:
    forced = state.get("forced_agent") == agent_name
    selected = agent_name in state["plan"].selected_sub_agents
    return (forced or selected) and (forced or not state.get("halted", False))


def _allowed_alternate_agents(state: AgentState, agent_name: str) -> list[str]:
    query_type = state["plan"].query_type
    candidates = {
        "text2sql": ["rag"] if query_type == "diagnosis" else [],
        "rag": ["case_search"] if query_type == "diagnosis" else [],
        "case_search": ["rag"] if query_type == "diagnosis" else [],
        "impact": [],
        "visualization": [],
    }.get(agent_name, [])
    attempted = {run["agent"] for run in state.get("agent_runs", [])}
    return [candidate for candidate in candidates if candidate not in attempted]


AGENT_NEXT_NODE = {
    "text2sql": "rag",
    "rag": "case_search",
    "case_search": "impact",
    "impact": "visualization",
    "visualization": "reflection",
}


def _route_after_agent_result(state: AgentState, *, agent_name: str) -> str:
    reflection = state.get("current_reflection", {})
    if (
        reflection.get("agent_name") == agent_name
        and reflection.get("decision") == "needs_supervisor_review"
    ):
        return "review"
    return "dispatch"


def _route_dispatcher(state: AgentState) -> str:
    return state.get("next_agent") or "reflection"


def _route_recovery_action(state: AgentState) -> str:
    action = state.get("recovery_action", "continue")
    current_agent = state.get("current_agent", "visualization")
    if action == "retry_same_agent":
        return current_agent
    if action == "replan":
        return "planner"
    if action == "alternate_agent" and state.get("alternate_agent"):
        return str(state["alternate_agent"])
    return "dispatcher"


def _route_final_reflection(state: AgentState) -> str:
    action = state.get("reflection", {}).get("action", "compose")
    if action == "replan":
        return "planner"
    if action == "retry_target" and state.get("forced_agent"):
        return str(state["forced_agent"])
    return "composer"


def _text2sql_query_type(
    planner_query_type: str, selected_sub_agents: list[str] | None = None
) -> QueryType:
    selected = set(selected_sub_agents or [])
    if planner_query_type == "diagnosis" and "visualization" in selected:
        return "trend"
    if planner_query_type in {"diagnosis", "impact"}:
        return "status"
    if planner_query_type in {
        "status", "master_data_lookup", "release_plan_lookup", "trend", "unsupported"
    }:
        return planner_query_type
    return "unsupported"


def _rag_answer_parts(items) -> list[str]:
    parts = []
    for item in items[:3]:
        knowledge_base = item.metadata.get("knowledge_base", "rag")
        source_document = item.metadata.get("source_document") or item.title
        parts.append(
            f"RAG({knowledge_base}) 근거 문서: {source_document}\n"
            f"{item.content[:700]}"
        )
    return parts


def build_agent_graph():
    builder = StateGraph(AgentState)
    builder.add_node("planner", _planner_node)
    builder.add_node("supervisor", _supervisor_node)
    builder.add_node("dispatcher", _dispatcher_node)
    builder.add_node("text2sql", _text2sql_node)
    builder.add_node("rag", _rag_node)
    builder.add_node("case_search", _case_search_node)
    builder.add_node("impact", _impact_node)
    builder.add_node("visualization", _visualization_node)
    builder.add_node("agent_supervisor", _agent_supervisor_node)
    builder.add_node("composer", _composer_node)
    builder.add_node("reflection", _reflection_node)
    builder.add_node("answer_supervisor", _answer_supervisor_node)
    builder.add_edge(START, "planner")
    builder.add_edge("planner", "supervisor")
    builder.add_edge("supervisor", "dispatcher")
    dispatch_targets = {
        "reflection": "reflection",
        **{agent_name: agent_name for agent_name in AGENT_NEXT_NODE},
    }
    builder.add_conditional_edges("dispatcher", _route_dispatcher, dispatch_targets)
    for agent_name in AGENT_NEXT_NODE:
        builder.add_conditional_edges(
            agent_name,
            partial(_route_after_agent_result, agent_name=agent_name),
            {"review": "agent_supervisor", "dispatch": "dispatcher"},
        )
    recovery_targets = {
        "planner": "planner",
        "reflection": "reflection",
        "dispatcher": "dispatcher",
        **{agent_name: agent_name for agent_name in AGENT_NEXT_NODE},
    }
    builder.add_conditional_edges(
        "agent_supervisor",
        _route_recovery_action,
        recovery_targets,
    )
    final_reflection_targets = {
        "planner": "planner",
        "composer": "composer",
        **{agent_name: agent_name for agent_name in AGENT_NEXT_NODE},
    }
    builder.add_conditional_edges(
        "reflection",
        _route_final_reflection,
        final_reflection_targets,
    )
    builder.add_edge("composer", "answer_supervisor")
    builder.add_edge("answer_supervisor", END)
    return builder.compile()


def initial_graph_state(
    request: ChatRequest,
    *,
    conversation_history: list[dict[str, Any]] | None = None,
) -> AgentState:
    return {
        "request": request,
        "conversation_id": request.conversation_id or str(uuid4()),
        "conversation_history": conversation_history or [],
        "status": "running",
        "halted": False,
        "answer_parts": [],
        "limitations": [],
        "evidence": [],
        "reasoning_state": [],
        "agent_runs": [],
        "agent_reflections": [],
        "supervisor_reviews": [],
        "supervisor_decisions": [],
        "retry_counts": {},
        "retry_budget_remaining": 2,
        "replan_count": 0,
        "replan_budget_remaining": 1,
        "alternate_budget_remaining": 1,
        "replan_feedback": [],
        "execution_cursor": 0,
        "next_agent": None,
        "termination_reason": None,
        "reflection_decisions": [],
        "answer_review": {},
    }
