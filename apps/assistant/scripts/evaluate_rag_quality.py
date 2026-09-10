"""Reproduce the September 10 RAG quality evaluation on an explicitly selected source tree.

Uses real document generation and Ragas 0.2.15 with the configured same-model judge.
Loads credentials and corpus settings from the working directory .env. Preserves all
answers, retrieved evidence, traces, metric errors and source/fixture/corpus hashes.
Requires --live; generation latency excludes the subsequent judge calls.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from dataclasses import asdict
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    if not args.live:
        parser.error("--live is required to send document evidence to the configured API.")
    root = args.source_root.resolve()
    app = root / "apps/assistant"
    sys.path[:0] = [str(app / "src"), str(app / "scripts")]
    os.environ["RAGAS_DO_NOT_TRACK"] = "true"
    from app.config import get_settings

    settings = get_settings()
    fixtures = app / "tests/fixtures"
    report = {
        "suite": "rag",
        "source_root": str(root),
        "source_sha256": hashlib.sha256(
            b"".join(
                str(p.relative_to(app)).encode() + b"\0" + p.read_bytes()
                for p in sorted((app / "src").rglob("*.py"))
            )
        ).hexdigest(),
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "model": settings.openai_model,
        "same_model_judge": True,
        "rag_reranker": settings.rag_reranker,
        "dense_retrieval_configured": bool(settings.vector_db_url),
        "results": [],
    }

    def load(name):
        path = fixtures / name
        report.setdefault("fixture_sha256", {})[name] = hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        return [{**x, "fixture": name} for x in json.loads(path.read_text())]

    def save(row):
        report["results"].append(row)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        print(
            json.dumps(
                {k: row[k] for k in ("id", "seconds", "error", "scores") if k in row},
                ensure_ascii=False,
            ),
            flush=True,
        )

    import asyncio

    import ragas
    from app.rag.grounding import compose_grounded
    from app.sub_agent.rag import retrieve_evidence
    from langchain_openai import AzureChatOpenAI, AzureOpenAIEmbeddings
    from ragas import SingleTurnSample
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.llms import LangchainLLMWrapper
    from ragas.metrics import (
        Faithfulness,
        LLMContextPrecisionWithoutReference,
        ResponseRelevancy,
    )

    report["ragas_version"] = ragas.__version__
    llm = LangchainLLMWrapper(
        AzureChatOpenAI(
            azure_endpoint=settings.openai_endpoint,
            api_key=settings.openai_api_key,
            api_version=settings.openai_api_version,
            azure_deployment=settings.openai_model,
            temperature=0,
            timeout=90,
            max_retries=1,
        )
    )
    emb = LangchainEmbeddingsWrapper(
        AzureOpenAIEmbeddings(
            azure_endpoint=settings.openai_endpoint,
            api_key=settings.openai_api_key,
            api_version=settings.openai_api_version,
            azure_deployment=settings.embedding_model,
            model=settings.embedding_model,
            check_embedding_ctx_length=False,
            max_retries=1,
        )
    )
    metrics = [
        Faithfulness(llm=llm),
        ResponseRelevancy(llm=llm, embeddings=emb),
        LLMContextPrecisionWithoutReference(llm=llm),
    ]
    cases = load("rag_extension_eval.json")
    report["planned_cases"] = len(cases[: args.limit])
    report["corpus_sha256"] = hashlib.sha256(
        Path(settings.rag_local_store_path).read_bytes()
    ).hexdigest()
    for case in cases[: args.limit]:
        started = time.monotonic()
        try:
            retrieval = retrieve_evidence(case["query"], knowledge_base=case["knowledge_base"])
            evidence = [x.model_dump() for x in retrieval.evidence]
            answer = compose_grounded(case["query"], evidence)
            row = {
                "id": case["id"],
                "question": case["query"],
                "reference_review": case["review"],
                "evidence": evidence,
                "trace": retrieval.trace,
                "answer": asdict(answer),
                "seconds": time.monotonic() - started,
                "scores": {},
                "metric_errors": {},
            }
            sample = SingleTurnSample(
                user_input=case["query"],
                response=answer.answer,
                retrieved_contexts=[x["content"] for x in evidence],
            )
            for metric in metrics:
                try:
                    score = asyncio.run(metric.single_turn_ascore(sample))
                    import math

                    row["scores"][metric.name] = score if math.isfinite(score) else None
                except Exception as exc:  # noqa: BLE001 - preserve each evaluation failure and continue
                    row["metric_errors"][metric.name] = str(exc)
            save(row)
        except Exception as exc:  # noqa: BLE001 - preserve each evaluation failure and continue
            save({"id": case["id"], "error": str(exc), "seconds": time.monotonic() - started})
    report["completed"] = True
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n")


if __name__ == "__main__":
    main()
