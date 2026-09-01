import json
import logging
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse

from app.agents.graph import build_agent_graph, initial_graph_state
from app.schemas.chat import ChatRequest, ChatResponse
from app.services.chat_service import ChatService

router = APIRouter(tags=["chat"])
service = ChatService()
logger = logging.getLogger("uvicorn.error")

DEFAULT_TRACE_CASES = [
    {
        "label": "SC-001 live status",
        "message": "지금 fab10 WIP 몇 개야?",
        "expected_query_type": "status",
        "expected_agents": ["text2sql"],
    },
    {
        "label": "SC-001 master lookup",
        "message": "fab10 Dry_Etch toolgroup 목록 보여줘",
        "expected_query_type": "master_data_lookup",
        "expected_agents": ["text2sql"],
    },
    {
        "label": "SC-002 diagnosis",
        "message": "왜 fab10 Queue Time이 늘었어?",
        "expected_query_type": "diagnosis",
        "expected_agents": ["text2sql", "rag", "case_search"],
    },
    {
        "label": "SC-003 impact",
        "message": "fab10에서 Queue Time이 10% 늘면 output 영향은?",
        "expected_query_type": "impact",
        "expected_agents": ["text2sql", "impact"],
    },
    {
        "label": "SC-004 trend",
        "message": "fab10 Queue Time 추세를 비교해줘",
        "expected_query_type": "trend",
        "expected_agents": ["text2sql", "visualization"],
    },
]


@router.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    logger.info("chat.start message=%s fab=%s", request.message, request.fab)
    response = service.ask(request)
    logger.info(
        "chat.done conversation_id=%s query_type=%s sql=%s chart=%s",
        response.conversation_id,
        response.query_type,
        response.sql,
        bool(response.chart),
    )
    return response


@router.post("/chat/stream")
def chat_stream(request: ChatRequest) -> StreamingResponse:
    """Stream real LangGraph node/tool progress as server-sent events."""

    def event_source():
        graph = build_agent_graph()
        state = initial_graph_state(request)
        yield _sse(
            "trace",
            {
                "type": "run_started",
                "node": "input",
                "message": "질문을 접수했습니다.",
                "data": {"conversation_id": state["conversation_id"]},
            },
        )
        logger.info("stream.start conversation_id=%s message=%s", state["conversation_id"], request.message)
        try:
            for update in graph.stream(state, stream_mode="updates"):
                for patch in update.values():
                    state.update(patch)
                    if event := patch.get("stream_event"):
                        _log_stream_event(event)
                        yield _sse("trace", event)

            final = {
                "conversation_id": state["conversation_id"],
                "status": state.get("status"),
                "query_type": state["plan"].query_type,
                "answer": state.get("answer"),
                "sql": state.get("sql"),
                "chart": state.get("chart"),
                "confidence": state.get("confidence"),
                "limitations": state.get("limitations", []),
                "evidence": state.get("evidence", []),
                "plan": asdict(state["plan"]),
                "agent_runs": state.get("agent_runs", []),
                "reflection": state.get("reflection", {}),
            }
            logger.info(
                "stream.done conversation_id=%s status=%s query_type=%s sql=%s chart=%s",
                final["conversation_id"],
                final["status"],
                final["query_type"],
                final["sql"],
                bool(final["chart"]),
            )
            yield _sse(
                "final",
                {
                    "type": "run_completed",
                    "node": "supervisor",
                    "message": "전체 실행을 완료했습니다.",
                    "data": final,
                },
            )
        except (KeyError, RuntimeError, TypeError, ValueError) as exc:
            logger.exception("stream.failed message=%s", request.message)
            yield _sse(
                "error",
                {
                    "type": "run_failed",
                    "node": "supervisor",
                    "message": "에이전트 실행 중 오류가 발생했습니다.",
                    "data": {"error": str(exc)},
                },
            )

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/meta")
def meta() -> dict[str, str]:
    return {
        "frontend": "react-vite",
        "backend": "fastapi",
        "agent": "langgraph-text2sql-visualization",
    }


@router.post("/feedback")
def feedback(payload: dict) -> dict[str, str]:
    return {
        "status": "accepted",
        "message": "feedback persistence is a placeholder",
    }


@router.post("/agent-trace")
def agent_trace(request: ChatRequest) -> dict[str, Any]:
    """Expose planner/supervisor internals for local orchestration evaluation."""
    return _trace_case({"message": request.message, "fab": request.fab})


@router.get("/agent-trace/samples")
def agent_trace_samples() -> dict[str, Any]:
    return {"cases": DEFAULT_TRACE_CASES}


@router.post("/agent-trace/batch")
def agent_trace_batch(payload: dict[str, Any]) -> dict[str, Any]:
    cases = payload.get("cases") or DEFAULT_TRACE_CASES
    traces = [_trace_case(case) for case in cases]
    passed = sum(1 for trace in traces if trace["passed"])
    return {
        "total": len(traces),
        "passed": passed,
        "failed": len(traces) - passed,
        "traces": traces,
    }


def _trace_case(case: dict[str, Any]) -> dict[str, Any]:
    request = ChatRequest(message=case["message"], fab=case.get("fab"))
    result = service.supervisor.run(request)
    plan = asdict(result.plan) if result.plan else None
    agent_runs = [asdict(run) for run in result.agent_runs]
    expected_query_type = case.get("expected_query_type")
    expected_agents = case.get("expected_agents")
    passed = True
    if expected_query_type:
        passed = passed and result.query_type == expected_query_type
    if expected_agents is not None:
        actual_agents = [run.agent for run in result.agent_runs]
        passed = passed and actual_agents == expected_agents

    return {
        "label": case.get("label") or result.query_type,
        "message": request.message,
        "expected_query_type": expected_query_type,
        "expected_agents": expected_agents,
        "passed": passed,
        "conversation_id": result.conversation_id,
        "status": result.status,
        "query_type": result.query_type,
        "answer": result.answer,
        "sql": result.sql,
        "chart": result.chart,
        "confidence": result.confidence,
        "limitations": result.limitations,
        "plan": plan,
        "agent_runs": agent_runs,
        "evidence": [item.model_dump() for item in result.evidence],
        "reflection": result.reflection,
        "prompt_versions": {
            "planner": result.plan.prompt_version if result.plan else None,
            "supervisor": result.prompt_version,
        },
    }


def _sse(event: str, payload: dict[str, Any]) -> str:
    body = json.dumps(jsonable_encoder(payload), ensure_ascii=False)
    return f"event: {event}\ndata: {body}\n\n"


def _log_stream_event(event: dict[str, Any]) -> None:
    data = event.get("data") or {}
    logger.info(
        "stream.event node=%s type=%s message=%s sql=%s row_count=%s",
        event.get("node"),
        event.get("type"),
        event.get("message"),
        data.get("sql"),
        data.get("row_count"),
    )
