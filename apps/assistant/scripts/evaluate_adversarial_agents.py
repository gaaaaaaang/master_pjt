"""Evaluate deterministic adversarial variants across FAB assistant agents."""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT / "src"))

from app.agents.planner import create_plan
from app.config import get_settings
from app.sub_agent.case_search import find_similar_cases
from app.sub_agent.impact import estimate_output_delta
from app.sub_agent.rag import INCIDENT_PLAYBOOK, retrieve_knowledge
from app.sub_agent.text2sql import answer_question, plan_text2sql
from app.sub_agent.visualization import build_chart_spec

DEFAULT_FIXTURE = APP_ROOT / "tests" / "fixtures" / "adversarial_agent_eval.json"


class OfflinePlannerClient:
    def complete_json(self, **_: Any) -> dict[str, Any]:
        raise RuntimeError("offline adversarial evaluation")


def evaluate_case(
    case: dict[str, Any], *, execute_text2sql: bool = False
) -> dict[str, Any]:
    agent = case["agent"]
    failures: list[str] = []
    details: dict[str, Any] = {}
    if agent == "planner":
        result = create_plan(case["question"], llm_client=OfflinePlannerClient())
        details = {"query_type": result.query_type, "agents": result.selected_sub_agents}
        if result.query_type != case["expected_query_type"]:
            failures.append(
                f"query_type expected={case['expected_query_type']} actual={result.query_type}"
            )
        if result.selected_sub_agents != case["expected_agents"]:
            failures.append(
                f"agents expected={case['expected_agents']} actual={result.selected_sub_agents}"
            )
    elif agent == "text2sql":
        result = (
            answer_question(
                case["question"],
                query_type=case["query_type"],
                execute=True,
                deterministic_only=True,
            )
            if execute_text2sql
            else plan_text2sql(
                case["question"],
                query_type=case["query_type"],
                deterministic_only=True,
            )
        )
        plan = result.plan
        details = {"status": result.status, "template": plan.template_id if plan else None}
        if result.status != case["expected_status"]:
            failures.append(f"status expected={case['expected_status']} actual={result.status}")
        if "expected_template" in case and (
            not plan or plan.template_id != case["expected_template"]
        ):
            failures.append(
                f"template expected={case['expected_template']} actual={plan.template_id if plan else None}"
            )
        for name, expected in case.get("expected_slots", {}).items():
            actual = plan.slots.get(name).value if plan and name in plan.slots else None
            if actual != expected:
                failures.append(f"slot {name} expected={expected} actual={actual}")
        for fragment in case.get("expected_sql_contains", []):
            if fragment.casefold() not in (result.sql or "").casefold():
                failures.append(f"SQL missing fragment: {fragment}")
    elif agent == "impact":
        result = estimate_output_delta(
            {"rows": case["baseline_rows"]}, {"question": case["question"]}
        )
        parsed_change = result["scenario"]["parsed_change"] or {}
        details = {
            "status": result["status"],
            "parsed_change": parsed_change,
            "mixed_dimensions": result["baseline"].get("mixed_dimensions", []),
        }
        if result["status"] != case["expected_status"]:
            failures.append(f"status expected={case['expected_status']} actual={result['status']}")
        if parsed_change.get("unit") != case["expected_change_unit"]:
            failures.append(
                f"unit expected={case['expected_change_unit']} actual={parsed_change.get('unit')}"
            )
        expected_estimates = case.get("expected_estimates")
        if expected_estimates is None:
            expected_estimates = (
                [case["expected_estimate"]] if "expected_estimate" in case else []
            )
        for estimate in expected_estimates:
            if estimate not in result["estimates"]:
                failures.append(f"estimate missing: {estimate}")
        if (expected := case.get("expected_change_count")) is not None:
            actual = len(result["scenario"].get("parsed_changes") or [])
            if actual != expected:
                failures.append(f"change count expected={expected} actual={actual}")
        if (expected := case.get("expected_mixed_dimensions")) is not None:
            actual = result["baseline"].get("mixed_dimensions", [])
            if actual != expected:
                failures.append(
                    f"mixed dimensions expected={expected} actual={actual}"
                )
        if (expected := case.get("expected_limitation_contains")) and not any(
            expected.casefold() in item.casefold()
            for item in result["limitations"]
        ):
            failures.append(f"limitation missing fragment: {expected}")
    elif agent == "rag":
        items = retrieve_knowledge(
            case["question"],
            top_k=3,
            knowledge_base=INCIDENT_PLAYBOOK,
            store_path=Path(get_settings().rag_local_store_path),
        )
        actual = items[0].metadata.get("chunk_id") if items else None
        details = {"top_id": actual}
        if actual != case["expected_top_id"]:
            failures.append(f"top_id expected={case['expected_top_id']} actual={actual}")
    elif agent == "case_search":
        items = find_similar_cases(
            case["question"],
            top_k=3,
            store_path=Path(get_settings().incident_case_store_path),
        )
        actual = items[0].metadata.get("case_id") if items else None
        details = {"top_id": actual}
        if actual != case["expected_top_id"]:
            failures.append(f"top_id expected={case['expected_top_id']} actual={actual}")
    elif agent == "visualization":
        try:
            chart = build_chart_spec(case["question"], case["rows"], intent=case["intent"])
        except ValueError as exc:
            details = {"status": "rejected", "reason": str(exc)}
            if case["expected_status"] != "rejected":
                failures.append(f"unexpected rejection: {exc}")
        else:
            details = {"status": "succeeded", "chart_type": chart["type"]}
            if case["expected_status"] != "succeeded":
                failures.append("invalid chart input was accepted")
            if chart["type"] != case.get("expected_chart_type"):
                failures.append(
                    f"chart type expected={case.get('expected_chart_type')} actual={chart['type']}"
                )
            expected_values = case.get("expected_values")
            if expected_values is not None:
                actual_values = [row[case["intent"]["y"]] for row in chart["rows"]]
                if actual_values != expected_values:
                    failures.append(f"values expected={expected_values} actual={actual_values}")
            if (expected := case.get("expected_row_count")) is not None and len(
                chart["rows"]
            ) != expected:
                failures.append(
                    f"row_count expected={expected} actual={len(chart['rows'])}"
                )
            if expected := case.get("expected_color_field"):
                actual = chart.get("encoding", {}).get("color", {}).get("field")
                if actual != expected:
                    failures.append(f"color field expected={expected} actual={actual}")
            if expected := case.get("expected_series_values"):
                actual = chart.get("series", {}).get("values")
                if actual != expected:
                    failures.append(f"series values expected={expected} actual={actual}")
            if expected := case.get("expected_x_values"):
                x_field = chart.get("encoding", {}).get("x", {}).get("field")
                actual = [row.get(x_field) for row in chart["rows"]]
                if actual != expected:
                    failures.append(f"x values expected={expected} actual={actual}")
            if (expected := case.get("expected_x_sort")) is not None:
                actual = chart.get("encoding", {}).get("x", {}).get("sort")
                if actual != expected:
                    failures.append(f"x sort expected={expected} actual={actual}")
            if (expected := case.get("expected_y_domain")) is not None:
                actual = chart.get("encoding", {}).get("y", {}).get("domain")
                if actual != expected:
                    failures.append(f"y domain expected={expected} actual={actual}")
            if (expected := case.get("expected_series_gaps")) is not None:
                actual = chart.get("series_gaps")
                if actual != expected:
                    failures.append(
                        f"series gaps expected={expected} actual={actual}"
                    )
            if (expected := case.get("expected_series_coverage")) is not None:
                actual = chart.get("series_coverage")
                if actual != expected:
                    failures.append(
                        f"series coverage expected={expected} actual={actual}"
                    )
            if (expected := case.get("expected_imputed_points")) is not None:
                actual = chart.get("imputed_points")
                if actual != expected:
                    failures.append(
                        f"imputed points expected={expected} actual={actual}"
                    )
            if (expected := case.get("expected_trend_summary")) is not None:
                actual = chart.get("trend_summary")
                if actual != expected:
                    failures.append(
                        f"trend_summary expected={expected} actual={actual}"
                    )
    else:
        failures.append(f"unsupported evaluator agent: {agent}")

    return {
        "id": case["id"],
        "scenario_id": case["scenario_id"],
        "agent": agent,
        "passed": not failures,
        "failures": failures,
        "details": details,
    }


