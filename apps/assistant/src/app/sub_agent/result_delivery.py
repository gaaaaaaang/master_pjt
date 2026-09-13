"""Deliver bounded query rows separately from the model's evidence sample."""
from app.config import get_settings


def effective_row_limit(sql, maximum):
    """Use the outer LIMIT, never an inner/CTE limit that may not bound output."""
    from sqlglot import exp, parse_one
    from sqlglot.errors import SqlglotError
    try:
        node = parse_one(sql or "", read="postgres")
        limit = node.args.get("limit") if node else None
        literal = limit.expression if limit else None
        if isinstance(literal, exp.Literal) and literal.is_int:
            return min(maximum, max(0, int(literal.this)))
    except (SqlglotError, ValueError):
        pass
    return maximum


def result_cardinality(rows):
    values = {"returned_row_count": len(rows)}
    if rows and all(row.get("area") is not None for row in rows):
        values["distinct_area_count"] = len({row["area"] for row in rows})
    return values


def result_presentation(evidence):
    """Describe UI attachments using successful evidence, never a model promise."""
    sql = next((item.get("metadata", {}) for item in reversed(evidence)
                if item.get("source_type") == "text2sql_plan"
                and item.get("metadata", {}).get("status") == "succeeded"), {})
    chart = next((item.get("metadata", {}) for item in reversed(evidence)
                  if item.get("source_type") == "visualization_spec"
                  and item.get("metadata", {}).get("status") == "succeeded"), {})
    return {"data_table_delivered": bool(sql.get("row_count")),
            "data_table_row_count": sql.get("row_count", 0),
            "row_limit_reached": bool(sql.get("limit_reached")),
            "data_table_location": "데이터 · SQL 보기",
            "chart_delivered": bool(chart),
            "chart_type": chart.get("chart_type"), "chart_encoding": chart.get("encoding")}


def query_result_payload(result):
    if result is None:
        return None
    limit = result.row_limit if result.row_limit is not None else effective_row_limit(result.sql, get_settings().db_max_rows)
    coverage = result.plan.semantic_plan.get("answer_coverage", []) if result.plan else []
    answer_status = (result.status if result.status != "succeeded" else
                     "empty" if not result.rows else
                     "partial" if result.row_count >= limit or any(item["status"] != "available" for item in coverage) else "complete")
    return {"status": result.status, "answer_status": answer_status, "answer_coverage": coverage, "columns": result.columns,
            "rows": result.rows, "row_count": result.row_count,
            "row_limit": limit, "limit_reached": result.row_count >= limit}


def sample_evidence_rows(rows, max_rows=60):
    if len(rows) <= max_rows:
        return list(rows)
    # Include both ends, so the latest values never disappear from a long trend.
    indices = [round(index * (len(rows) - 1) / (max_rows - 1)) for index in range(max_rows)]
    return [rows[index] for index in indices]
