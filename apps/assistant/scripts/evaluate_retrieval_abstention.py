"""Measure RAG and CaseSearch abstention on unrelated negative queries."""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT / "src"))

from app.config import get_settings
from app.sub_agent.case_search import find_similar_cases
from app.sub_agent.rag import retrieve_knowledge

DEFAULT_FIXTURE = APP_ROOT / "tests" / "fixtures" / "retrieval_abstention_eval.json"


def evaluate_cases(cases: list[dict[str, Any]]) -> dict[str, Any]:
    settings = get_settings()
    results = []
    for case in cases:
        if case["agent"] == "rag":
            evidence = retrieve_knowledge(
                case["query"],
                top_k=3,
                knowledge_base=case["knowledge_base"],
                store_path=Path(settings.rag_local_store_path),
            )
        else:
            evidence = find_similar_cases(
                case["query"],
                top_k=3,
                store_path=Path(settings.incident_case_store_path),
            )
        results.append(
            {
                "id": case["id"],
                "agent": case["agent"],
                "returned_count": len(evidence),
                "abstained": not evidence,
            }
        )

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for result in results:
        grouped[result["agent"]].append(result)
    by_agent = {
        agent: {
            "case_count": len(items),
            "abstention_rate": round(
                sum(item["abstained"] for item in items) / len(items), 4
            ),
        }
        for agent, items in sorted(grouped.items())
    }
    total = len(results)
    abstained = sum(item["abstained"] for item in results)
    return {
        "summary": {
            "case_count": total,
            "abstention_rate": round(abstained / total, 4) if total else 0.0,
            "agents": by_agent,
            "failed_case_ids": [item["id"] for item in results if not item["abstained"]],
        },
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()
    report = evaluate_cases(json.loads(args.fixture.read_text(encoding="utf-8")))
    print(json.dumps(report if args.as_json else report["summary"], ensure_ascii=False, indent=2))
    return 0 if report["summary"]["abstention_rate"] == 1.0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
