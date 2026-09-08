from app.sub_agent.visualization import build_chart_spec


def test_line_chart_uses_query_plan_encoding() -> None:
    rows = [{"release_date": "2018-01-01", "lot_count": 3}]

    chart = build_chart_spec(
        "Route releases",
        rows,
        intent={
            "type": "line",
            "x": "release_date",
            "y": "lot_count",
            "x_title": "Release date",
            "y_title": "Lot release count",
        },
    )

    assert chart["type"] == "line"
    assert chart["encoding"]["x"]["field"] == "release_date"
    assert chart["encoding"]["y"]["field"] == "lot_count"
    assert chart["rows"] == rows


def test_multi_metric_comparison_normalizes_wide_rows_for_grouped_series() -> None:
    rows = [
        {"part": "part_3", "cycleavg": 10.5, "ontime_percent": 89.3},
        {"part": "part_4", "cycleavg": 9.8, "ontime_percent": 90.1},
    ]

    chart = build_chart_spec(
        "Product comparison",
        rows,
        intent={
            "type": "grouped_bar",
            "x": "part",
            "y": ["cycleavg", "ontime_percent"],
            "x_title": "Product",
            "y_title": "Metric value",
        },
    )

    assert chart["type"] == "grouped_bar"
    assert chart["encoding"]["y"]["field"] == "value"
    assert chart["encoding"]["color"]["field"] == "metric"
    assert chart["series"] == {
        "field": "metric",
        "values": ["cycleavg", "ontime_percent"],
    }
    assert chart["rows"] == [
        {"part": "part_3", "metric": "cycleavg", "value": 10.5},
        {"part": "part_3", "metric": "ontime_percent", "value": 89.3},
        {"part": "part_4", "metric": "cycleavg", "value": 9.8},
        {"part": "part_4", "metric": "ontime_percent", "value": 90.1},
    ]


def test_chart_normalizes_numeric_strings_across_all_rows() -> None:
    chart = build_chart_spec(
        "WIP trend",
        [
            {"report_date": "2020-01-01", "wiplotavg": "1,200"},
            {"report_date": "2020-01-02", "wiplotavg": "1200.5"},
        ],
        intent={"type": "line", "x": "report_date", "y": "wiplotavg"},
    )

    assert chart["rows"] == [
        {"report_date": "2020-01-01", "wiplotavg": 1200},
        {"report_date": "2020-01-02", "wiplotavg": 1200.5},
    ]


def test_chart_rejects_missing_or_non_finite_values_with_value_error() -> None:
    for rows in (
        [{"part": "part_3", "cycleavg": 10}, {"part": "part_4"}],
        [{"part": "part_3", "cycleavg": "NaN"}],
    ):
        try:
            build_chart_spec(
                "comparison",
                rows,
                intent={"type": "bar", "x": "part", "y": "cycleavg"},
            )
        except ValueError as exc:
            assert "Chart row" in str(exc)
        else:
            raise AssertionError("invalid chart rows must be rejected")


def test_temporal_multi_metric_chart_preserves_independent_line_series() -> None:
    chart = build_chart_spec(
        "WIP and ontime trend",
        [
            {"report_date": "2020-01-01", "wiplotavg": 10, "ontime_percent": 90},
            {"report_date": "2020-01-02", "wiplotavg": 12, "ontime_percent": 88},
        ],
        intent={
            "type": "line",
            "x": "report_date",
            "y": ["wiplotavg", "ontime_percent"],
        },
    )

    assert chart["type"] == "line"
    assert chart["encoding"]["color"]["field"] == "metric"
    assert chart["series"]["values"] == ["wiplotavg", "ontime_percent"]
    assert len(chart["rows"]) == 4


