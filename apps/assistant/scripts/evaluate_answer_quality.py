"""Evaluate deterministic final-answer checks with positive and negative SC-001..SC-004 cases."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT / "src"))

from app.sub_agent.reflection import verify_response

DEFAULT_FIXTURE = APP_ROOT / "tests" / "fixtures" / "answer_quality_eval.json"


def evaluate_cases(cases: list[dict[str, Any]]) -> dict[str, Any]:
    results = []
    for case in cases:
        review = verify_response(
            case["answer"],
            evidence=case["evidence"],
            limitations=case["limitations"],
            query_type=case["query_type"],
            question=case["question"],
        )
        expected = bool(case["expected_supported"])
        passed = review["is_supported"] is expected
        results.append(
            {
                "id": case["id"],
                "scenario_id": case["scenario_id"],
                "expected_supported": expected,
                "actual_supported": review["is_supported"],
                "quality_score": review["quality_score"],
                "passed": passed,
                "warnings": review["warnings"],
            }
        )

    total = len(results)
    positives = [item for item in results if item["expected_supported"]]
    negatives = [item for item in results if not item["expected_supported"]]
    passed = sum(item["passed"] for item in results)
    return {
        "summary": {
            "case_count": total,
            "classification_accuracy": round(passed / total, 4) if total else 0.0,
            "positive_accept_rate": round(
                sum(item["actual_supported"] for item in positives) / len(positives), 4
            )
            if positives
            else 0.0,
            "negative_reject_rate": round(
                sum(not item["actual_supported"] for item in negatives) / len(negatives), 4
            )
            if negatives
            else 0.0,
            "failed_case_ids": [item["id"] for item in results if not item["passed"]],
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
    return 0 if report["summary"]["classification_accuracy"] == 1.0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
