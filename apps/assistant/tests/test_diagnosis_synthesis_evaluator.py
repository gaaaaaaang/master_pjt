import json

from scripts.evaluate_diagnosis_synthesis import DEFAULT_FIXTURE, evaluate_cases


def test_diagnosis_synthesis_fixture_enforces_calibration_and_taxonomy() -> None:
    report = evaluate_cases(json.loads(DEFAULT_FIXTURE.read_text(encoding="utf-8")))

    assert report["summary"] == {
        "case_count": 14,
        "passed": 14,
        "contract_pass_rate": 1.0,
        "candidate_only_rate": 1.0,
        "taxonomy_precision_rate": 1.0,
        "provenance_calibration_rate": 1.0,
        "evidence_eligibility_rate": 1.0,
        "candidate_ranking_rate": 1.0,
        "conflict_calibration_rate": 1.0,
        "support_level_rate": 1.0,
        "failed_case_ids": [],
    }