def test_multi_equipment_multi_metric_line_chart_builds_combined_series() -> None:
    chart = build_chart_spec(
        "Equipment utilization and down trend",
        [
            {"report_date": "2020-01-01", "stn": "DE_BE_11", "util_percent": 80, "down_percent": 5},
            {"report_date": "2020-01-02", "stn": "DE_BE_11", "util_percent": 82, "down_percent": 4},
            {"report_date": "2020-01-01", "stn": "DE_BE_12", "util_percent": 75, "down_percent": 8},
            {"report_date": "2020-01-02", "stn": "DE_BE_12", "util_percent": 73, "down_percent": 9},
        ],
        intent={
            "type": "line",
            "x": "report_date",
            "y": ["util_percent", "down_percent"],
            "series": "stn",
        },
    )

    assert chart["encoding"]["color"]["field"] == "series_key"
    assert chart["series"]["values"] == [
        "DE_BE_11 / util_percent",
        "DE_BE_11 / down_percent",
        "DE_BE_12 / util_percent",
        "DE_BE_12 / down_percent",
    ]
    assert len(chart["rows"]) == 8
    assert {item["series"] for item in chart["trend_summary"]} == set(
        chart["series"]["values"]
    )


def test_single_metric_chart_uses_result_column_as_series() -> None:
    chart = build_chart_spec(
        "Process WIP trend",
        [
            {"report_date": "2020-01-01", "stngrp": "Dry_Etch", "wiplotavg": 10},
            {"report_date": "2020-01-01", "stngrp": "Photo", "wiplotavg": 8},
        ],
        intent={
            "type": "line",
            "x": "report_date",
            "y": "wiplotavg",
            "series": "stngrp",
        },
    )

    assert chart["encoding"]["color"] == {"field": "stngrp", "title": "stngrp"}
    assert chart["series"] == {
        "field": "stngrp",
        "values": ["Dry_Etch", "Photo"],
    }


def test_chart_rejects_unknown_type_instead_of_rendering_it_as_line() -> None:
    try:
        build_chart_spec(
            "invalid",
            [{"report_date": "2020-01-01", "wiplotavg": 10}],
            intent={"type": "pie", "x": "report_date", "y": "wiplotavg"},
        )
    except ValueError as exc:
        assert "Unsupported chart type" in str(exc)
    else:
        raise AssertionError("unsupported chart types must be rejected")


def test_line_chart_orders_iso_temporal_rows_ascending() -> None:
    chart = build_chart_spec(
        "WIP trend",
        [
            {"report_date": "2020-01-03", "wiplotavg": 13},
            {"report_date": "2020-01-01", "wiplotavg": 10},
            {"report_date": "2020-01-02", "wiplotavg": 12},
        ],
        intent={"type": "line", "x": "report_date", "y": "wiplotavg"},
    )

    assert [row["report_date"] for row in chart["rows"]] == [
        "2020-01-01",
        "2020-01-02",
        "2020-01-03",
    ]
    assert chart["trend_summary"] == [
        {
            "series": "wiplotavg",
            "start_x": "2020-01-01",
            "end_x": "2020-01-03",
            "start_value": 10,
            "end_value": 13,
            "absolute_delta": 3,
            "percent_delta": 30,
        }
    ]


def test_multi_metric_line_chart_summarizes_each_metric() -> None:
    chart = build_chart_spec(
        "WIP and ontime trend",
        [
            {"report_date": "2020-01-01", "wiplotavg": 10, "ontime_percent": 90},
            {"report_date": "2020-01-02", "wiplotavg": 12, "ontime_percent": 88},
        ],
        intent={
            "type": "line",
            "x": "report_date",
            "y": ["wiplotavg", "ontime_percent"],
        },
    )

    assert chart["trend_summary"] == [
        {
            "series": "wiplotavg",
            "start_x": "2020-01-01",
            "end_x": "2020-01-02",
            "start_value": 10,
            "end_value": 12,
            "absolute_delta": 2,
            "percent_delta": 20,
        },
        {
            "series": "ontime_percent",
            "start_x": "2020-01-01",
            "end_x": "2020-01-02",
            "start_value": 90,
            "end_value": 88,
            "absolute_delta": -2,
            "percent_delta": -2.2222,
        },
    ]


def test_line_chart_rejects_duplicate_x_and_series_key() -> None:
    try:
        build_chart_spec(
            "duplicate trend",
            [
                {"report_date": "2020-01-01", "stngrp": "Dry_Etch", "wip": 10},
                {"report_date": "2020-01-01", "stngrp": "Dry_Etch", "wip": 12},
            ],
            intent={"type": "line", "x": "report_date", "y": "wip", "series": "stngrp"},
        )
    except ValueError as exc:
        assert "duplicates line key" in str(exc)
    else:
        raise AssertionError("duplicate line keys must be rejected")


