"""Inspect official Ragas 0.2.15 relevance components on repeated FIXED answers."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    if not args.live:
        parser.error("--live is required.")
    os.environ["RAGAS_DO_NOT_TRACK"] = "true"
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from app.config import get_settings
    from langchain_openai import AzureChatOpenAI, AzureOpenAIEmbeddings
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.llms import LangchainLLMWrapper
    from ragas.metrics import ResponseRelevancy
    from ragas.metrics._answer_relevance import ResponseRelevanceInput

    settings = get_settings()
    if settings.openai_endpoint.rstrip("/") != "https://skax.ai-talentlab.com":
        parser.error("Only the previously authorized destination is allowed.")
    common = {"azure_endpoint": settings.openai_endpoint, "api_key": settings.openai_api_key,
              "api_version": settings.openai_api_version, "max_retries": 1}
    metric = ResponseRelevancy(
        llm=LangchainLLMWrapper(AzureChatOpenAI(
            **common, azure_deployment=settings.openai_model, temperature=0, timeout=60,
        )),
        embeddings=LangchainEmbeddingsWrapper(AzureOpenAIEmbeddings(
            **common, azure_deployment=settings.embedding_model, model=settings.embedding_model,
            check_embedding_ctx_length=False,
        )),
    )
    report = {"source_report": str(args.report), "fixed_answers": True,
              "metric": "Ragas 0.2.15 ResponseRelevancy", "results": []}
    cases = [c for c in json.loads(args.report.read_text())["results"]
             if c["id"] in {"ext_pm_approval", "ext_knowledge_update"}]

    async def components(answer):
        return await asyncio.gather(*[
            metric.question_generation.generate(
                data=ResponseRelevanceInput(response=answer), llm=metric.llm, callbacks=[],
            ) for _ in range(metric.strictness)
        ])

    args.output.parent.mkdir(parents=True, exist_ok=True)
    for case in cases:
        for repeat in range(3):
            answers = asyncio.run(components(case["answer"]["answer"]))
            similarities = metric.calculate_similarity(case["question"], [a.question for a in answers])
            gated = any(a.noncommittal for a in answers)
            row = {"id": case["id"], "repeat": repeat + 1,
                   "response": case["answer"]["answer"],
                   "generated": [a.model_dump() for a in answers],
                   "similarities": similarities.tolist(), "noncommittal_gate": gated,
                   "score": float(similarities.mean()) * int(not gated)}
            report["results"].append(row)
            args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
            print(json.dumps({k: row[k] for k in ["id", "repeat", "score", "noncommittal_gate"]}), flush=True)


if __name__ == "__main__":
    main()
