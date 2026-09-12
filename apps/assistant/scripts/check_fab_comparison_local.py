"""Local DB + real SSE route regression. All model and HTTP calls are blocked."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.parse import urlsplit

from evaluate_demo_offline import ask_stream_route


def unavailable(*args, **kwargs):
    raise RuntimeError("Model unavailable: local comparison regression")


def forbidden(*args, **kwargs):
    raise AssertionError("External HTTP forbidden")


async def run(output):
    from app.api import routes
    from app.config import get_settings
    from app.schemas.chat import ChatRequest
    from app.services.conversation_memory import ConversationMemory

    settings = get_settings()
    assert urlsplit(settings.postgres_dsn).hostname in {"localhost", "127.0.0.1", "::1"}
    assert not settings.vector_db_url and settings.rag_reranker == "feature"
    questions = ["fab11 wip은 몇개야??", "fab13 wip은 몇개야??",
                 "방금 답한 fab11이랑 fab13의 wip 차이에 대해서 분석해줄래?? 왜 fab11에는 더 쌓였는지 궁금해. 시각화 자료도 그려줄 수 있음 그려줘",
                 "방금 조회한 두 FAB의 WIP 차이를 비교해줘",
                 "FAB11과 FAB13의 최근 24시간 WIP 추세를 비교해서 그래프로 보여줘"]
    report = {"mode": "local_database_model_unavailable", "external_http_blocked": True,
              "memory_reloaded_each_turn": True, "results": []}
    with TemporaryDirectory(prefix="fab-comparison-") as temporary, \
            patch("app.agents.llm.AzureAgentClient.complete_json", new=unavailable), \
            patch("app.sub_agent.text2sql.OpenAIText2SQLClient.create_sql", new=unavailable), \
            patch("httpx.Client.send", new=forbidden), patch("httpx.AsyncClient.send", new=forbidden), \
            patch.object(routes, "conversation_memory", ConversationMemory()):
        conversation_id = None
        for index, question in enumerate(questions):
            routes.conversation_memory = ConversationMemory(store_path=Path(temporary) / "state.sqlite3")
            response, events = await ask_stream_route(ChatRequest(message=question, conversation_id=conversation_id))
            conversation_id = response["conversation_id"]
            rows = (response.get("query_result") or {}).get("rows") or []
            errors = []
            if response["status"] != "succeeded":
                errors.append("answer_not_completed")
            if index >= 2:
                if {row.get("fab") for row in rows} != {"FAB11", "FAB13"}:
                    errors.append("missing_comparison_fab")
                if not response.get("chart"):
                    errors.append("missing_chart")
            if index in {2, 3}:
                totals = {fab: sum(row["wip_lots"] for row in rows if row.get("fab") == fab) for fab in ["FAB11", "FAB13"]}
                if totals != {"FAB11": 188, "FAB13": 159}:
                    errors.append("snapshot_reference_mismatch")
            if index < 2 and "시뮬레이션" in response["answer"]:
                errors.append("repetitive_provenance_in_answer")
            if not response.get("data_sources"):
                errors.append("missing_provenance_metadata")
            report["results"].append({"question": question, "response": response, "event_count": len(events), "errors": errors})
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))
            print(json.dumps({"turn": index+1, "status": response["status"], "rows": len(rows), "errors": errors}, ensure_ascii=False), flush=True)
    report["passed"] = all(not row["errors"] for row in report["results"])
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return report["passed"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    os.environ["LANGSMITH_TRACING"] = "false"
    raise SystemExit(0 if asyncio.run(run(args.output)) else 1)
