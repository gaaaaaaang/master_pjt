"""Measure offline deterministic Text2SQL coverage and semantic contract accuracy."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

APP_ROOT = Path(__file__).resolve().parents[1]
APP_SRC = APP_ROOT / "src"
sys.path.insert(0, str(APP_ROOT))
sys.path.insert(0, str(APP_SRC))

from app.sub_agent.text2sql import answer_question, plan_text2sql

from scripts.smoke_text2sql_postgres import evaluate_case

DEFAULT_FIXTURE = APP_ROOT / "tests" / "fixtures" / "text2sql_fab10_eval.json"
OFFLINE_QUERY_TYPES = {
    "status",
    "trend",
    "master_data_lookup",
    "release_plan_lookup",
    "unsupported",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Execute only deterministic/guarded status and trend cases against local PostgreSQL.",
    )
    return parser.parse_args()


def evaluate_deterministic_cases(
    cases: list[dict[str, Any]],
    *,
    execute: bool = False,
) -> dict[str, Any]:
    eligible = [case for case in cases if case.get("expected_query_type") in OFFLINE_QUERY_TYPES]
    results = []
    for case in eligible:
        fab = case.get("fab_context", "fab10")
        result = (
            answer_question(
                case["question"],
                fab=fab,
                execute=True,
                deterministic_only=True,
            )
            if execute
            else plan_text2sql(case["question"], fab=fab, deterministic_only=True)
        )
        evaluated_case = case
        if not execute:
            evaluated_case = {
                key: value
                for key, value in case.items()
                if key not in {
                    "min_rows",
                    "expected_answer_contains",
                    "expected_limitation_contains",
                }
            }
        validation = evaluate_case(evaluated_case, result)
        template_id = result.plan.template_id if result.plan else None
        results.append(
            {
                "id": case["id"],
                "passed": not validation["all"],
                "status": result.status,
                "query_type": result.query_type,
                "has_sql": bool(result.sql),
                "template_id": template_id,
                "deterministic": bool(
                    template_id and template_id.startswith("deterministic_")
                ),
                "row_count": result.row_count,
                "ex_ok": not validation["ex"],
                "em_ok": not validation["em"],
                "intent_ok": not validation["intent"],
                "failures": validation["all"],
            }
        )

    total = len(results)
    passed = sum(item["passed"] for item in results)
    deterministic = sum(item["deterministic"] for item in results)
    expected_sql = sum(bool(case.get("expect_sql")) for case in eligible)
    ex_pass = sum(item["ex_ok"] for item in results)
    em_pass = sum(item["em_ok"] for item in results)
    intent_pass = sum(item["intent_ok"] for item in results)
    return {
        "summary": {
            "mode": "local_execution" if execute else "generation_only",
            "eligible_cases": total,
            "semantic_pass": passed,
            "semantic_pass_rate": round(passed / total, 4) if total else 0.0,
            "ex_pass": ex_pass,
            "ex_rate": round(ex_pass / total, 4) if total else 0.0,
            "em_pass": em_pass,
            "em_rate": round(em_pass / total, 4) if total else 0.0,
            "intent_pass": intent_pass,
            "intent_rate": round(intent_pass / total, 4) if total else 0.0,
            "expected_sql_cases": expected_sql,
            "deterministic_sql_cases": deterministic,
            "deterministic_sql_coverage": (
                round(deterministic / expected_sql, 4) if expected_sql else 0.0
            ),
            "failed_case_ids": [item["id"] for item in results if not item["passed"]],
        },
        "results": results,
    }


def main() -> int:
    args = parse_args()
    cases = json.loads(args.fixture.read_text(encoding="utf-8"))
    report = evaluate_deterministic_cases(cases, execute=args.execute)
    print(
        json.dumps(
            report if args.as_json else report["summary"],
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if report["summary"]["semantic_pass_rate"] == 1.0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
