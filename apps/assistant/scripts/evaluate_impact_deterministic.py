"""Run the SC-003 impact contract without Azure or database access."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT / "src"))

from app.sub_agent.impact import estimate_output_delta

FIXTURE = APP_ROOT / "tests" / "fixtures" / "scenario_acceptance_questions.json"
BASELINES: dict[str, dict[str, Any]] = {
    "sc003_queue_time_output_impact": {
        "rows": [{"cycleavg": 12.0, "lotcomps": 1000}],
        "columns": ["cycleavg", "lotcomps"],
        "source_tables": ["fab10.autosched_perf"],
    },
    "sc003_util_drop_capacity_impact": {
        "rows": [{"util_percent": 80.0, "lotcomps": 1000}],
        "columns": ["util_percent", "lotcomps"],
        "source_tables": ["fab10.autosched_stngrp"],
    },
    "sc003_station_down_output_impact": {
        "rows": [{"down_percent": 4.0, "lotcomps": 1000}],
        "columns": ["down_percent", "lotcomps"],
        "source_tables": ["fab10.autosched_stn", "fab10.autosched_perf"],
    },
    "sc003_product_cycle_time_impact": {
        "rows": [{"cycleavg": 10.0, "ontime_percent": 90.0}],
        "columns": ["cycleavg", "ontime_percent"],
        "source_tables": ["fab10.autosched_part"],
    },
    "sc003_compound_util_cycle_impact": {
        "rows": [
            {
                "util_percent": 80.0,
                "cycleavg": 10.0,
                "lotcomps": 1000.0,
                "ontime_percent": 90.0,
            }
        ],
        "columns": ["util_percent", "cycleavg", "lotcomps", "ontime_percent"],
        "source_tables": ["fab10.autosched_perf", "fab10.autosched_stngrp"],
    },
}

VARIANT_CASES = [
    {
        "id": "sc003_direction_uses_changed_metric_clause",
        "question": "utilization이 5%p 증가하면 capacity가 감소하나?",
        "expected_current_status": "succeeded",
        "baseline": {"rows": [{"util_percent": 80.0}], "columns": ["util_percent"]},
        "expected_direction": 1,
        "expected_estimates": {"projected_util_percent": 85.0, "capacity_delta_percent": 6.25},
    },
    {
        "id": "sc003_ambiguous_direction_rejected",
        "question": "utilization이 5%p 변하면 capacity 영향은?",
        "expected_current_status": "data_unavailable",
        "baseline": {"rows": [{"util_percent": 80.0}], "columns": ["util_percent"]},
        "expected_direction": None,
    },
    {
        "id": "sc003_outcome_direction_is_not_input_direction",
        "question": "utilization이 5%p 변하면 capacity가 감소하나?",
        "expected_current_status": "data_unavailable",
        "baseline": {"rows": [{"util_percent": 80.0}], "columns": ["util_percent"]},
        "expected_direction": None,
        "expected_limitation_contains": "방향",
    },
    {
        "id": "sc003_explicit_signed_drop",
        "question": "utilization이 -5%p 변하면 capacity 영향은?",
        "expected_current_status": "succeeded",
        "baseline": {"rows": [{"util_percent": 80.0}], "columns": ["util_percent"]},
        "expected_direction": -1,
        "expected_estimates": {"projected_util_percent": 75.0, "capacity_delta_percent": -6.25},
    },
    {
        "id": "sc003_conflicting_sign_and_word_rejected",
        "question": "utilization이 -5%p 증가하면 capacity 영향은?",
        "expected_current_status": "data_unavailable",
        "baseline": {"rows": [{"util_percent": 80.0}], "columns": ["util_percent"]},
        "expected_direction": None,
    },
    {
        "id": "sc003_physical_utilization_range_rejected",
        "question": "utilization이 5%p 증가하면 capacity 영향은?",
        "expected_current_status": "data_unavailable",
        "baseline": {"rows": [{"util_percent": 98.0}], "columns": ["util_percent"]},
        "expected_direction": 1,
    },
    {
        "id": "sc003_relative_utilization_percent",
        "question": "가동률이 5% 감소하면 capacity 영향은?",
        "expected_current_status": "succeeded",
        "baseline": {
            "rows": [{"util_percent": 80.0, "lotcomps": 1000.0}],
            "columns": ["util_percent", "lotcomps"],
        },
        "expected_direction": -1,
        "expected_change_count": 1,
        "expected_estimates": {
            "projected_util_percent": 76.0,
            "capacity_delta_percent": -5.0,
        },
    },
    {
        "id": "sc003_compound_supported_changes",
        "question": (
            "가동률이 5%p 감소하고 cycle time이 8% 증가하면 capacity와 납기 영향은?"
        ),
        "expected_current_status": "succeeded",
        "baseline": {
            "rows": [{"util_percent": 80.0, "cycleavg": 10.0, "lotcomps": 1000.0}],
            "columns": ["util_percent", "cycleavg", "lotcomps"],
        },
        "expected_direction": -1,
        "expected_change_count": 2,
        "expected_estimates": {
            "capacity_delta_percent": -6.25,
            "projected_cycle_time": 10.8,
        },
        "expected_limitation_contains": "ontime",
    },
    {
        "id": "sc003_compound_partial_model",
        "question": (
            "가동률이 5%p 감소하고 Queue Time이 10% 증가하면 capacity와 output 영향은?"
        ),
        "expected_current_status": "succeeded",
        "baseline": {
            "rows": [{"util_percent": 80.0, "lotcomps": 1000.0}],
            "columns": ["util_percent", "lotcomps"],
        },
        "expected_direction": -1,
        "expected_change_count": 2,
        "expected_estimates": {"capacity_delta_percent": -6.25},
        "expected_limitation_contains": "Queue Time",
    },
    {
        "id": "sc003_duplicate_metric_changes_rejected",
        "question": "utilization이 5% 증가한 뒤 3% 감소하면 capacity 영향은?",
        "expected_current_status": "data_unavailable",
        "baseline": {
            "rows": [{"util_percent": 80.0}],
            "columns": ["util_percent"],
        },
        "expected_direction": 1,
        "expected_change_count": 2,
        "expected_limitation_contains": "여러 개",
    },
    {
        "id": "sc003_nonpositive_cycle_projection_rejected",
        "question": "cycle time이 100% 감소하면 영향은?",
        "expected_current_status": "data_unavailable",
        "baseline": {
            "rows": [{"cycleavg": 10.0}],
            "columns": ["cycleavg"],
        },
        "expected_direction": -1,
        "expected_limitation_contains": "0 이하",
    },
    {
        "id": "sc003_mixed_product_baseline_rejected",
        "question": "utilization이 5%p 감소하면 capacity 영향은?",
        "expected_current_status": "data_unavailable",
        "baseline": {
            "rows": [
                {"product": "Product_3", "util_percent": 80.0},
                {"product": "Product_4", "util_percent": 60.0},
            ],
            "columns": ["product", "util_percent"],
        },
        "expected_direction": -1,
        "expected_limitation_contains": "서로 다른 대상/기간 차원",
    },
]


def evaluate_case(case: dict[str, Any]) -> dict[str, Any]:
    baseline = case.get("baseline") or BASELINES[case["id"]]
    result = estimate_output_delta(
        baseline={
            "rows": baseline["rows"],
            "columns": baseline["columns"],
            "query_plan": {
                "template_id": f"eval_{case['id']}",
                "source_tables": baseline.get("source_tables", []),
            },
        },
        scenario={"question": case["question"], "fab": "fab10"},
    )
    failures = []
    if result["status"] != case["expected_current_status"]:
        failures.append(
            f"status expected={case['expected_current_status']} actual={result['status']}"
        )
    parsed_change = result["scenario"].get("parsed_change") or {}
    if "expected_direction" in case and parsed_change.get("direction") != case["expected_direction"]:
        failures.append(
            f"direction expected={case['expected_direction']} actual={parsed_change.get('direction')}"
        )
    if "expected_change_count" in case:
        actual_count = len(result["scenario"].get("parsed_changes") or [])
        if actual_count != case["expected_change_count"]:
            failures.append(
                f"change count expected={case['expected_change_count']} actual={actual_count}"
            )
    for name, expected in case.get("expected_estimates", {}).items():
        if result["estimates"].get(name) != expected:
            failures.append(
                f"estimate {name} expected={expected} actual={result['estimates'].get(name)}"
            )
    expected_limitation = case.get("expected_limitation_contains")
    if expected_limitation and not any(
        expected_limitation.casefold() in limitation.casefold()
        for limitation in result["limitations"]
    ):
        failures.append(f"limitation missing fragment: {expected_limitation}")
    if result["status"] == "succeeded":
        for field in ("inputs", "estimates", "formulae", "provenance"):
            if not result.get(field):
                failures.append(f"missing {field}")
        if not result["assumptions"] and not result["limitations"]:
            failures.append("missing calculation boundary")
    else:
        if result["estimates"]:
            failures.append("unavailable case produced estimates")
        if not result["limitations"]:
            failures.append("unavailable case omitted limitations")
    return {
        "id": case["id"],
        "status": result["status"],
        "passed": not failures,
        "failures": failures,
        "estimate_keys": sorted(result["estimates"]),
    }


def run_evaluation() -> dict[str, Any]:
    scenarios = json.loads(FIXTURE.read_text(encoding="utf-8"))
    scenario = next(item for item in scenarios if item["scenario_id"] == "SC-003")
    results = [evaluate_case(case) for case in [*scenario["questions"], *VARIANT_CASES]]
    return {
        "summary": {
            "cases": len(results),
            "passed": sum(item["passed"] for item in results),
            "rate": round(sum(item["passed"] for item in results) / len(results), 4),
        },
        "results": results,
    }


def main() -> int:
    report = run_evaluation()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["summary"]["passed"] == report["summary"]["cases"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
