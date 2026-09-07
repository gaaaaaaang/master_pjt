"""Bounded real Azure embedding + Milvus + LLM reranker evaluation on existing fixtures."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from time import perf_counter

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT / "src"))
from app.agents.llm_nodes import compose_with_llm
from app.agents.planner import PlannerDecision
from app.config import Settings, get_settings
from app.rag.manifest import atomic_write
from app.schemas.chat import Evidence
from app.sub_agent.rag import _to_evidence, retrieve_with_trace
from evaluate_rag_search import evaluate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--uri", default="http://127.0.0.1:19530")
    parser.add_argument("--collection", default="master_pjt_rag_api_eval")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--compose", action="store_true")
    parser.add_argument("--case-id", action="append")
    parser.add_argument(
        "--replay-report", type=Path, help="Reuse saved retrieval; call only Composer."
    )
    args = parser.parse_args()
    if not args.live:
        parser.error("--live is required for document transmission to the configured API.")
    settings = Settings(_env_file=args.env_file)
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
    store = APP_ROOT / "output/rag/master_pjt_v2.jsonl"
    os.environ.update(
        VECTOR_DB_URL=args.uri,
        VECTOR_DB_COLLECTION=args.collection,
        RAG_INDEX_MANIFEST_PATH=str(args.manifest.resolve()),
        RAG_LOCAL_STORE_PATH=str(store),
        RAG_RERANKER="llm",
        RAG_RERANK_LIMIT="12",
    )
    get_settings.cache_clear()
    fixtures = []
    for name in ("rag_search_eval.json", "rag_search_challenge.json"):
        fixtures.extend(json.loads((APP_ROOT / "tests/fixtures" / name).read_text()))
    by_id = {row["id"]: row for row in fixtures}
    selected = ["pm", "multi", "exact", "q01", "q15", "q23", "q28", "unknown_recipe", "q30"]
    selected = args.case_id or selected
    cases = [by_id[cid] for cid in selected]
    details = []
    saved = {}

    def live_retrieve(query, top_k, *, knowledge_base, store_path):
        del store_path
        start = perf_counter()
        result = retrieve_with_trace(query, top_k, knowledge_base=knowledge_base)
        evidence = [_to_evidence(chunk, chunk["metadata"]["score"]) for chunk in result.chunks]
        for item in evidence:
            item.metadata["retrieval_trace"] = result.trace
            item.metadata["retrieval_limitations"] = result.limitations
        saved[query] = evidence
        detail = {
            "query": query,
            "seconds": round(perf_counter() - start, 3),
            "evidence": [e.model_dump() for e in evidence],
            "trace": result.trace,
        }
        details.append(detail)
        atomic_write(
            args.output.with_suffix(".partial.json"),
            json.dumps(details, ensure_ascii=False, indent=2),
        )
        print(
            json.dumps(
                {
                    "case": len(details),
                    "seconds": detail["seconds"],
                    "chunks": len(evidence),
                    "reranker": (detail["trace"] or {}).get("reranker"),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        return evidence

    if args.replay_report:
        previous = json.loads(args.replay_report.read_text())
        if previous["case_ids"] != selected:
            parser.error("Replay case IDs must match the saved report.")
        baseline, live, details = previous["local"], previous["live"], previous["details"]
        saved = {
            row["query"]: [Evidence.model_validate(e) for e in row["evidence"]] for row in details
        }
    else:
        baseline = evaluate(cases, store_path=store)
        live = evaluate(cases, store_path=store, retrieve_fn=live_retrieve)
    generations = []
    if args.compose:
        for cid in (key for key in ("pm", "multi", "unknown_recipe") if key in selected):
            case = by_id[cid]
            evidence = saved[case["query"]]
            limitations = list(
                dict.fromkeys(
                    limitation
                    for e in evidence
                    for limitation in e.metadata.get("retrieval_limitations", [])
                )
            )
            limitations.append(
                "검색 문서는 시뮬레이션 참조 자료이며 실제 사내 SOP가 아닙니다."
                if evidence
                else "질문을 뒷받침하는 문서 근거를 찾지 못했습니다."
            )
            plan = PlannerDecision(
                status="ready",
                query_type="knowledge_lookup",
                intent=case["query"],
                selected_sub_agents=["rag"],
                execution_steps=[],
                rag_knowledge_base=case["knowledge_base"],
            )
            start = perf_counter()
            answer = compose_with_llm(
                question=case["query"],
                plan=plan,
                answer_parts=[],
                evidence=[e.model_dump() for e in evidence],
                limitations=limitations,
                reflection={},
            )
            generations.append(
                {
                    "case_id": cid,
                    "answer": answer,
                    "seconds": round(perf_counter() - start, 3),
                    "source_chunk_ids": [e.metadata["chunk_id"] for e in evidence],
                }
            )
            print(
                json.dumps(
                    {"composed": cid, "seconds": generations[-1]["seconds"]}, ensure_ascii=False
                ),
                flush=True,
            )
    report = {
        "backend": "actual_azure_embeddings_milvus_llm",
        "retrieval_replayed_from": str(args.replay_report) if args.replay_report else None,
        "model": settings.openai_model,
        "embedding_model": settings.embedding_model,
        "case_ids": selected,
        "scope": "Explicit fixture KB; Composer tested separately. Not a full Planner/Supervisor test.",
        "local": baseline,
        "live": live,
        "details": details,
        "generations": generations,
    }
    atomic_write(args.output, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps({"local": baseline["metrics"], "live": live["metrics"]}, ensure_ascii=False),
        flush=True,
    )


if __name__ == "__main__":
    main()
