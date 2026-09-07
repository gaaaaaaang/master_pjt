from app.sub_agent.impact import estimate_output_delta


def test_utilization_percentage_point_drop_estimates_capacity_and_lotcomps() -> None:
    result = estimate_output_delta(
        baseline={"rows": [{"util_percent": 80, "lotcomps": 1000}]},
        scenario={"question": "fab10 utilization이 5%p 떨어지면 capacity 영향 계산"},
    )

    assert result["status"] == "succeeded"
    assert result["estimates"]["projected_util_percent"] == 75.0
    assert result["estimates"]["estimated_capacity_change_percent"] == -6.25
    assert result["estimates"]["capacity_delta_percent"] == -6.25
    assert result["estimates"]["estimated_lotcomps_delta"] == -62.5
    assert result["inputs"] == {
        "baseline_util_percent": 80.0,
        "utilization_delta_percentage_point": -5.0,
        "baseline_lotcomps": 1000.0,
    }
    assert len(result["formulae"]) == 3
    assert result["assumptions"]
    assert "입력 기준" in result["summary"]
    assert "계산식" in result["summary"]


def test_cycle_time_change_does_not_invent_ontime_causality() -> None:
    result = estimate_output_delta(
        baseline={"rows": [{"cycleavg": 10, "ontime_percent": 90}]},
        scenario={"question": "cycle time이 8% 증가하면 납기 준수율 영향은?"},
    )

    assert result["status"] == "succeeded"
    assert result["estimates"]["projected_cycle_time"] == 10.8
    assert "ontime" in result["limitations"][0]
    assert "projected_ontime" not in result["estimates"]
    assert result["inputs"]["baseline_cycle_time"] == 10.0
    assert result["formulae"]


def test_duplicate_changes_for_same_metric_are_not_partially_applied() -> None:
    result = estimate_output_delta(
        baseline={"rows": [{"util_percent": 80}]},
        scenario={"question": "utilization이 5% 증가한 뒤 3% 감소하면 capacity 영향은?"},
    )

    assert result["status"] == "data_unavailable"
    assert result["estimates"] == {}
    assert len(result["scenario"]["parsed_changes"]) == 2
    assert any("순차·합성 규칙 없이" in item for item in result["limitations"])


def test_nonpositive_projected_cycle_time_is_rejected() -> None:
    result = estimate_output_delta(
        baseline={"rows": [{"cycleavg": 10}]},
        scenario={"question": "cycle time이 100% 감소하면 영향은?"},
    )

    assert result["status"] == "data_unavailable"
    assert result["estimates"] == {}
    assert any("0 이하" in item for item in result["limitations"])


def test_downtime_requires_hourly_completion_rate() -> None:
    result = estimate_output_delta(
        baseline={"rows": [{"down_percent": 4, "lotcomps": 1000}]},
        scenario={"question": "down이 2시간 늘면 lot completion 영향은?"},
    )

    assert result["status"] == "data_unavailable"
    assert result["estimates"] == {}
    assert result["inputs"] == {}
    assert result["formulae"] == []
    assert "시간당 lot completion rate" in result["limitations"][0]
    assert "operational metric" in result["summary"]


def test_impact_records_baseline_query_provenance_and_ignores_non_finite_values() -> None:
    result = estimate_output_delta(
        baseline={
            "rows": [
                {"util_percent": 80, "lotcomps": 1000},
                {"util_percent": float("nan"), "lotcomps": 1200},
            ],
            "columns": ["util_percent", "lotcomps"],
            "query_plan": {
                "template_id": "status_station_group",
                "source_tables": ["fab10.autosched_stngrp"],
            },
        },
        scenario={"question": "utilization이 5%p 줄면 capacity 영향"},
    )

    assert result["inputs"]["baseline_util_percent"] == 80.0
    assert result["provenance"]["aggregation"] == "arithmetic_mean_by_numeric_column"
    assert result["provenance"]["source_tables"] == ["fab10.autosched_stngrp"]


def test_mixed_target_baseline_is_not_averaged_into_one_impact() -> None:
    result = estimate_output_delta(
        baseline={
            "rows": [
                {"part": "part_3", "util_percent": 80},
                {"part": "part_4", "util_percent": 60},
            ]
        },
        scenario={"question": "utilization이 5%p 감소하면 capacity 영향은?"},
    )

    assert result["status"] == "data_unavailable"
    assert result["estimates"] == {}
    assert result["baseline"]["mixed_dimensions"] == ["part"]
    assert result["provenance"]["mixed_dimensions"] == ["part"]
    assert any("서로 다른 대상/기간 차원" in item for item in result["limitations"])


