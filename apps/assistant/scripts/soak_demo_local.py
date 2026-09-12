"""Exercise repeated demo conversations in one event loop with all models offline.

The local DB is read-only. HTTP/model calls are blocked. Document-only answers
must honestly report model unavailability; those checks are not answer successes.
An isolated temporary SQLite store is removed at the end of the run.
"""
from __future__ import annotations

import argparse
import asyncio
import gc
import hashlib
import json
import os
import resource
import sys
import threading
import time
import tracemalloc
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from evaluate_demo_conversations import verify
from evaluate_demo_offline import ask_stream_route
from evaluate_snapshot_variants import compare, expected_rows


def unavailable_model(*args, **kwargs):
    # Plain functions do not retain every prompt in Mock.call_args_list, and a
    # fresh exception cannot accumulate tracebacks from previous invocations.
    raise RuntimeError("Model unavailable: local stability test")


def forbidden_http(*args, **kwargs):
    raise AssertionError("HTTP forbidden in local stability test")


async def run(args):
    from app.api import routes
    from app.config import get_settings
    from app.schemas.chat import ChatRequest
    from app.services.conversation_memory import ConversationMemory

    settings = get_settings()
    if urlsplit(settings.postgres_dsn or "").hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Only a localhost DB is permitted")
    if settings.vector_db_url or settings.rag_reranker != "feature" or settings.mock_mode:
        raise ValueError("Real local DB and feature/BM25 retrieval are required")
    cases = json.loads(args.fixture.read_text())
    raw = json.loads(args.raw.read_text())
    source = Path(__file__).resolve().parents[1] / "src"

    def source_hash():
        return hashlib.sha256(b"".join(p.read_bytes() for p in sorted(source.rglob("*.py")))).hexdigest()

    report = {"started_at":datetime.now(UTC).isoformat(), "source_sha256":source_hash(),
              "harness_sha256":hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "external_http_blocked":True, "mode":"persistent_event_loop_model_unavailable",
              "fixture_sha256":hashlib.sha256(args.fixture.read_bytes()).hexdigest(),
              "requested_duration_seconds":args.duration_seconds, "cycles":[], "errors":[],
              "interpretation":"Local resilience and resource observations, not live-model accuracy or latency."}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    conversation_id, previous_period = None, (None, None)
    started = time.monotonic()
    tracemalloc.start(1)
    with (
        TemporaryDirectory(prefix="fab-soak-") as temporary,
        patch("app.agents.llm.AzureAgentClient.complete_json", new=unavailable_model),
        patch("app.sub_agent.text2sql.OpenAIText2SQLClient.create_sql", new=unavailable_model),
        patch("httpx.Client.send", new=forbidden_http),
        patch("httpx.AsyncClient.send", new=forbidden_http),
        patch.object(routes, "conversation_memory", ConversationMemory(store_path=Path(temporary) / "state.sqlite3")),
    ):
        while time.monotonic() - started < args.duration_seconds:
            cycle_started = time.monotonic()
            cycle = {"index":len(report["cycles"])+1, "answers_completed":0,
                     "document_model_unavailable":0, "numeric_results_checked":0, "errors":[]}
            if report["cycles"] and cycle["index"] % 5 == 0:
                routes.conversation_memory = ConversationMemory(store_path=Path(temporary) / "state.sqlite3")
            for case in cases:
                request = ChatRequest(message=case["question"], conversation_id=conversation_id, fab=case.get("request_fab"))
                response, _events = await ask_stream_route(request)
                conversation_id = response["conversation_id"]
                document_only = case.get("expects_sql") is False
                contract = {**case, "expected_status":"failed"} if document_only else case
                errors, previous_period = verify(contract, response, previous_period)
                if document_only:
                    if not any(e.get("source_type") == "rag_chunk" for e in response.get("evidence", [])):
                        errors.append("document search did not supply evidence")
                    if "모델을 사용할 수 없습니다" not in response.get("answer", ""):
                        errors.append("missing honest model-unavailable explanation")
                    cycle["document_model_unavailable"] += not errors
                else:
                    cycle["answers_completed"] += not errors
                if case.get("value_contract"):
                    value_contract = {**case, **case["value_contract"]}
                    expected, keys = expected_rows(value_contract, raw["fabs"][case["fab"]], datetime.now(ZoneInfo("Asia/Seoul")).date())
                    errors.extend(compare((response.get("query_result") or {}).get("rows", []), expected, keys, ordered=bool(value_contract.get("limit"))))
                    cycle["numeric_results_checked"] += 1
                if len(routes.conversation_memory.get_history(conversation_id)) > 24:
                    errors.append("conversation context exceeded the 12-exchange bound")
                if errors:
                    cycle["errors"].append({"id":case["id"], "errors":errors})
            gc.collect()
            current, peak = tracemalloc.get_traced_memory()
            cycle.update(seconds=round(time.monotonic()-cycle_started, 3),
                         elapsed_seconds=round(time.monotonic()-started, 3),
                         traced_current_mb=round(current/1024**2, 3), traced_peak_mb=round(peak/1024**2, 3),
                         resident_high_water_mb=round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/(1024**2 if sys.platform == "darwin" else 1024), 3),
                         threads=threading.active_count(), file_descriptors=len(os.listdir("/dev/fd")),
                         source_unchanged=source_hash() == report["source_sha256"])
            if not cycle["source_unchanged"]:
                cycle["errors"].append({"id":"source", "errors":["Application source changed during stability test"]})
            report["cycles"].append(cycle)
            report["errors"].extend(cycle["errors"])
            args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
            print(json.dumps(cycle, ensure_ascii=False), flush=True)
            if cycle["errors"]:
                break
            remaining = args.duration_seconds - (time.monotonic()-started)
            if remaining > 0:
                await asyncio.sleep(min(remaining, max(0, args.interval_seconds-cycle["seconds"])))
    report.update(finished_at=datetime.now(UTC).isoformat(), elapsed_seconds=round(time.monotonic()-started, 3),
                  passed=not report["errors"], source_unchanged=source_hash() == report["source_sha256"])
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    tracemalloc.stop()
    if report["errors"]:
        raise SystemExit(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--duration-seconds", type=int, default=1800)
    parser.add_argument("--interval-seconds", type=int, default=30)
    args = parser.parse_args()
    if not 1 <= args.duration_seconds <= 7200 or args.interval_seconds < 1:
        parser.error("Use a duration of 1..7200 seconds and a positive interval")
    os.environ["LANGSMITH_TRACING"] = "false"
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
