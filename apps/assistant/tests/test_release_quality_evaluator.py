import json

from scripts.evaluate_release_quality import (
    DEFAULT_BASELINE,
    build_infrastructure_failure_report,
    build_report,
    check_postgres_preflight,
    collect_metrics,
)


def test_current_local_metrics_match_saved_baseline() -> None:
    baseline = json.loads(DEFAULT_BASELINE.read_text(encoding="utf-8"))

    report = build_report(collect_metrics(), baseline)

    assert report["passed"] is True
    assert report["failures"] == []
    assert report["external_calls"] is False
    assert report["full_graph"]["status"] == "not_measured"
    assert report["baseline_version"] == 73
    assert report["evaluation_modes"]["diagnosis"] == "deterministic_calibration_fixture"
    assert report["evaluation_modes"]["context_memory"] == "deterministic_multiturn_fixture"
    assert report["evaluation_modes"]["text2sql"] == "generation_only"
    assert report["evaluation_status"] == "completed"


def test_release_gate_fails_metric_regression_and_minimum() -> None:
    baseline = {
        "scope": "test",
        "metrics": {"rag.mrr": {"baseline": 0.9, "minimum": 0.8}},
    }

    report = build_report({"rag.mrr": 0.7}, baseline)

    assert report["passed"] is False
    assert report["metrics"]["rag.mrr"]["delta_from_baseline"] == -0.2
    assert report["failures"] == [
        "regression: rag.mrr current=0.7 baseline=0.9",
        "below minimum: rag.mrr current=0.7 minimum=0.8",
    ]


def test_release_gate_fails_when_baseline_metric_disappears() -> None:
    baseline = {
        "scope": "test",
        "metrics": {"text2sql.ex_rate": {"baseline": 1.0, "minimum": 0.9}},
    }

    report = build_report({}, baseline)

    assert report["passed"] is False
    assert report["failures"] == ["missing current metric: text2sql.ex_rate"]


def test_postgres_preflight_reports_infrastructure_failure_without_dsn() -> None:
    preflight = check_postgres_preflight(None)
    report = build_infrastructure_failure_report(
        {"scope": "test", "version": 31, "full_graph": {"status": "not_measured"}},
        preflight,
    )

    assert preflight["status"] == "unavailable"
    assert report["evaluation_status"] == "infrastructure_unavailable"
    assert report["metrics"] == {}
    assert report["failures"] == [
        (
            "evaluation infrastructure unavailable: postgres ConfigurationError: "
            "POSTGRES_DSN is not configured."
        )
    ]


def test_postgres_preflight_distinguishes_connection_error_from_quality_regression() -> None:
    def refused(_: str) -> None:
        raise ConnectionRefusedError("local database refused connection")

    preflight = check_postgres_preflight("postgresql://masked", probe=refused)

    assert preflight == {
        "status": "unavailable",
        "error_type": "ConnectionRefusedError",
        "message": "local database refused connection",
    }