def test_repeated_rows_for_same_target_can_use_numeric_mean() -> None:
    result = estimate_output_delta(
        baseline={
            "rows": [
                {"part": "part_3", "util_percent": 80},
                {"part": "PART_3", "util_percent": 70},
            ]
        },
        scenario={"question": "utilization이 5%p 감소하면 capacity 영향은?"},
    )

    assert result["status"] == "succeeded"
    assert result["baseline"]["mixed_dimensions"] == []
    assert result["inputs"]["baseline_util_percent"] == 75.0
    assert result["estimates"]["projected_util_percent"] == 70.0


def test_direction_is_bound_to_change_clause_not_outcome_wording() -> None:
    result = estimate_output_delta(
        baseline={"rows": [{"util_percent": 80}]},
        scenario={"question": "utilization이 5%p 증가하면 capacity가 감소하나?"},
    )

    assert result["status"] == "succeeded"
    assert result["scenario"]["parsed_change"]["direction"] == 1
    assert result["estimates"]["projected_util_percent"] == 85.0


def test_outcome_direction_is_not_used_as_ambiguous_input_direction() -> None:
    result = estimate_output_delta(
        baseline={"rows": [{"util_percent": 80}]},
        scenario={"question": "utilization이 5%p 변하면 capacity가 감소하나?"},
    )

    assert result["status"] == "data_unavailable"
    assert result["scenario"]["parsed_change"]["direction"] is None
    assert result["estimates"] == {}
    assert "방향" in result["limitations"][0]


def test_ambiguous_or_conflicting_change_direction_is_rejected() -> None:
    for question in (
        "utilization이 5%p 변하면 capacity 영향은?",
        "utilization이 -5%p 증가하면 capacity 영향은?",
    ):
        result = estimate_output_delta(
            baseline={"rows": [{"util_percent": 80}]},
            scenario={"question": question},
        )

        assert result["status"] == "data_unavailable"
        assert result["estimates"] == {}
        assert "방향" in result["limitations"][0]


def test_out_of_range_impact_scenario_is_not_silently_clamped() -> None:
    result = estimate_output_delta(
        baseline={"rows": [{"util_percent": 98}]},
        scenario={"question": "utilization이 5%p 증가하면 capacity 영향은?"},
    )

    assert result["status"] == "data_unavailable"
    assert result["estimates"] == {}
    assert "0~100%" in result["limitations"][0]


def test_relative_utilization_percent_change_estimates_capacity() -> None:
    result = estimate_output_delta(
        baseline={"rows": [{"util_percent": 80, "lotcomps": 1000}]},
        scenario={"question": "가동률이 5% 감소하면 capacity 영향은?"},
    )

    assert result["status"] == "succeeded"
    assert result["inputs"]["utilization_change_percent"] == -5.0
    assert result["estimates"]["projected_util_percent"] == 76.0
    assert result["estimates"]["capacity_delta_percent"] == -5.0
    assert result["estimates"]["estimated_lotcomps_delta"] == -50.0


def test_compound_impact_calculates_each_supported_metric_change() -> None:
    result = estimate_output_delta(
        baseline={
            "rows": [
                {
                    "util_percent": 80,
                    "cycleavg": 10,
                    "lotcomps": 1000,
                    "ontime_percent": 90,
                }
            ]
        },
        scenario={
            "question": (
                "가동률이 5%p 감소하고 cycle time이 8% 증가하면 "
                "capacity와 납기 영향은?"
            )
        },
    )

    assert result["status"] == "succeeded"
    assert [item["metric"] for item in result["scenario"]["parsed_changes"]] == [
        "utilization",
        "cycle_time",
    ]
    assert result["estimates"]["capacity_delta_percent"] == -6.25
    assert result["estimates"]["projected_cycle_time"] == 10.8
    assert any("ontime" in limitation for limitation in result["limitations"])


def test_compound_impact_exposes_unsupported_partial_calculation() -> None:
    result = estimate_output_delta(
        baseline={"rows": [{"util_percent": 80, "lotcomps": 1000}]},
        scenario={
            "question": (
                "가동률이 5%p 감소하고 Queue Time이 10% 증가하면 "
                "capacity와 output 영향은?"
            )
        },
    )

    assert result["status"] == "succeeded"
    assert result["estimates"]["capacity_delta_percent"] == -6.25
    assert any("Queue Time" in limitation for limitation in result["limitations"])


def test_cycle_duration_string_is_converted_to_hours_with_unit_provenance() -> None:
    result = estimate_output_delta(
        baseline={"rows": [{"cycleavg": "49:30:00"}]},
        scenario={"question": "cycle time이 10% 증가하면 영향은?"},
    )

    assert result["status"] == "succeeded"
    assert result["inputs"]["baseline_cycle_time"] == 49.5
    assert result["estimates"]["projected_cycle_time"] == 54.45
    assert result["baseline"]["metric_units"]["cycleavg"] == "hours"
    assert result["provenance"]["metric_units"]["cycleavg"] == "hours"
