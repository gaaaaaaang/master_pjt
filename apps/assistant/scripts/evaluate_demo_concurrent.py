"""Check four simultaneous FAB conversations using local tools, with models off."""
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
from zoneinfo import ZoneInfo

from evaluate_demo_conversations import verify
from evaluate_demo_offline import ask_stream_route
from evaluate_snapshot_variants import compare, expected_rows
from soak_demo_local import forbidden_http, unavailable_model


async def main_async(args):
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
    report = {"started_at":datetime.now(UTC).isoformat(), "external_http_blocked":True,
              "source_sha256":hashlib.sha256(b"".join(p.read_bytes() for p in sorted(source.rglob("*.py")))).hexdigest(),
              "harness_sha256":hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "mode":"four_concurrent_fabs_model_unavailable", "rounds":args.rounds}

    async def conversation(fab):
        selected = [case for case in cases if case["fab"] == fab]
        conversation_id, period = None, (None, None)
        checks = []
        started = time.monotonic()
        for round_number in range(args.rounds):
            for case in selected:
                response, _events = await ask_stream_route(ChatRequest(message=case["question"], fab=case.get("request_fab"), conversation_id=conversation_id))
                conversation_id = response["conversation_id"]
                document = case.get("expects_sql") is False
                contract = {**case, "expected_status":"failed"} if document else case
                errors, period = verify(contract, response, period)
                if document and (not any(e.get("source_type") == "rag_chunk" for e in response.get("evidence", []))
                                 or "모델을 사용할 수 없습니다" not in response.get("answer", "")):
                    errors.append("missing document evidence or honest model-unavailable explanation")
                if case.get("value_contract"):
                    value_contract = {**case, **case["value_contract"]}
                    expected, keys = expected_rows(value_contract, raw["fabs"][fab], datetime.now(ZoneInfo("Asia/Seoul")).date())
                    errors.extend(compare((response.get("query_result") or {}).get("rows", []), expected, keys, ordered=bool(value_contract.get("limit"))))
                checks.append({"id":case["id"], "round":round_number+1, "status":response["status"],
                               "document_model_unavailable":document, "numeric_checked":bool(case.get("value_contract")),
                               "passed":not errors, "errors":errors})
        return {"fab":fab, "conversation_id":conversation_id, "seconds":round(time.monotonic()-started, 3), "checks":checks}

    with (
        TemporaryDirectory(prefix="fab-concurrent-") as temporary,
        patch("app.agents.llm.AzureAgentClient.complete_json", new=unavailable_model),
        patch("app.sub_agent.text2sql.OpenAIText2SQLClient.create_sql", new=unavailable_model),
        patch("httpx.Client.send", new=forbidden_http),
        patch("httpx.AsyncClient.send", new=forbidden_http),
        patch.object(routes, "conversation_memory", ConversationMemory(store_path=Path(temporary) / "state.sqlite3")),
    ):
        report["conversations"] = await asyncio.gather(*(conversation(fab) for fab in sorted({case["fab"] for case in cases})))
    checks = [check for item in report["conversations"] for check in item["checks"]]
    distinct = len({item["conversation_id"] for item in report["conversations"]}) == len(report["conversations"])
    report.update(cases=len(checks), passed=sum(check["passed"] for check in checks),
                  distinct_conversations=distinct, numeric_checks=sum(check["numeric_checked"] for check in checks),
                  answers_completed=sum(check["passed"] and not check["document_model_unavailable"] for check in checks),
                  expected_document_unavailability=sum(check["passed"] and check["document_model_unavailable"] for check in checks),
                  finished_at=datetime.now(UTC).isoformat())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({k:v for k,v in report.items() if k != "conversations"}, ensure_ascii=False))
    if not distinct or any(not check["passed"] for check in checks):
        raise SystemExit(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rounds", type=int, default=2)
    args = parser.parse_args()
    if not 1 <= args.rounds <= 10:
        parser.error("Use 1..10 rounds")
    os.environ["LANGSMITH_TRACING"] = "false"
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
