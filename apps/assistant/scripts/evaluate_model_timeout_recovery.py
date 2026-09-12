"""Exercise real agent clients with a mock network timeout and real local DB.

Model URLs/keys are replaced with fixtures and HTTPX uses MockTransport. No model
request leaves this process. The test checks that one timeout is not repeated by
every later review stage of the same question; a new question retries normally.
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
from unittest.mock import patch
from urllib.parse import urlsplit

import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--stream-route", action="store_true")
    args = parser.parse_args()
    os.environ["LANGSMITH_TRACING"] = "false"
    from app.config import get_settings
    from app.schemas.chat import ChatRequest
    from app.services.chat_service import ChatService
    from app.services.conversation_memory import ConversationMemory

    settings = get_settings()
    if urlsplit(settings.postgres_dsn or "").hostname not in {"localhost", "127.0.0.1", "::1"}:
        parser.error("Only a localhost DB is permitted")
    if settings.vector_db_url or settings.rag_reranker != "feature" or settings.mock_mode:
        parser.error("Real local DB and feature/BM25 retrieval are required")
    safe_settings = settings.model_copy(update={"openai_endpoint":"https://model.invalid", "openai_api_key":"fixture-key"})
    original_client = httpx.Client
    attempts = 0

    def timeout(request):
        nonlocal attempts
        assert request.url.host == "model.invalid"
        assert request.headers["api-key"] == "fixture-key"
        attempts += 1
        raise httpx.ReadTimeout("Injected transport timeout; no external request was sent", request=request)

    class FixtureClient(original_client):
        def __init__(self, **kwargs):
            kwargs["transport"] = httpx.MockTransport(timeout)
            super().__init__(**kwargs)

    def no_async_http(*args, **kwargs):
        raise AssertionError("External HTTP is forbidden")

    cases = [{"id":fab, "question":f"{fab} 전체 현재 WIP은 몇 개야?", "expected_status":"succeeded", "wip":wip}
             for fab,wip in [("FAB10",142),("FAB11",188),("FAB12",261),("FAB13",159)]]
    cases += [
        {"id":"trend", "question":"FAB12 최근 7일 공정별 수율 추세를 그래프로 보여줘", "expected_status":"succeeded", "chart":True},
        {"id":"impact", "question":"FAB13 etch 가동률이 5%p 떨어지면 처리량에 얼마나 영향이 있어?", "expected_status":"succeeded", "impact":True},
        {"id":"knowledge", "question":"Cycle Time Degradation이 뭐야? WIP이나 가동률과 어떤 관계가 있어? 문서 근거로 설명해줘", "expected_status":"failed", "documents":True},
    ]
    checks = []
    with (
        patch("httpx.Client", new=FixtureClient),
        patch("httpx.AsyncClient.send", new=no_async_http),
        patch("app.agents.llm.get_settings", return_value=safe_settings),
        patch("app.sub_agent.text2sql.get_settings", return_value=safe_settings),
        patch("app.api.routes.conversation_memory", ConversationMemory()),
    ):
        service = ChatService(memory=ConversationMemory())
        for case in cases:
            before, started = attempts, time.monotonic()
            request = ChatRequest(message=case["question"])
            if args.stream_route:
                from evaluate_demo_offline import ask_stream_route
                response, _events = asyncio.run(ask_stream_route(request))
            else:
                response = service.ask(request).model_dump(mode="json")
            errors = []
            if response["status"] != case["expected_status"]:
                errors.append("unexpected response status")
            if attempts-before != 1:
                errors.append(f"expected one mock network attempt, got {attempts-before}")
            usage = response["model_usage"]
            if usage.get("network_call_count") != 1 or usage.get("skipped_calls", 0) < 1:
                errors.append("incorrect request/skip accounting")
            rows = (response.get("query_result") or {}).get("rows", [])
            if "wip" in case and (not rows or float(rows[0].get("wip_lots", -1)) != case["wip"]):
                errors.append("whole-FAB WIP mismatch")
            if case.get("chart") and not response.get("chart"):
                errors.append("missing chart")
            sources = {e["source_type"] for e in response.get("evidence", [])}
            if case.get("impact") and "impact_calculation" not in sources:
                errors.append("missing impact calculation")
            if case.get("documents") and ("rag_chunk" not in sources or "모델을 사용할 수 없습니다" not in response["answer"]):
                errors.append("missing document evidence or model-unavailable explanation")
            checks.append({**case, "status":response["status"], "network_attempts":attempts-before,
                           "model_usage":usage, "seconds":round(time.monotonic()-started, 3), "errors":errors, "passed":not errors})
    source = Path(__file__).resolve().parents[1] / "src"
    report = {"checked_at":datetime.now(UTC).isoformat(), "mock_transport":True, "external_model_calls":0,
              "transport":"in_process_sse_body_iterator" if args.stream_route else "chat_service",
              "source_sha256":hashlib.sha256(b"".join(p.read_bytes() for p in sorted(source.rglob("*.py")))).hexdigest(),
              "cases":len(checks), "passed":sum(check["passed"] for check in checks), "checks":checks,
              "interpretation":"Mock timeout + real local tools. Does not measure live-model latency or answer accuracy."}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({k:v for k,v in report.items() if k != "checks"}))
    for check in checks:
        print(check["id"], check["network_attempts"], check["model_usage"].get("skipped_calls"), check["errors"])
    if any(not check["passed"] for check in checks):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