def test_line_chart_rejects_equivalent_temporal_keys() -> None:
    try:
        build_chart_spec(
            "duplicate timestamp trend",
            [
                {"report_date": "2020-01-01", "wip": 10},
                {"report_date": "2020-01-01T00:00:00", "wip": 12},
            ],
            intent={"type": "line", "x": "report_date", "y": "wip"},
        )
    except ValueError as exc:
        assert "duplicates line key" in str(exc)
    else:
        raise AssertionError("equivalent temporal line keys must be rejected")


def test_line_chart_rejects_categorical_x_axis() -> None:
    try:
        build_chart_spec(
            "invalid categorical trend",
            [{"period": "first", "wip": 10}, {"period": "second", "wip": 12}],
            intent={"type": "line", "x": "period", "y": "wip"},
        )
    except ValueError as exc:
        assert "consistently temporal or numeric" in str(exc)
    else:
        raise AssertionError("categorical line x values must be rejected")


def test_line_chart_orders_numeric_x_axis() -> None:
    chart = build_chart_spec(
        "numeric trend",
        [{"step": "10", "wip": 12}, {"step": "2", "wip": 10}],
        intent={"type": "line", "x": "step", "y": "wip"},
    )

    assert [row["step"] for row in chart["rows"]] == ["2", "10"]


def test_chart_rejects_unknown_or_conflicting_series_field() -> None:
    rows = [{"report_date": "2020-01-01", "wip": 10}]
    for series in ("missing", "report_date", "wip"):
        try:
            build_chart_spec(
                "invalid series",
                rows,
                intent={"type": "line", "x": "report_date", "y": "wip", "series": series},
            )
        except ValueError as exc:
            assert "series field" in str(exc)
        else:
            raise AssertionError("invalid series fields must be rejected")


def test_ranked_bar_preserves_query_result_category_order() -> None:
    rows = [
        {"stngrp": "Dry_Etch", "util_percent": 94},
        {"stngrp": "Photo", "util_percent": 88},
        {"stngrp": "Implant", "util_percent": 81},
    ]

    chart = build_chart_spec(
        "Top utilization",
        rows,
        intent={"type": "bar", "x": "stngrp", "y": "util_percent"},
    )

    assert chart["rows"] == rows
    assert chart["encoding"]["x"]["sort"] == ["Dry_Etch", "Photo", "Implant"]


def test_bar_rejects_duplicate_category_keys() -> None:
    try:
        build_chart_spec(
            "ambiguous ranking",
            [
                {"stngrp": "Dry_Etch", "util_percent": 94},
                {"stngrp": "Dry_Etch", "util_percent": 88},
            ],
            intent={"type": "bar", "x": "stngrp", "y": "util_percent"},
        )
    except ValueError as exc:
        assert "duplicates categorical key" in str(exc)
    else:
        raise AssertionError("duplicate categorical keys must be rejected")


def test_chart_domain_includes_negative_values_and_zero_baseline() -> None:
    line = build_chart_spec(
        "Capacity delta",
        [
            {"step": 1, "capacity_delta_percent": -6.25},
            {"step": 2, "capacity_delta_percent": 3.5},
        ],
        intent={"type": "line", "x": "step", "y": "capacity_delta_percent"},
    )
    grouped = build_chart_spec(
        "Metric deltas",
        [{"scenario": "planned", "capacity": -6.25, "throughput": 3.5}],
        intent={
            "type": "grouped_bar",
            "x": "scenario",
            "y": ["capacity", "throughput"],
        },
    )

    assert line["encoding"]["y"]["domain"] == [-6.25, 3.5]
    assert grouped["encoding"]["y"]["domain"] == [-6.25, 3.5]


def test_sparse_line_series_reports_missing_axis_values() -> None:
    chart = build_chart_spec(
        "Sparse equipment trend",
        [
            {"report_date": "2020-01-01", "stn": "DE_BE_11", "wip": 10},
            {"report_date": "2020-01-01", "stn": "DE_BE_12", "wip": 8},
            {"report_date": "2020-01-02", "stn": "DE_BE_12", "wip": 9},
            {"report_date": "2020-01-03", "stn": "DE_BE_11", "wip": 12},
            {"report_date": "2020-01-03", "stn": "DE_BE_12", "wip": 11},
        ],
        intent={
            "type": "line",
            "x": "report_date",
            "y": "wip",
            "series": "stn",
        },
    )

    assert chart["series_gaps"] == [
        {"series": "DE_BE_11", "missing_x": ["2020-01-02"]}
    ]


