from typing import Any


def build_chart_spec(
    title: str,
    rows: list[dict[str, Any]],
    *,
    intent: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build a chart contract whose encodings are grounded in the query plan."""
    if not rows:
        raise ValueError("Chart rows must not be empty.")

    columns = list(rows[0])
    chart_intent = intent or {}
    x_field = chart_intent.get("x") or columns[0]
    y_field = chart_intent.get("y") or (columns[1] if len(columns) > 1 else columns[0])
    if x_field not in columns or y_field not in columns:
        raise ValueError("Chart encoding does not match query result columns.")

    return {
        "title": title,
        "type": chart_intent.get("type", "bar"),
        "encoding": {
            "x": {"field": x_field, "title": chart_intent.get("x_title", x_field)},
            "y": {"field": y_field, "title": chart_intent.get("y_title", y_field)},
        },
        "series": chart_intent.get("series"),
        "rows": rows,
    }
