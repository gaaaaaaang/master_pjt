"""Visualization sub-agent placeholder."""

from typing import Any


def build_chart_spec(title: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Return a frontend-friendly chart spec from tabular FAB query results."""
    return {
        "title": title,
        "type": "bar",
        "rows": rows,
    }

