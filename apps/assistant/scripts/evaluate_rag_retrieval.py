"""Measure local FAB RAG retrieval Recall@K and MRR against a golden fixture."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

APP_ROOT = Path(__file__).resolve().parents[1]
APP_SRC = APP_ROOT / "src"
sys.path.insert(0, str(APP_SRC))

from app.config import get_settings
from app.sub_agent.rag import retrieve_knowledge

DEFAULT_FIXTURE = APP_ROOT / "tests" / "fixtures" / "rag_retrieval_eval.json"


def parse_args() -> argparse.Namespace:
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--store-path", type=Path, default=Path(settings.rag_local_store_path))
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--json", action="store_true", dest="as_json")
    return parser.parse_args()


def evaluate_retrieval(
    cases: list[dict[str, Any]],
    *,
    store_path: Path,
    top_k: int,
) -> dict[str, Any]:
    results = []
    reciprocal_ranks = []
    for case in cases:
        evidence = retrieve_knowledge(
            case["query"],
            top_k=top_k,
            knowledge_base=case["knowledge_base"],
            store_path=store_path,
        )
        retrieved = [str(item.metadata.get("chunk_id")) for item in evidence]
        relevant = set(case["relevant_chunk_ids"])
        first_rank = next(
            (index for index, chunk_id in enumerate(retrieved, start=1) if chunk_id in relevant),
            None,
        )
        reciprocal_rank = 1.0 / first_rank if first_rank else 0.0
        reciprocal_ranks.append(reciprocal_rank)
        results.append(
            {
                "id": case["id"],
                "knowledge_base": case["knowledge_base"],
                "query": case["query"],
                "relevant_chunk_ids": sorted(relevant),
                "retrieved_chunk_ids": retrieved,
                "first_relevant_rank": first_rank,
                "reciprocal_rank": round(reciprocal_rank, 4),
                "hit": first_rank is not None,
                "incident_issue_alignment": (
                    all(item.metadata.get("issue_aligned") is True for item in evidence)
                    if case["knowledge_base"] == "incident_playbook"
                    else None
                ),
                "incident_evidence_count": (
                    len(evidence) if case["knowledge_base"] == "incident_playbook" else 0
                ),
            }
        )

    total = len(results)
    hits = sum(item["hit"] for item in results)
    incident_results = [
        item for item in results if item["knowledge_base"] == "incident_playbook"
    ]
    incident_evidence_count = sum(item["incident_evidence_count"] for item in incident_results)
    aligned_evidence_count = sum(
        item["incident_evidence_count"]
        for item in incident_results
        if item["incident_issue_alignment"]
    )
    summary = {
        "case_count": total,
        f"recall_at_{top_k}": round(hits / total, 4) if total else 0.0,
        "mrr": round(sum(reciprocal_ranks) / total, 4) if total else 0.0,
        "incident_issue_alignment_rate": (
            round(aligned_evidence_count / incident_evidence_count, 4)
            if incident_evidence_count
            else 0.0
        ),
        "failed_case_ids": [item["id"] for item in results if not item["hit"]],
    }
    return {"summary": summary, "results": results}


def main() -> int:
    args = parse_args()
    cases = json.loads(args.fixture.read_text(encoding="utf-8"))
    report = evaluate_retrieval(cases, store_path=args.store_path, top_k=args.top_k)
    if args.as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
        for result in report["results"]:
            mark = "PASS" if result["hit"] else "FAIL"
            print(f"{mark} {result['id']} rank={result['first_relevant_rank']}")
    return 0 if report["summary"][f"recall_at_{args.top_k}"] == 1.0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
