import json
from pathlib import Path

from scripts.evaluate_text2sql_deterministic import evaluate_deterministic_cases

FIXTURE = Path(__file__).resolve().parent / "fixtures/text2sql_fab10_eval.json"


def test_deterministic_text2sql_gate_covers_all_status_and_trend_cases() -> None:
    cases = json.loads(FIXTURE.read_text(encoding="utf-8"))

    report = evaluate_deterministic_cases(cases)

    assert report["summary"] == {
        "mode": "generation_only",
        "eligible_cases": 79,
        "semantic_pass": 79,
        "semantic_pass_rate": 1.0,
        "ex_pass": 79,
        "ex_rate": 1.0,
        "em_pass": 79,
        "em_rate": 1.0,
        "intent_pass": 79,
        "intent_rate": 1.0,
        "expected_sql_cases": 61,
        "deterministic_sql_cases": 61,
        "deterministic_sql_coverage": 1.0,
        "failed_case_ids": [],
    }
