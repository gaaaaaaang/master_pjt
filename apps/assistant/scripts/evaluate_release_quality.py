"""Aggregate local agent metrics and fail on baseline or minimum-threshold regressions."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from psycopg import Error as PsycopgError

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT / "src"))
sys.path.insert(0, str(APP_ROOT / "scripts"))

from app.config import get_settings
from evaluate_adversarial_agents import DEFAULT_FIXTURE as ADVERSARIAL_FIXTURE
from evaluate_adversarial_agents import evaluate_cases as evaluate_adversarial_cases
from evaluate_answer_quality import DEFAULT_FIXTURE as ANSWER_FIXTURE
from evaluate_answer_quality import evaluate_cases as evaluate_answer_cases
from evaluate_case_retrieval import DEFAULT_FIXTURE as CASE_FIXTURE
from evaluate_case_retrieval import evaluate_retrieval as evaluate_case_retrieval
from evaluate_context_memory import DEFAULT_FIXTURE as CONTEXT_FIXTURE
from evaluate_context_memory import evaluate_cases as evaluate_context_cases
from evaluate_diagnosis_synthesis import DEFAULT_FIXTURE as DIAGNOSIS_FIXTURE
from evaluate_diagnosis_synthesis import evaluate_cases as evaluate_diagnosis_cases
from evaluate_impact_deterministic import run_evaluation as evaluate_impact
from evaluate_rag_retrieval import DEFAULT_FIXTURE as RAG_FIXTURE
from evaluate_rag_retrieval import evaluate_retrieval as evaluate_rag_retrieval
from evaluate_retrieval_abstention import DEFAULT_FIXTURE as ABSTENTION_FIXTURE
from evaluate_retrieval_abstention import evaluate_cases as evaluate_abstention_cases
from evaluate_text2sql_deterministic import DEFAULT_FIXTURE as TEXT2SQL_FIXTURE
from evaluate_text2sql_deterministic import evaluate_deterministic_cases

DEFAULT_BASELINE = APP_ROOT / "tests" / "fixtures" / "agent_quality_baseline.json"
DEFAULT_OUTPUT = APP_ROOT / "output" / "evals" / "release_quality.json"


def collect_metrics(*, execute_text2sql: bool = False) -> dict[str, float | int]:
    settings = get_settings()
    text2sql = evaluate_deterministic_cases(
        _read_json(TEXT2SQL_FIXTURE), execute=execute_text2sql
    )["summary"]
    rag = evaluate_rag_retrieval(
        _read_json(RAG_FIXTURE),
        store_path=Path(settings.rag_local_store_path),
        top_k=3,
    )["summary"]
    cases = evaluate_case_retrieval(
        _read_json(CASE_FIXTURE),
        store_path=Path(settings.incident_case_store_path),
        top_k=3,
    )["summary"]
    impact = evaluate_impact()["summary"]
    diagnosis = evaluate_diagnosis_cases(_read_json(DIAGNOSIS_FIXTURE))["summary"]
    context = evaluate_context_cases(_read_json(CONTEXT_FIXTURE))["summary"]
    answer_quality = evaluate_answer_cases(_read_json(ANSWER_FIXTURE))["summary"]
    adversarial = evaluate_adversarial_cases(
        _read_json(ADVERSARIAL_FIXTURE), execute_text2sql=execute_text2sql
    )["summary"]
    abstention = evaluate_abstention_cases(_read_json(ABSTENTION_FIXTURE))["summary"]
    return {
        "text2sql.case_count": text2sql["eligible_cases"],
        "text2sql.semantic_pass_rate": text2sql["semantic_pass_rate"],
        "text2sql.ex_rate": text2sql["ex_rate"],
        "text2sql.em_rate": text2sql["em_rate"],
        "text2sql.intent_rate": text2sql["intent_rate"],
        "text2sql.deterministic_sql_coverage": text2sql["deterministic_sql_coverage"],
        "rag.case_count": rag["case_count"],
        "rag.recall_at_3": rag["recall_at_3"],
        "rag.mrr": rag["mrr"],
        "rag.incident_issue_alignment_rate": rag["incident_issue_alignment_rate"],
        "case_search.case_count": cases["case_count"],
        "case_search.recall_at_3": cases["recall_at_3"],
        "case_search.mrr": cases["mrr"],
        "impact.case_count": impact["cases"],
        "impact.contract_pass_rate": impact["rate"],
        "diagnosis.case_count": diagnosis["case_count"],
        "diagnosis.contract_pass_rate": diagnosis["contract_pass_rate"],
        "diagnosis.candidate_only_rate": diagnosis["candidate_only_rate"],
        "diagnosis.taxonomy_precision_rate": diagnosis["taxonomy_precision_rate"],
        "diagnosis.provenance_calibration_rate": diagnosis[
            "provenance_calibration_rate"
        ],
        "diagnosis.evidence_eligibility_rate": diagnosis[
            "evidence_eligibility_rate"
        ],
        "diagnosis.candidate_ranking_rate": diagnosis["candidate_ranking_rate"],
        "diagnosis.conflict_calibration_rate": diagnosis[
            "conflict_calibration_rate"
        ],
        "diagnosis.support_level_rate": diagnosis["support_level_rate"],
        "context_memory.case_count": context["case_count"],
        "context_memory.context_accuracy_rate": context["context_accuracy_rate"],
        "context_memory.normalization_accuracy_rate": context[
            "normalization_accuracy_rate"
        ],
        "context_memory.switch_accuracy_rate": context["switch_accuracy_rate"],
        "context_memory.override_accuracy_rate": context["override_accuracy_rate"],
        "context_memory.inheritance_accuracy_rate": context[
            "inheritance_accuracy_rate"
        ],
        "context_memory.selection_inheritance_accuracy_rate": context[
            "selection_inheritance_accuracy_rate"
        ],
        "answer_quality.case_count": answer_quality["case_count"],
        "answer_quality.classification_accuracy": answer_quality["classification_accuracy"],
        "answer_quality.positive_accept_rate": answer_quality["positive_accept_rate"],
        "answer_quality.negative_reject_rate": answer_quality["negative_reject_rate"],
        "adversarial.case_count": adversarial["case_count"],
        "adversarial.pass_rate": adversarial["pass_rate"],
        **{
            f"adversarial.{agent if agent != 'planner' else 'planner_outage_propagation'}_pass_rate": metrics["pass_rate"]
            for agent, metrics in adversarial["agents"].items()
        },
        "retrieval_abstention.case_count": abstention["case_count"],
        "retrieval_abstention.rate": abstention["abstention_rate"],
        "retrieval_abstention.rag_rate": abstention["agents"]["rag"]["abstention_rate"],
        "retrieval_abstention.case_search_rate": abstention["agents"]["case_search"][
            "abstention_rate"
        ],
    }


def build_report(
    current: dict[str, float | int],
    baseline_document: dict[str, Any],
    *,
    text2sql_mode: str = "generation_only",
) -> dict[str, Any]:
    comparisons = {}
    failures = []
    baseline_metrics = baseline_document.get("metrics") or {}
    for name, contract in baseline_metrics.items():
        if name not in current:
            failures.append(f"missing current metric: {name}")
            comparisons[name] = {**contract, "current": None, "passed": False}
            continue
        value = current[name]
        baseline = contract["baseline"]
        minimum = contract["minimum"]
        passed = value >= baseline and value >= minimum
        comparisons[name] = {
            "current": value,
            "baseline": baseline,
            "minimum": minimum,
            "delta_from_baseline": round(float(value) - float(baseline), 4),
            "passed": passed,
        }
        if value < baseline:
            failures.append(f"regression: {name} current={value} baseline={baseline}")
        if value < minimum:
            failures.append(f"below minimum: {name} current={value} minimum={minimum}")

    return {
        "scope": baseline_document.get("scope"),
        "baseline_version": baseline_document.get("version"),
        "evaluation_modes": {
            "text2sql": text2sql_mode,
            "rag": "local_lexical_retrieval",
            "case_search": "local_provenance_retrieval",
            "impact": "deterministic_contract",
            "diagnosis": "deterministic_calibration_fixture",
            "context_memory": "deterministic_multiturn_fixture",
            "answer_quality": "deterministic_positive_negative_fixture",
            "adversarial": "deterministic_variant_fixture",
            "adversarial_planner": "model_outage_propagation_not_planning_accuracy",
            "retrieval_abstention": "local_negative_query_fixture",
        },
        "external_calls": False,
        "evaluation_status": "completed",
        "full_graph": baseline_document.get("full_graph"),
        "passed": not failures,
        "failures": failures,
        "metrics": comparisons,
    }


def check_postgres_preflight(
    dsn: str | None,
    *,
    probe: Any | None = None,
) -> dict[str, Any]:
    if not dsn:
        return {
            "status": "unavailable",
            "error_type": "ConfigurationError",
            "message": "POSTGRES_DSN is not configured.",
        }
    try:
        if probe is not None:
            probe(dsn)
        else:
            import psycopg

            with psycopg.connect(
                dsn,
                connect_timeout=3,
                options="-c default_transaction_read_only=on",
            ) as connection, connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
    except (PsycopgError, OSError, RuntimeError) as exc:
        return {
            "status": "unavailable",
            "error_type": type(exc).__name__,
            "message": str(exc).splitlines()[0] or "PostgreSQL preflight failed.",
        }
    return {"status": "available"}


def build_infrastructure_failure_report(
    baseline_document: dict[str, Any],
    postgres_preflight: dict[str, Any],
) -> dict[str, Any]:
    error_type = str(postgres_preflight.get("error_type") or "UnknownError")
    message = str(postgres_preflight.get("message") or "PostgreSQL is unavailable.")
    return {
        "scope": baseline_document.get("scope"),
        "baseline_version": baseline_document.get("version"),
        "evaluation_modes": {"text2sql": "local_execution"},
        "external_calls": False,
        "evaluation_status": "infrastructure_unavailable",
        "infrastructure": {"postgres": postgres_preflight},
        "full_graph": baseline_document.get("full_graph"),
        "passed": False,
        "failures": [
            f"evaluation infrastructure unavailable: postgres {error_type}: {message}"
        ],
        "metrics": {},
    }


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--execute-text2sql", action="store_true")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()

    baseline = _read_json(args.baseline)
    postgres_preflight = {"status": "not_required"}
    if args.execute_text2sql:
        postgres_preflight = check_postgres_preflight(get_settings().postgres_dsn)
    if postgres_preflight["status"] == "unavailable":
        report = build_infrastructure_failure_report(baseline, postgres_preflight)
    else:
        report = build_report(
            collect_metrics(execute_text2sql=args.execute_text2sql),
            baseline,
            text2sql_mode=("local_execution" if args.execute_text2sql else "generation_only"),
        )
        report["infrastructure"] = {"postgres": postgres_preflight}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            report
            if args.as_json
            else {
                "scope": report["scope"],
                "evaluation_status": report["evaluation_status"],
                "infrastructure": report["infrastructure"],
                "passed": report["passed"],
                "failures": report["failures"],
                "full_graph": report["full_graph"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
