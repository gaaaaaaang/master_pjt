"""Compile the unambiguous single-table subset of a validated semantic plan.

The model chooses meaning; this compiler only renders the checked plan. Queries
outside this subset still use SQL generation and the same AST validator.
"""
from __future__ import annotations

from app.sub_agent.semantic_plan import SemanticPlan, validate_sql_plan


def compile_single_table(plan: SemanticPlan) -> str | None:
    if not plan.supported or len(plan.tables) != 1 or plan.joins:
        return None
    if plan.aggregates and any(p not in plan.group_by for p in plan.projections):
        return None
    if not plan.aggregates and plan.group_by:
        return None
    if len({p.column for p in plan.projections} | {a.alias for a in plan.aggregates}) != len(plan.projections) + len(plan.aggregates):
        return None

    def ident(value: str) -> str:
        return '"' + value.replace('"', '""') + '"'

    def table(value: str) -> str:
        return ".".join(ident(part) for part in value.split("."))

    def column(ref) -> str:
        return f"{table(ref.table)}.{ident(ref.column)}"

    def literal(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    projections = [column(p) for p in plan.projections]
    for aggregate in plan.aggregates:
        if aggregate.function == "count_rows":
            expression = "COUNT(*)"
        elif aggregate.function == "count_distinct":
            expression = f"COUNT(DISTINCT {column(aggregate.source)})"
        else:
            expression = f"{aggregate.function.upper()}({column(aggregate.source)})"
        projections.append(f"{expression} AS {ident(aggregate.alias)}")
    if not projections:
        return None
    predicates = []
    operators = {"eq": "=", "ne": "<>", "gt": ">", "gte": ">=", "lt": "<", "lte": "<=", "like": "LIKE", "ilike": "ILIKE"}
    for predicate in plan.filters:
        left = column(predicate.source)
        if predicate.operator in {"is_null", "not_null"}:
            predicates.append(f"{left} IS {'NOT ' if predicate.operator == 'not_null' else ''}NULL")
        elif predicate.operator == "in":
            if not predicate.values:
                return None
            predicates.append(f"{left} IN ({', '.join(literal(v) for v in predicate.values)})")
        elif predicate.operator in operators and len(predicate.values) == 1:
            predicates.append(f"{left} {operators[predicate.operator]} {literal(predicate.values[0])}")
        else:
            return None
    static_filters = " AND ".join(predicates)
    for latest in plan.latest_by:
        restriction = f" WHERE {static_filters}" if plan.latest_scope == "filtered" and static_filters else ""
        predicates.append(f"{column(latest)} = (SELECT MAX({column(latest)}) FROM {table(latest.table)}{restriction})")
    sql = f"SELECT {', '.join(projections)} FROM {table(plan.tables[0])}"
    if predicates:
        sql += " WHERE " + " AND ".join(predicates)
    if plan.group_by:
        sql += " GROUP BY " + ", ".join(column(p) for p in plan.group_by)
    if plan.order_by:
        sql += " ORDER BY " + ", ".join(f"{ident(s.output)} {s.direction.upper()}" for s in plan.order_by)
    if plan.result_limit:
        sql += f" LIMIT {plan.result_limit}"
    try:
        validate_sql_plan(sql, plan)
    except ValueError:
        return None
    return sql
