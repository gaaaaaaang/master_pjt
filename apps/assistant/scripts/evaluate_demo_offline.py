"""Fault-inject model unavailability while exercising real local DB and RAG paths.

This is a resilience test, NOT a live-model evaluation. It blocks all HTTP and
both model clients, and uses the application's actual deterministic fallbacks.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.parse import urlsplit


async def ask_stream_route(request):
    """Consume the production SSE route in-process, without an HTTP connection."""
    from app.api.routes import chat_stream

    class ConnectedRequest:
        async def is_disconnected(self):
            return False

    response = chat_stream(request, ConnectedRequest())
    if not response.headers.get("content-type", "").startswith("text/event-stream"):
        raise AssertionError("The stream route did not return the SSE content type")
    events = []
    async for chunk in response.body_iterator:
        content = chunk.decode() if isinstance(chunk, bytes) else chunk
        for frame in content.split("\n\n"):
            values = [line[5:].lstrip() for line in frame.splitlines() if line.startswith("data:")]
            if values:
                events.append(json.loads("\n".join(values)))
    if not events or events[0].get("type") != "run_started":
        raise AssertionError("SSE did not begin with run_started")
    completed = [event for event in events if event.get("type") == "run_completed"]
    if len(completed) != 1 or events[-1] is not completed[0]:
        raise AssertionError("SSE must end with exactly one run_completed event")
    return completed[0]["data"], events


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local-db", action="store_true", required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ids", nargs="*")
    parser.add_argument("--conversation", action="store_true", help="Reuse one local conversation and verify each turn's explicit scope assertions")
    parser.add_argument("--stream-route", action="store_true", help="Consume the real SSE route in-process with every model and HTTP call still blocked")
    parser.add_argument("--raw", type=Path, help="Captured raw rows for explicit value_contract assertions")
    parser.add_argument("--restart-memory-every-turn", action="store_true", help="Reload conversation context from an isolated temporary SQLite store before every turn")
    args = parser.parse_args()
    os.environ["LANGSMITH_TRACING"] = "false"
    from app.config import get_settings
    settings = get_settings()
    if urlsplit(settings.postgres_dsn or "").hostname not in {"localhost", "127.0.0.1", "::1"}:
        parser.error("This audit permits only a localhost DB.")
    if settings.vector_db_url or settings.rag_reranker != "feature":
        parser.error("This audit requires local BM25 and feature reranking.")
    if settings.mock_mode:
        parser.error("Real local tools must be enabled; mock_mode is not this test.")

    from app.schemas.chat import ChatRequest
    from app.services.chat_service import ChatService
    from app.services.conversation_memory import ConversationMemory
    source = Path(__file__).resolve().parents[1] / "src"
    selected = [case for case in json.loads(args.fixture.read_text())
                if not args.ids or case["id"] in args.ids]
    if any(case.get("value_contract") for case in selected) and not args.raw:
        parser.error("--raw is required for value_contract assertions")
    raw = json.loads(args.raw.read_text()) if args.raw else None
    report = {"started_at":datetime.now(UTC).isoformat(), "mode":"model_unavailable_local_tools",
              "external_http_blocked":True, "conversation":args.conversation,
              "transport":"in_process_sse_body_iterator" if args.stream_route else "chat_service",
              "memory_reloaded_each_turn":args.restart_memory_every_turn,
              "source_sha256":hashlib.sha256(b"".join(p.read_bytes() for p in sorted(source.rglob("*.py")))).hexdigest(),
              "fixture_sha256":hashlib.sha256(args.fixture.read_bytes()).hexdigest(),
              "results":[]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary_state = TemporaryDirectory(prefix="fab-demo-memory-") if args.restart_memory_every_turn else None
    state_path = Path(temporary_state.name) / "conversation.sqlite3" if temporary_state else None
    memory = ConversationMemory(store_path=state_path)
    service = ChatService(memory=memory)
    conversation_id = None
    previous_period = (None, None)
    with (
        patch("app.agents.llm.AzureAgentClient.complete_json", side_effect=RuntimeError("Model unavailable: injected local resilience test")),
        patch("app.sub_agent.text2sql.OpenAIText2SQLClient.create_sql", side_effect=RuntimeError("Model unavailable: injected local resilience test")),
        patch("httpx.Client.send", side_effect=AssertionError("All HTTP is forbidden in this local resilience test")),
        patch("httpx.AsyncClient.send", side_effect=AssertionError("All HTTP is forbidden in this local resilience test")),
        patch("app.api.routes.conversation_memory", memory),
    ):
        for case in selected:
            if args.restart_memory_every_turn:
                from app.api import routes
                memory = ConversationMemory(store_path=state_path)
                service = ChatService(memory=memory)
                routes.conversation_memory = memory
            started = time.monotonic()
            result = dict(case)
            try:
                request = ChatRequest(message=case["question"], conversation_id=conversation_id, fab=case.get("request_fab"))
                if args.stream_route:
                    response, events = asyncio.run(ask_stream_route(request))
                    result.update(status=response["status"], response=response, events=events)
                else:
                    response = service.ask(request).model_dump(mode="json")
                    result.update(status=response["status"], response=response)
                if args.conversation:
                    from evaluate_demo_conversations import verify
                    conversation_id = response["conversation_id"]
                    errors, previous_period = verify(case, result["response"], previous_period)
                    result.update(errors=errors, passed=not errors)
                if case.get("value_contract"):
                    from zoneinfo import ZoneInfo

                    from evaluate_snapshot_variants import compare, expected_rows
                    contract = {**case, **case["value_contract"]}
                    expected, keys = expected_rows(contract, raw["fabs"][case["fab"]], datetime.now(ZoneInfo("Asia/Seoul")).date())
                    actual = (response.get("query_result") or {}).get("rows", [])
                    issues = compare(actual, expected, keys, ordered=bool(contract.get("limit")))
                    result["value_errors"] = issues
                    result["passed"] = result.get("passed", True) and not issues
            except Exception as exc:  # noqa: BLE001 - preserve independent failure records
                result.update(status="error", error=f"{type(exc).__name__}: {exc}")
            result["seconds"] = round(time.monotonic()-started, 3)
            report["results"].append(result)
            args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))
            print(case["id"], result["status"], result["seconds"], flush=True)
    print(f"Saved {len(selected)} model-unavailable cases to {args.output}")
    if temporary_state:
        temporary_state.cleanup()


if __name__ == "__main__":
    main()
