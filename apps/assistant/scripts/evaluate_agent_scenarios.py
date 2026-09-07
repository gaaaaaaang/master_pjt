"""Evaluate SC-001..SC-004 routing, execution, and final-answer quality by agent."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

APP_ROOT = Path(__file__).resolve().parents[1]
APP_SRC = APP_ROOT / "src"
sys.path.insert(0, str(APP_SRC))

from app.agents.supervisor import Supervisor, SupervisorResult
from app.schemas.chat import ChatRequest
from app.sub_agent.reflection import verify_response

DEFAULT_FIXTURE = APP_ROOT / "tests" / "fixtures" / "scenario_acceptance_questions.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--scenario", action="append", help="Only run a scenario ID, repeatable.")
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument(
        "--live",
        action="store_true",
        help="Allow configured Azure LLM calls and database queries.",
    )
    return parser.parse_args()


def evaluate_result(case: dict[str, Any], result: SupervisorResult) -> dict[str, Any]:
    actual_agents = [run.agent for run in result.agent_runs]
    expected_agents = case.get("expected_agents")
    routing_failures = []
    execution_failures = []
    answer_failures = []

    if (expected := case.get("expected_query_type")) and result.query_type != expected:
        routing_failures.append(f"query_type expected={expected} actual={result.query_type}")
    if expected_agents is not None and actual_agents != expected_agents:
        routing_failures.append(f"agents expected={expected_agents} actual={actual_agents}")

    if (expected := case.get("expected_current_status")) and result.status != expected:
        execution_failures.append(f"status expected={expected} actual={result.status}")
    for fragment in case.get("expected_sql_contains", []):
        if fragment.casefold() not in (result.sql or "").casefold():
            execution_failures.append(f"SQL missing fragment: {fragment}")
    if expected_chart := case.get("target_chart_type"):
        actual_chart = (result.chart or {}).get("type")
        if result.status == "succeeded" and actual_chart != expected_chart:
            execution_failures.append(
                f"chart type expected={expected_chart} actual={actual_chart}"
            )

    evidence = [
        item.model_dump() if hasattr(item, "model_dump") else item
        for item in result.evidence
    ]
    evidence_types = {str(item.get("source_type") or "") for item in evidence}
    for expected_type in case.get("target_evidence_types", []):
        if expected_type not in evidence_types:
            execution_failures.append(
                f"evidence type expected={expected_type} actual={sorted(evidence_types)}"
            )

    if result.query_type == "diagnosis":
        synthesis = next(
            (item for item in evidence if item.get("source_type") == "diagnosis_synthesis"),
            None,
        )
        if synthesis is None:
            execution_failures.append("diagnosis synthesis evidence is missing")
        elif synthesis.get("metadata", {}).get("conclusion_level") != "candidate_only":
            execution_failures.append("diagnosis conclusion must remain candidate_only")
        elif not synthesis.get("metadata", {}).get("reliability_assessment"):
            execution_failures.append("diagnosis reliability assessment is missing")
        elif synthesis.get("metadata", {}).get("reliability_assessment", {}).get(
            "can_confirm_root_cause"
        ):
            execution_failures.append("diagnosis synthesis must not confirm root cause")

    if result.query_type == "impact":
        calculation = next(
            (
                item.get("metadata", {})
                for item in evidence
                if item.get("source_type") == "impact_calculation"
            ),
            None,
        )
        if calculation is None:
            execution_failures.append("impact calculation evidence is missing")
        elif calculation.get("status") == "succeeded":
            for field in ("inputs", "formulae", "provenance"):
                if not calculation.get(field):
                    execution_failures.append(f"impact calculation missing {field}")
        elif calculation.get("estimates"):
            execution_failures.append("unavailable impact calculation must not contain estimates")
        if calculation is not None and not calculation.get("limitations") and not calculation.get("assumptions"):
            execution_failures.append("impact calculation must expose assumptions or limitations")

    semantic_haystack = json.dumps(
        {"sql": result.sql, "evidence": evidence},
        ensure_ascii=False,
        default=str,
    ).casefold()
    if result.status == "succeeded":
        for table in case.get("target_source_tables", []):
            if table.casefold() not in semantic_haystack:
                execution_failures.append(f"source table missing: {table}")
        for column in case.get("target_columns", []):
            if column.casefold() not in semantic_haystack:
                execution_failures.append(f"result column missing: {column}")
        for metric in case.get("target_metrics", []):
            if metric.casefold() not in semantic_haystack:
                execution_failures.append(f"metric missing: {metric}")

    query_plan = next(
        (
            item.get("metadata", {}).get("query_plan")
            for item in evidence
            if item.get("source_type") == "text2sql_plan"
            and item.get("metadata", {}).get("query_plan")
        ),
        None,
    )
    for fixture_key, slot_key in (
        ("target_date_basis", "date_basis"),
        ("target_relative_period", "relative_period"),
    ):
        expected_value = case.get(fixture_key)
        if not expected_value or result.status != "succeeded":
            continue
        slot = (query_plan or {}).get("slots", {}).get(slot_key, {})
        actual_value = slot.get("value") if isinstance(slot, dict) else None
        if actual_value != expected_value:
            execution_failures.append(
                f"{slot_key} expected={expected_value} actual={actual_value}"
            )

    answer_lower = result.answer.casefold()
    for fragment in case.get("expected_answer_contains", []):
        if fragment.casefold() not in answer_lower:
            answer_failures.append(f"answer missing fragment: {fragment}")
    if not result.answer_review.get("approved", False):
        answer_failures.extend(result.answer_review.get("issues") or ["answer review not approved"])
    deterministic_answer_check = verify_response(
        result.answer,
        evidence=evidence,
        limitations=result.limitations,
        query_type=result.query_type,
        question=case["question"],
    )
    answer_failures.extend(deterministic_answer_check["warnings"])

    return {
        "id": case["id"],
        "question": case["question"],
        "status": result.status,
        "query_type": result.query_type,
        "agents": actual_agents,
        "routing_ok": not routing_failures,
        "execution_ok": not execution_failures,
        "answer_ok": not answer_failures,
        "passed": not routing_failures and not execution_failures and not answer_failures,
        "routing_failures": routing_failures,
        "execution_failures": execution_failures,
        "answer_failures": list(dict.fromkeys(answer_failures)),
        "answer_review": result.answer_review,
        "answer_quality": {
            "score": deterministic_answer_check["quality_score"],
            "dimensions": deterministic_answer_check["quality_dimensions"],
        },
    }


def summarize(results: list[dict[str, Any]], runs: list[SupervisorResult]) -> dict[str, Any]:
    total = len(results)
    metric_names = ("routing", "execution", "answer")
    metrics = {
        name: {
            "pass": sum(1 for item in results if item[f"{name}_ok"]),
            "total": total,
        }
        for name in metric_names
    }
    for metric in metrics.values():
        metric["rate"] = round(metric["pass"] / total, 4) if total else 0.0

    quality_dimension_names = (
        "question_alignment",
        "evidence_grounding",
        "limitation_visibility",
        "provenance_calibration",
    )
    answer_quality = {
        name: {
            "pass": sum(
                bool(item.get("answer_quality", {}).get("dimensions", {}).get(name))
                for item in results
            ),
            "total": total,
        }
        for name in quality_dimension_names
    }
    for metric in answer_quality.values():
        metric["rate"] = round(metric["pass"] / total, 4) if total else 0.0

    agents: dict[str, dict[str, int | float]] = {}
    for result in runs:
        reflections = {item["agent_name"]: item for item in result.agent_reflections}
        for run in result.agent_runs:
            stats = agents.setdefault(
                run.agent,
                {"runs": 0, "succeeded": 0, "reflection_pass": 0},
            )
            stats["runs"] += 1
            stats["succeeded"] += int(run.status == "succeeded")
            stats["reflection_pass"] += int(
                reflections.get(run.agent, {}).get("decision") == "pass"
            )
    for stats in agents.values():
        runs_count = int(stats["runs"])
        stats["success_rate"] = round(int(stats["succeeded"]) / runs_count, 4)
        stats["reflection_pass_rate"] = round(
            int(stats["reflection_pass"]) / runs_count,
            4,
        )

    return {
        "cases": total,
        "passed": sum(1 for item in results if item["passed"]),
        "metrics": metrics,
        "answer_quality": answer_quality,
        "average_answer_quality_score": (
            round(
                sum(float(item.get("answer_quality", {}).get("score", 0.0)) for item in results)
                / total,
                4,
            )
            if total
            else 0.0
        ),
        "agents": agents,
    }


def main() -> int:
    args = parse_args()
    if not args.live:
        print(
            "Refusing live evaluation without --live. This run can send scenario questions and "
            "schema context to the configured Azure endpoint and execute database queries.",
            file=sys.stderr,
        )
        return 2
    scenarios = json.loads(args.fixture.read_text(encoding="utf-8"))
    selected = set(args.scenario or [])
    supervisor = Supervisor()
    results = []
    runs = []
    for scenario in scenarios:
        if selected and scenario["scenario_id"] not in selected:
            continue
        for case in scenario["questions"]:
            result = supervisor.run(ChatRequest(message=case["question"]))
            runs.append(result)
            results.append(
                {
                    "scenario_id": scenario["scenario_id"],
                    **evaluate_result(case, result),
                }
            )

    report = {"summary": summarize(results, runs), "results": results}
    if args.as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
        for item in results:
            mark = "PASS" if item["passed"] else "FAIL"
            print(f"{mark} {item['scenario_id']} {item['id']}")
    return 0 if report["summary"]["passed"] == report["summary"]["cases"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
