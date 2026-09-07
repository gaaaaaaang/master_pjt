import json

from scripts.evaluate_context_memory import DEFAULT_FIXTURE, evaluate_cases


def test_context_memory_fixture_enforces_normalization_and_precedence() -> None:
    report = evaluate_cases(json.loads(DEFAULT_FIXTURE.read_text(encoding="utf-8")))

    assert report["summary"] == {
        "case_count": 10,
        "context_accuracy_rate": 1.0,
        "normalization_accuracy_rate": 1.0,
        "switch_accuracy_rate": 1.0,
        "override_accuracy_rate": 1.0,
        "inheritance_accuracy_rate": 1.0,
        "selection_inheritance_accuracy_rate": 1.0,
        "failed_case_ids": [],
    }
