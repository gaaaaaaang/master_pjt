"""Evaluate deterministic multi-turn context normalization and precedence."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT / "src"))

from app.schemas.chat import ChatRequest
from app.services.conversation_memory import ConversationMemory

DEFAULT_FIXTURE = APP_ROOT / "tests" / "fixtures" / "context_memory_eval.json"
CONTEXT_FIELDS = (
    "fab", "line", "process", "product", "route", "equipment", "date_basis", "metric"
)


def evaluate_cases(cases: list[dict[str, Any]]) -> dict[str, Any]:
    results = []
    for case in cases:
        memory = ConversationMemory()
        conversation_id = None
        for message in case.get("history", []):
            request, _ = memory.prepare_request(
                ChatRequest(message=message, conversation_id=conversation_id)
            )
            conversation_id = request.conversation_id
            memory.append_exchange(
                conversation_id=conversation_id,
                request=request,
                answer="context recorded",
            )

        follow_up = case["follow_up"]
        request, _ = memory.prepare_request(
            ChatRequest(
                message=follow_up["message"],
                conversation_id=conversation_id,
                **(follow_up.get("request_context") or {}),
            )
        )
        actual = {field: getattr(request, field) for field in CONTEXT_FIELDS}
        failures = [
            f"{field} expected={expected} actual={actual.get(field)}"
            for field, expected in case["expected_context"].items()
            if actual.get(field) != expected
        ]
        results.append(
            {
                "id": case["id"],
                "category": case["category"],
                "passed": not failures,
                "actual_context": actual,
                "failures": failures,
            }
        )

    total = len(results)

    def category_rate(category: str) -> float:
        selected = [item for item in results if item["category"] == category]
        return (
            round(sum(item["passed"] for item in selected) / len(selected), 4)
            if selected
            else 0.0
        )

    passed = sum(item["passed"] for item in results)
    return {
        "summary": {
            "case_count": total,
            "context_accuracy_rate": round(passed / total, 4) if total else 0.0,
            "normalization_accuracy_rate": category_rate("normalization"),
            "switch_accuracy_rate": category_rate("switch"),
            "override_accuracy_rate": category_rate("override"),
            "inheritance_accuracy_rate": category_rate("inheritance"),
            "selection_inheritance_accuracy_rate": category_rate("selection_inheritance"),
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
    return 0 if report["summary"]["context_accuracy_rate"] == 1.0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
