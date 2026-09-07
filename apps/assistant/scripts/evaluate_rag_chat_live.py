"""Exercise actual FastAPI chat routes and every selected graph node with live APIs.

Uses in-process ASGI transport, not a deployed server or a load test. No mocks of
Planner, Supervisor, retrieval, Reflection or Composer are installed. Questions
are document-only; database credentials are deliberately not copied into runtime.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT / "src"))
from app.config import Settings, get_settings
from app.main import app
from app.rag.manifest import atomic_write
from evaluate_rag_search import covered_units
from fastapi.testclient import TestClient


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument(
        "--fixture", type=Path, default=APP_ROOT / "tests/fixtures/rag_extension_eval.json"
    )
    parser.add_argument(
        "--manifest", type=Path, default=APP_ROOT / "output/rag/api_eval_manifest.json"
    )
    parser.add_argument("--collection", default="master_pjt_rag_api_eval")
    parser.add_argument("--uri", default="http://127.0.0.1:19530")
    parser.add_argument("--case-id", action="append")
    parser.add_argument("--mode", choices=["chat", "stream"], default="stream")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.live:
        parser.error(
            "--live is required to send document evidence and questions to the configured API."
        )
    settings = Settings(_env_file=args.env_file)
    if settings.openai_endpoint.rstrip("/") != "https://skax.ai-talentlab.com":
        parser.error("This evaluation is authorized only for https://skax.ai-talentlab.com.")
    for key in (
        "openai_api_key",
        "openai_model",
        "openai_endpoint",
        "openai_api_version",
        "embedding_model",
        "embedding_dimension",
        "embedding_revision",
    ):
        value = getattr(settings, key)
        if value is not None:
            os.environ[key.upper()] = str(value)
    os.environ.update(
        VECTOR_DB_URL=args.uri,
        VECTOR_DB_COLLECTION=args.collection,
        RAG_INDEX_MANIFEST_PATH=str(args.manifest.resolve()),
        RAG_LOCAL_STORE_PATH=str(APP_ROOT / "output/rag/master_pjt_v2.jsonl"),
        RAG_RERANKER="llm",
        POSTGRES_DSN="",
        MYSQL_DSN="",
    )
    get_settings.cache_clear()
    cases = json.loads(args.fixture.read_text())
    if args.case_id:
        by_id = {c["id"]: c for c in cases}
        cases = [by_id[cid] for cid in args.case_id]
    report = {
        "scope": "Real FastAPI routes via in-process ASGI; real LLM and Milvus; no deployed-server/load test.",
        "git_head": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=APP_ROOT, text=True
        ).strip(),
        "fixture_sha256": hashlib.sha256(args.fixture.read_bytes()).hexdigest(),
        "source_sha256": hashlib.sha256(
            b"".join(
                str(path.relative_to(APP_ROOT)).encode() + b"\0" + path.read_bytes()
                for path in sorted((APP_ROOT / "src").rglob("*.py"))
            )
        ).hexdigest(),
        "corpus_sha256": hashlib.sha256(
            (APP_ROOT / "output/rag/master_pjt_v2.jsonl").read_bytes()
        ).hexdigest(),
        "manifest_sha256": hashlib.sha256(args.manifest.read_bytes()).hexdigest(),
        "started_at_utc": datetime.now(UTC).isoformat(),
        "endpoint": settings.openai_endpoint,
        "mode": args.mode,
        "cases": [],
    }
    with TestClient(app, raise_server_exceptions=False) as client:
        for case in cases:
            started = perf_counter()
            response = client.post(
                "/api/chat/stream" if args.mode == "stream" else "/api/chat",
                json={"message": case["query"]},
            )
            events, final = [], {}
            if response.status_code == 200:
                if args.mode == "stream":
                    for frame in response.text.split("\n\n"):
                        event, data = None, None
                        for line in frame.splitlines():
                            if line.startswith("event: "):
                                event = line[7:]
                            if line.startswith("data: "):
                                data = json.loads(line[6:])
                        if event and data:
                            if event != "final":
                                events.append({"event": event, "payload": data})
                            if event == "final":
                                final = data["data"]
                else:
                    final = response.json()
            evidence = [e for e in final.get("evidence", []) if e["source_type"] == "rag_chunk"]
            expected = set(case["expected_markers"])
            found = set().union(*(covered_units(e["content"], expected) for e in evidence))
            row = {
                "id": case["id"],
                "query": case["query"],
                "seconds": round(perf_counter() - started, 3),
                "http_status": response.status_code,
                "final_received": bool(final),
                "expected_markers": sorted(expected),
                "missing_units": sorted(expected - found),
                "complete_retrieval": bool(expected) and expected == found,
                "rag_count": len(evidence),
                "review_rubric": case["review"],
                "final": final,
                "events": events,
            }
            report["cases"].append(row)
            atomic_write(args.output, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
            print(
                json.dumps(
                    {
                        key: row[key]
                        for key in (
                            "id",
                            "seconds",
                            "http_status",
                            "final_received",
                            "missing_units",
                            "rag_count",
                        )
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )


if __name__ == "__main__":
    main()
