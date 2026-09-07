from scripts.evaluate_impact_deterministic import evaluate_case, run_evaluation


def test_sc003_deterministic_gate_passes_all_cases() -> None:
    report = run_evaluation()

    assert report["summary"] == {"cases": 17, "passed": 17, "rate": 1.0}


def test_impact_evaluator_reports_status_mismatch() -> None:
    result = evaluate_case(
        {
            "id": "sc003_queue_time_output_impact",
            "question": "fab10에서 Queue Time이 10% 늘면 output 영향은?",
            "expected_current_status": "succeeded",
        }
    )

    assert result["passed"] is False
    assert result["failures"] == ["status expected=succeeded actual=data_unavailable"]
