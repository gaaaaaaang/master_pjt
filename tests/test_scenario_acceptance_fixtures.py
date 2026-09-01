import json
from pathlib import Path

FIXTURE = Path("tests/fixtures/scenario_acceptance_questions.json")
TEXT2SQL_FIXTURE = Path("tests/fixtures/text2sql_fab10_eval.json")


def test_scenario_acceptance_fixture_covers_sc001_to_sc004() -> None:
    scenarios = json.loads(FIXTURE.read_text())

    assert [scenario["scenario_id"] for scenario in scenarios] == [
        "SC-001",
        "SC-002",
        "SC-003",
        "SC-004",
    ]
    assert sum(len(scenario["questions"]) for scenario in scenarios) >= 18


def test_scenario_acceptance_questions_define_routing_expectations() -> None:
    scenarios = json.loads(FIXTURE.read_text())

    for scenario in scenarios:
        assert scenario["acceptance_focus"]
        for question in scenario["questions"]:
            assert question["id"].startswith(scenario["scenario_id"].lower().replace("-", ""))
            assert question["question"]
            assert question["expected_query_type"]
            assert "expected_agents" in question
            assert question["expected_current_status"] in {
                "succeeded",
                "needs_clarification",
                "data_unavailable",
                "unsupported",
                "failed",
            }


def test_text2sql_eval_fixture_has_unique_cases_and_expected_contract() -> None:
    cases = json.loads(TEXT2SQL_FIXTURE.read_text())
    ids = [case["id"] for case in cases]

    assert len(cases) >= 47
    assert len(ids) == len(set(ids))
    for case in cases:
        assert case["question"]
        assert case["expected_status"] in {
            "succeeded",
            "needs_clarification",
            "data_unavailable",
            "unsupported",
            "failed",
        }
        assert case["expected_query_type"] in {
            "status",
            "master_data_lookup",
            "release_plan_lookup",
            "trend",
            "unsupported",
        }
        assert isinstance(case["expect_sql"], bool)