def test_single_point_line_is_not_summarized_as_zero_change() -> None:
    chart = build_chart_spec(
        "Single observation",
        [{"report_date": "2020-01-01", "wip": 10}],
        intent={"type": "line", "x": "report_date", "y": "wip"},
    )

    assert chart["trend_summary"] == []
    assert chart["series_gaps"] == []
    assert chart["series_coverage"] == [
        {
            "series": "wip",
            "point_count": 1,
            "axis_point_count": 1,
            "coverage_rate": 1.0,
            "assessment": "insufficient",
            "reason": "fewer_than_two_points",
        }
    ]


def test_low_coverage_series_is_excluded_from_trend_summary() -> None:
    chart = build_chart_spec(
        "Low coverage trend",
        [
            {"step": 1, "stn": "A", "wip": 10},
            {"step": 1, "stn": "B", "wip": 8},
            {"step": 2, "stn": "B", "wip": 9},
            {"step": 3, "stn": "B", "wip": 10},
            {"step": 4, "stn": "B", "wip": 11},
            {"step": 5, "stn": "A", "wip": 14},
            {"step": 5, "stn": "B", "wip": 12},
        ],
        intent={"type": "line", "x": "step", "y": "wip", "series": "stn"},
    )

    assert [item["series"] for item in chart["trend_summary"]] == ["B"]
    assert chart["series_coverage"][0] == {
        "series": "A",
        "point_count": 2,
        "axis_point_count": 5,
        "coverage_rate": 0.4,
        "assessment": "insufficient",
        "reason": "coverage_below_0.5",
    }


def test_single_series_calendar_contract_detects_missing_day() -> None:
    chart = build_chart_spec(
        "Daily WIP",
        [
            {"report_date": "2020-01-01", "wip": 10},
            {"report_date": "2020-01-03", "wip": 12},
        ],
        intent={
            "type": "line",
            "x": "report_date",
            "y": "wip",
            "grain": "day",
            "range_start": "2020-01-01",
            "range_end_exclusive": "2020-01-04",
            "missing_policy": "gap",
        },
    )

    assert chart["series_gaps"] == [
        {"series": "wip", "missing_x": ["2020-01-02"]}
    ]
    assert chart["series_coverage"][0]["coverage_rate"] == 0.6667
    assert chart["imputed_points"] == []


def test_count_calendar_contract_fills_missing_day_with_grounded_zero() -> None:
    chart = build_chart_spec(
        "Daily releases",
        [
            {"release_date": "2020-01-01", "lot_count": 2},
            {"release_date": "2020-01-03", "lot_count": 1},
        ],
        intent={
            "type": "line",
            "x": "release_date",
            "y": "lot_count",
            "grain": "day",
            "range_start": "2020-01-01",
            "range_end_exclusive": "2020-01-04",
            "missing_policy": "zero",
        },
    )

    assert chart["rows"] == [
        {"release_date": "2020-01-01", "lot_count": 2},
        {"release_date": "2020-01-02", "lot_count": 0},
        {"release_date": "2020-01-03", "lot_count": 1},
    ]
    assert chart["source_rows"] == [
        {"release_date": "2020-01-01", "lot_count": 2},
        {"release_date": "2020-01-03", "lot_count": 1},
    ]
    assert chart["imputed_points"] == [
        {"x": "2020-01-02", "fields": ["lot_count"], "value": 0}
    ]
    assert chart["series_gaps"] == []
    assert chart["series_coverage"][0]["assessment"] == "complete"


def test_calendar_contract_rejects_unbounded_axis_expansion() -> None:
    try:
        build_chart_spec(
            "Oversized daily trend",
            [{"report_date": "2020-01-01", "wip": 10}],
            intent={
                "type": "line",
                "x": "report_date",
                "y": "wip",
                "grain": "day",
                "range_start": "2020-01-01",
                "range_end_exclusive": "2025-01-01",
                "missing_policy": "gap",
            },
        )
    except ValueError as exc:
        assert "exceeds 1000 axis points" in str(exc)
    else:
        raise AssertionError("oversized chart axes must be rejected")
