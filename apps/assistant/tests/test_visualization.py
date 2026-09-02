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
            "series": "Route_Product_3",
        },
    )

    assert chart["type"] == "line"
    assert chart["encoding"]["x"]["field"] == "release_date"
    assert chart["encoding"]["y"]["field"] == "lot_count"
    assert chart["rows"] == rows
