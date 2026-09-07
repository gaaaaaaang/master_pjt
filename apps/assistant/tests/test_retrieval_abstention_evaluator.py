import json

from scripts.evaluate_retrieval_abstention import DEFAULT_FIXTURE, evaluate_cases


def test_unrelated_queries_are_rejected_by_rag_and_case_search() -> None:
    report = evaluate_cases(json.loads(DEFAULT_FIXTURE.read_text(encoding="utf-8")))

    assert report["summary"]["case_count"] == 15
    assert report["summary"]["abstention_rate"] == 1.0
    assert report["summary"]["failed_case_ids"] == []
    assert report["summary"]["agents"] == {
        "case_search": {"case_count": 10, "abstention_rate": 1.0},
        "rag": {"case_count": 5, "abstention_rate": 1.0},
    }