def evaluate_cases(
    cases: list[dict[str, Any]], *, execute_text2sql: bool = False
) -> dict[str, Any]:
    results = [
        evaluate_case(case, execute_text2sql=execute_text2sql) for case in cases
    ]
    by_agent: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for result in results:
        by_agent[result["agent"]].append(result)
    agent_metrics = {
        agent: {
            "cases": len(items),
            "passed": sum(item["passed"] for item in items),
            "pass_rate": round(sum(item["passed"] for item in items) / len(items), 4),
        }
        for agent, items in sorted(by_agent.items())
    }
    total = len(results)
    passed = sum(item["passed"] for item in results)
    return {
        "summary": {
            "case_count": total,
            "passed": passed,
            "pass_rate": round(passed / total, 4) if total else 0.0,
            "agents": agent_metrics,
            "failed_case_ids": [item["id"] for item in results if not item["passed"]],
            "text2sql_mode": "local_execution" if execute_text2sql else "generation_only",
        },
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--execute-text2sql", action="store_true")
    args = parser.parse_args()
    report = evaluate_cases(
        json.loads(args.fixture.read_text(encoding="utf-8")),
        execute_text2sql=args.execute_text2sql,
    )
    print(json.dumps(report if args.as_json else report["summary"], ensure_ascii=False, indent=2))
    return 0 if report["summary"]["pass_rate"] == 1.0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
