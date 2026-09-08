import math
from datetime import date, datetime, timedelta
from numbers import Real
from typing import Any

SUPPORTED_CHART_TYPES = {"bar", "grouped_bar", "line"}
MIN_TREND_COVERAGE = 0.5
MAX_EXPECTED_AXIS_POINTS = 1000


def build_chart_spec(
    title: str,
    rows: list[dict[str, Any]],
    *,
    intent: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a chart contract whose encodings are grounded in the query plan."""
    if not rows:
        raise ValueError("Chart rows must not be empty.")

    columns = list(rows[0])
    chart_intent = intent or {}
    chart_type = str(chart_intent.get("type") or "bar")
    if chart_type not in SUPPORTED_CHART_TYPES:
        raise ValueError(f"Unsupported chart type: {chart_type}.")
    x_field = chart_intent.get("x") or columns[0]
    y_value = chart_intent.get("y") or (columns[1] if len(columns) > 1 else columns[0])
    y_fields = y_value if isinstance(y_value, list) else [y_value]
    series_field = chart_intent.get("series")
    if (
        not y_fields
        or x_field not in columns
        or any(field not in columns for field in y_fields)
        or x_field in y_fields
    ):
        raise ValueError("Chart encoding does not match query result columns.")
    if series_field is not None and series_field not in columns:
        raise ValueError(f"Chart series field '{series_field}' is not in query result columns.")
    if series_field in {x_field, *y_fields}:
        raise ValueError("Chart series field must differ from x and y encodings.")
    _validate_chart_rows(rows, x_field=x_field, y_fields=y_fields)
    y_domain = _numeric_domain(rows, y_fields)
    ordered_rows = _order_line_rows(rows, x_field) if chart_type == "line" else list(rows)
    source_rows = list(ordered_rows)
    expected_x_values = (
        _expected_temporal_axis(chart_intent) if chart_type == "line" else None
    )
    imputed_points: list[dict[str, Any]] = []
    if chart_type == "line":
        _validate_line_keys(ordered_rows, x_field=x_field, series_field=series_field)
        if chart_intent.get("missing_policy") == "zero" and expected_x_values:
            ordered_rows, imputed_points = _fill_zero_rows(
                ordered_rows,
                expected_x_values=expected_x_values,
                x_field=x_field,
                y_fields=y_fields,
                series_field=series_field,
            )
            ordered_rows = _order_line_rows(ordered_rows, x_field)
    else:
        _validate_categorical_keys(
            ordered_rows,
            x_field=x_field,
            series_field=series_field,
        )
    x_encoding = {
        "field": x_field,
        "title": chart_intent.get("x_title", x_field),
    }
    if chart_type != "line":
        x_encoding["sort"] = list(dict.fromkeys(row[x_field] for row in ordered_rows))

    if len(y_fields) > 1:
        if series_field is not None:
            _validate_series_rows(ordered_rows, series_field)
        if chart_type == "bar":
            chart_type = "grouped_bar"
        if series_field is None:
            normalized_rows = [
                {x_field: row[x_field], "metric": field, "value": _numeric_value(row[field])}
                for row in ordered_rows
                for field in y_fields
            ]
            color_field = "metric"
            series_values = y_fields
        else:
            normalized_rows = [
                {
                    x_field: row[x_field],
                    series_field: row[series_field],
                    "metric": field,
                    "series_key": f"{row[series_field]} / {field}",
                    "value": _numeric_value(row[field]),
                }
                for row in ordered_rows
                for field in y_fields
            ]
            color_field = "series_key"
            series_values = list(
                dict.fromkeys(str(row["series_key"]) for row in normalized_rows)
            )
        chart = {
            "title": title,
            "type": chart_type,
            "encoding": {
                "x": x_encoding,
                "y": {
                    "field": "value",
                    "title": chart_intent.get("y_title", "Value"),
                    "domain": y_domain,
                },
                "color": {
                    "field": color_field,
                    "title": "Metric" if series_field is None else "Series / Metric",
                },
            },
            "series": {"field": color_field, "values": series_values},
            "rows": normalized_rows,
            "source_rows": source_rows,
        }
        if chart_type == "line":
            chart["trend_summary"] = _build_trend_summary(
                normalized_rows,
                x_field=x_field,
                y_field="value",
                metric_field=color_field,
                expected_x_values=expected_x_values,
            )
            chart["series_gaps"] = _build_series_gaps(
                normalized_rows,
                x_field=x_field,
                metric_field=color_field,
                expected_x_values=expected_x_values,
            )
            chart["series_coverage"] = _build_series_coverage(
                normalized_rows,
                x_field=x_field,
                metric_field=color_field,
                expected_x_values=expected_x_values,
            )
            chart["imputed_points"] = imputed_points
        return chart

    y_field = y_fields[0]
    if series_field in columns:
        _validate_series_rows(ordered_rows, series_field)
    normalized_rows = [
        {**row, y_field: _numeric_value(row[y_field])}
        for row in ordered_rows
    ]

    chart = {
        "title": title,
        "type": chart_type,
        "encoding": {
            "x": x_encoding,
            "y": {
                "field": y_field,
                "title": chart_intent.get("y_title", y_field),
                "domain": y_domain,
            },
        },
        "series": series_field,
        "rows": normalized_rows,
    }
    if series_field in columns:
        series_values = list(dict.fromkeys(str(row[series_field]) for row in ordered_rows))
        chart["encoding"]["color"] = {"field": series_field, "title": series_field}
        chart["series"] = {"field": series_field, "values": series_values}
    if chart_type == "line":
        chart["trend_summary"] = _build_trend_summary(
            normalized_rows,
            x_field=x_field,
            y_field=y_field,
            metric_field=series_field,
            metric_name=y_field,
            expected_x_values=expected_x_values,
        )
        chart["series_gaps"] = _build_series_gaps(
            normalized_rows,
            x_field=x_field,
            metric_field=series_field,
            metric_name=y_field,
            expected_x_values=expected_x_values,
        )
        chart["series_coverage"] = _build_series_coverage(
            normalized_rows,
            x_field=x_field,
            metric_field=series_field,
            metric_name=y_field,
            expected_x_values=expected_x_values,
        )
        chart["imputed_points"] = imputed_points
        if imputed_points:
            chart["source_rows"] = source_rows
    return chart


def _build_trend_summary(
    rows: list[dict[str, Any]],
    *,
    x_field: str,
    y_field: str,
    metric_field: str | None,
    metric_name: str | None = None,
    expected_x_values: list[Any] | None = None,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        label = str(row[metric_field]) if metric_field else str(metric_name or y_field)
        grouped.setdefault(label, []).append(row)

    summary = []
    axis_point_count = len(
        {
            _line_axis_key(value)
            for value in [
                *(expected_x_values or []),
                *(row[x_field] for row in rows),
            ]
        }
    )
    for label, points in grouped.items():
        assessment, _ = _trend_coverage_assessment(
            point_count=len(points),
            axis_point_count=axis_point_count,
        )
        if assessment == "insufficient":
            continue
        first = points[0]
        last = points[-1]
        start_value = float(first[y_field])
        end_value = float(last[y_field])
        absolute_delta = end_value - start_value
        item = {
            "series": label,
            "start_x": first[x_field],
            "end_x": last[x_field],
            "start_value": _rounded_number(start_value),
            "end_value": _rounded_number(end_value),
            "absolute_delta": _rounded_number(absolute_delta),
            "percent_delta": (
                _rounded_number(absolute_delta / start_value * 100)
                if start_value != 0
                else None
            ),
        }
        summary.append(item)
    return summary


def _build_series_gaps(
    rows: list[dict[str, Any]],
    *,
    x_field: str,
    metric_field: str | None,
    metric_name: str | None = None,
    expected_x_values: list[Any] | None = None,
) -> list[dict[str, Any]]:
    axis_values: dict[tuple[str, float | str], Any] = {
        _line_axis_key(value): value for value in expected_x_values or []
    }
    observed: dict[str, set[tuple[str, float | str]]] = {}
    for row in rows:
        axis_key = _line_axis_key(row[x_field])
        axis_values.setdefault(axis_key, row[x_field])
        label = str(row[metric_field]) if metric_field else str(metric_name or "value")
        observed.setdefault(label, set()).add(axis_key)

    gaps = []
    for label, observed_keys in observed.items():
        missing_x = [
            value for key, value in axis_values.items() if key not in observed_keys
        ]
        if missing_x:
            gaps.append({"series": label, "missing_x": missing_x})
    return gaps


def _build_series_coverage(
    rows: list[dict[str, Any]],
    *,
    x_field: str,
    metric_field: str | None,
    metric_name: str | None = None,
    expected_x_values: list[Any] | None = None,
) -> list[dict[str, Any]]:
    axis_keys = list(
        dict.fromkeys(
            [
                *(_line_axis_key(value) for value in expected_x_values or []),
                *(_line_axis_key(row[x_field]) for row in rows),
            ]
        )
    )
    observed: dict[str, set[tuple[str, float | str]]] = {}
    for row in rows:
        label = str(row[metric_field]) if metric_field else str(metric_name or "value")
        observed.setdefault(label, set()).add(_line_axis_key(row[x_field]))

    axis_point_count = len(axis_keys)
    coverage = []
    for label, observed_keys in observed.items():
        point_count = len(observed_keys)
        assessment, reason = _trend_coverage_assessment(
            point_count=point_count,
            axis_point_count=axis_point_count,
        )
        item = {
            "series": label,
            "point_count": point_count,
            "axis_point_count": axis_point_count,
            "coverage_rate": round(point_count / axis_point_count, 4),
            "assessment": assessment,
        }
        if reason:
            item["reason"] = reason
        coverage.append(item)
    return coverage


def _trend_coverage_assessment(
    *, point_count: int, axis_point_count: int
) -> tuple[str, str | None]:
    if point_count < 2:
        return "insufficient", "fewer_than_two_points"
    coverage_rate = point_count / axis_point_count if axis_point_count else 0.0
    if coverage_rate < MIN_TREND_COVERAGE:
        return "insufficient", "coverage_below_0.5"
    if point_count < axis_point_count:
        return "sparse", None
    return "complete", None


def _expected_temporal_axis(intent: dict[str, Any]) -> list[str] | None:
    grain = intent.get("grain")
    range_start = intent.get("range_start")
    range_end = intent.get("range_end_exclusive")
    if grain not in {"day", "week", "month"} or not range_start or not range_end:
        return None
    try:
        start = date.fromisoformat(str(range_start))
        end = date.fromisoformat(str(range_end))
    except ValueError as exc:
        raise ValueError("Chart temporal range must use ISO dates.") from exc
    if start >= end:
        raise ValueError("Chart temporal range start must be before its exclusive end.")

    cursor = (
        date(start.year, start.month, 1)
        if grain == "month"
        else start - timedelta(days=start.weekday())
        if grain == "week"
        else start
    )
    values = []
    while cursor < end:
        if len(values) >= MAX_EXPECTED_AXIS_POINTS:
            raise ValueError(
                f"Chart temporal range exceeds {MAX_EXPECTED_AXIS_POINTS} axis points."
            )
        values.append(cursor.isoformat())
        cursor = (
            date(cursor.year + (cursor.month == 12), cursor.month % 12 + 1, 1)
            if grain == "month"
            else cursor + timedelta(days=7 if grain == "week" else 1)
        )
    return values


def _fill_zero_rows(
    rows: list[dict[str, Any]],
    *,
    expected_x_values: list[Any],
    x_field: str,
    y_fields: list[str],
    series_field: str | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    series_values = (
        list(dict.fromkeys(row[series_field] for row in rows))
        if series_field
        else [None]
    )
    observed = {
        (_line_axis_key(row[x_field]), str(row[series_field]) if series_field else None)
        for row in rows
    }
    completed = list(rows)
    imputed = []
    for x_value in expected_x_values:
        for series_value in series_values:
            key = (
                _line_axis_key(x_value),
                str(series_value) if series_field else None,
            )
            if key in observed:
                continue
            row = {x_field: x_value, **{field: 0 for field in y_fields}}
            point = {"x": x_value, "fields": list(y_fields), "value": 0}
            if series_field:
                row[series_field] = series_value
                point["series"] = series_value
            completed.append(row)
            imputed.append(point)
    return completed, imputed


def _rounded_number(value: float) -> int | float:
    rounded = round(value, 4)
    return int(rounded) if rounded.is_integer() else rounded


def _validate_chart_rows(
    rows: list[dict[str, Any]], *, x_field: str, y_fields: list[str]
) -> None:
    required = {x_field, *y_fields}
    for index, row in enumerate(rows):
        missing = sorted(required - row.keys())
        if missing:
            raise ValueError(
                f"Chart row {index} is missing encoding fields: {', '.join(missing)}."
            )
        for field in y_fields:
            try:
                _numeric_value(row[field])
            except ValueError as exc:
                raise ValueError(f"Chart row {index} field '{field}' must be finite numeric data.") from exc


def _validate_series_rows(rows: list[dict[str, Any]], series_field: str) -> None:
    for index, row in enumerate(rows):
        if series_field not in row or row[series_field] is None or str(row[series_field]).strip() == "":
            raise ValueError(f"Chart row {index} is missing series field '{series_field}'.")


def _validate_categorical_keys(
    rows: list[dict[str, Any]], *, x_field: str, series_field: str | None
) -> None:
    seen: set[tuple[str, str | None]] = set()
    for index, row in enumerate(rows):
        x_value = row[x_field]
        if x_value is None or str(x_value).strip() == "":
            raise ValueError(f"Chart row {index} is missing category field '{x_field}'.")
        series_value = str(row[series_field]) if series_field else None
        key = (str(x_value), series_value)
        if key in seen:
            raise ValueError(
                f"Chart row {index} duplicates categorical key "
                f"x='{x_value}' series='{series_value}'."
            )
        seen.add(key)


def _order_line_rows(rows: list[dict[str, Any]], x_field: str) -> list[dict[str, Any]]:
    temporal_values = [_temporal_value(row.get(x_field)) for row in rows]
    if all(value is not None for value in temporal_values):
        order_values = temporal_values
    else:
        numeric_values = [_numeric_axis_value(row.get(x_field)) for row in rows]
        if not all(value is not None for value in numeric_values):
            raise ValueError(
                "Line chart x values must be consistently temporal or numeric."
            )
        order_values = numeric_values
    return [
        row
        for _, row in sorted(
            zip(order_values, rows, strict=True),
            key=lambda item: item[0],
        )
    ]


def _temporal_value(value: Any) -> float | None:
    if isinstance(value, datetime):
        return value.timestamp()
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time()).timestamp()
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.strip()).timestamp()
    except ValueError:
        return None


def _validate_line_keys(
    rows: list[dict[str, Any]], *, x_field: str, series_field: str | None
) -> None:
    seen: set[tuple[tuple[str, float | str], str | None]] = set()
    for index, row in enumerate(rows):
        series = str(row[series_field]) if series_field else None
        axis_key = _line_axis_key(row[x_field])
        key = (axis_key, series)
        if key in seen:
            raise ValueError(
                f"Chart row {index} duplicates line key x='{row[x_field]}' series='{series}'."
            )
        seen.add(key)


def _line_axis_key(value: Any) -> tuple[str, float | str]:
    temporal = _temporal_value(value)
    if temporal is not None:
        return ("temporal", temporal)
    numeric = _numeric_axis_value(value)
    if numeric is not None:
        return ("numeric", numeric)
    return ("categorical", str(value))


def _numeric_axis_value(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        numeric = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


def _numeric_value(value: Any) -> int | float:
    if isinstance(value, bool) or value is None:
        raise ValueError("Chart values must be numeric.")
    if isinstance(value, Real):
        numeric = float(value)
    else:
        try:
            numeric = float(str(value).replace(",", ""))
        except (TypeError, ValueError) as exc:
            raise ValueError("Chart values must be numeric.") from exc
    if not math.isfinite(numeric):
        raise ValueError("Chart values must be finite.")
    return int(numeric) if numeric.is_integer() else numeric


def _numeric_domain(
    rows: list[dict[str, Any]], y_fields: list[str]
) -> list[int | float]:
    values = [_numeric_value(row[field]) for row in rows for field in y_fields]
    lower = min(0, min(values))
    upper = max(0, max(values))
    if lower == upper:
        upper = 1
    return [lower, upper]
