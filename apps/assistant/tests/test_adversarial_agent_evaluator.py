import json

from scripts.evaluate_adversarial_agents import DEFAULT_FIXTURE, evaluate_cases


def test_adversarial_fixture_covers_all_specialist_agents_without_external_calls() -> None:
    report = evaluate_cases(json.loads(DEFAULT_FIXTURE.read_text(encoding="utf-8")))

    assert report["summary"]["case_count"] == 57
    assert report["summary"]["pass_rate"] == 1.0
    assert report["summary"]["failed_case_ids"] == []
    assert report["summary"]["text2sql_mode"] == "generation_only"
    assert set(report["summary"]["agents"]) == {
        "planner",
        "text2sql",
        "rag",
        "case_search",
        "impact",
        "visualization",
    }
    assert all(
        metrics["pass_rate"] == 1.0
        for metrics in report["summary"]["agents"].values()
    )
