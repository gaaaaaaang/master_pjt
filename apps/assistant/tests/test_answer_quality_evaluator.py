import json

from scripts.evaluate_answer_quality import DEFAULT_FIXTURE, evaluate_cases


def test_answer_quality_fixture_accepts_and_rejects_all_sc001_to_sc004_cases() -> None:
    cases = json.loads(DEFAULT_FIXTURE.read_text(encoding="utf-8"))

    report = evaluate_cases(cases)

    assert {item["scenario_id"] for item in report["results"]} == {
        "SC-001",
        "SC-002",
        "SC-003",
        "SC-004",
    }
    assert report["summary"] == {
        "case_count": 70,
        "classification_accuracy": 1.0,
        "positive_accept_rate": 1.0,
        "negative_reject_rate": 1.0,
        "failed_case_ids": [],
    }
