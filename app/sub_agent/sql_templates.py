from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

FabId = Literal["fab10", "fab11", "fab12", "fab13"]

ALLOWED_FABS: set[str] = {"fab10", "fab11", "fab12", "fab13"}

ROUTE_TABLES_BY_FAB: dict[str, set[str]] = {
    "fab10": {"route_product_3", "route_product_4"},
    "fab11": {f"route_product_{idx}" for idx in range(1, 11)},
    "fab12": {"route_product_3", "route_product_4", "route_product_e3"},
    "fab13": {
        *(f"route_product_{idx}" for idx in range(1, 11)),
        "route_product_e1",
        "route_product_e2",
        "route_product_e3",
    },
}


@dataclass(frozen=True)
class SqlTemplate:
    name: str
    description: str
    required_slots: tuple[str, ...]
    sql: str


def _schema(fab_id: str) -> FabId:
    normalized = fab_id.lower().strip()
    if normalized not in ALLOWED_FABS:
        raise ValueError(f"Unsupported fab_id: {fab_id}")
    return normalized  # type: ignore[return-value]


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _normalized_token(value: str) -> str:
    return value.lower().strip().replace("-", "_").replace(" ", "_")


def route_table_for_product(product: str, fab_id: str | None = None) -> str:
    token = _normalized_token(product)
    for prefix in ("product_", "route_product_"):
        if token.startswith(prefix):
            token = token.removeprefix(prefix)
            break

    if not token:
        raise ValueError("Product is required for route lookup.")

    allowed = {"1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "e1", "e2", "e3"}
    if token not in allowed:
        raise ValueError(f"Unsupported product route: {product}")
    table_name = f"route_product_{token}"
    if fab_id:
        schema = _schema(fab_id)
        if table_name not in ROUTE_TABLES_BY_FAB[schema]:
            raise ValueError(f"Route table is not available in {schema}: {table_name}")
    return table_name


def fab_status_summary(fab_id: str) -> SqlTemplate:
    schema = _schema(fab_id)
    return SqlTemplate(
        name="sc001_fab_status_summary",
        description="FAB-level WIP, starts, completions, on-time rate, and cycle average.",
        required_slots=("fab_id",),
        sql=f"""
SELECT
    { _sql_literal(schema) } AS fab_id,
    report_time,
    period,
    lotstarts,
    lotcomps,
    wiplotavg,
    ontime_percent,
    cycleavg
FROM {schema}.autosched_perf
WHERE relative = 'Y'
  AND period <> 'WarmUp'
ORDER BY report_time DESC NULLS LAST, source_row_id DESC
LIMIT 1
""".strip(),
    )


def process_group_status(fab_id: str, process_group: str | None = None) -> SqlTemplate:
    schema = _schema(fab_id)
    predicate = ""
    required_slots = ["fab_id"]
    if process_group:
        predicate = f"\n  AND stngrp ILIKE '%' || {_sql_literal(process_group)} || '%'"
        required_slots.append("process_group")

    return SqlTemplate(
        name="sc001_process_group_status",
        description="Process group status ranked by average WIP.",
        required_slots=tuple(required_slots),
        sql=f"""
SELECT
    { _sql_literal(schema) } AS fab_id,
    report_time,
    period,
    stngrp,
    lotcomps,
    util_percent,
    wiplotavg,
    proc_percent,
    down_percent,
    pm_percent
FROM {schema}.autosched_stngrp
WHERE relative = 'Y'
  AND period <> 'WarmUp'{predicate}
ORDER BY wiplotavg DESC NULLS LAST, util_percent DESC NULLS LAST
LIMIT 20
""".strip(),
    )


def station_status(fab_id: str, station: str | None = None) -> SqlTemplate:
    schema = _schema(fab_id)
    predicate = ""
    required_slots = ["fab_id"]
    if station:
        predicate = f"\n  AND stn ILIKE '%' || {_sql_literal(station)} || '%'"
        required_slots.append("station")

    return SqlTemplate(
        name="sc001_station_status",
        description="Station/tool status with current state, utilization, WIP, PM, and down ratio.",
        required_slots=tuple(required_slots),
        sql=f"""
SELECT
    { _sql_literal(schema) } AS fab_id,
    report_time,
    period,
    stn,
    lotcomps,
    util_percent,
    wiplotavg,
    curstate,
    down_percent,
    pm_percent,
    proc_percent
FROM {schema}.autosched_stn
WHERE relative = 'Y'
  AND period <> 'WarmUp'{predicate}
ORDER BY wiplotavg DESC NULLS LAST, util_percent DESC NULLS LAST
LIMIT 20
""".strip(),
    )


def product_status(fab_id: str, product: str | None = None) -> SqlTemplate:
    schema = _schema(fab_id)
    predicate = ""
    required_slots = ["fab_id"]
    if product:
        predicate = f"\n  AND part ILIKE '%' || {_sql_literal(product)} || '%'"
        required_slots.append("product")

    return SqlTemplate(
        name="sc001_product_status",
        description="Product-level starts, completions, WIP, current WIP, and on-time rate.",
        required_slots=tuple(required_slots),
        sql=f"""
SELECT
    { _sql_literal(schema) } AS fab_id,
    report_time,
    period,
    part,
    lotstarts,
    lotcomps,
    wiplotavg,
    wiplotcur,
    ontime_percent,
    cycleavg
FROM {schema}.autosched_part
WHERE relative = 'Y'
  AND period <> 'WarmUp'{predicate}
ORDER BY wiplotavg DESC NULLS LAST, lotcomps DESC NULLS LAST
LIMIT 20
""".strip(),
    )


def lot_status(fab_id: str, lot_id: str) -> SqlTemplate:
    schema = _schema(fab_id)
    return SqlTemplate(
        name="sc001_lot_status",
        description="Lot-level route progress and timing status.",
        required_slots=("fab_id", "lot_id"),
        sql=f"""
SELECT
    { _sql_literal(schema) } AS fab_id,
    part,
    lot,
    startdate,
    compdate,
    duedate,
    stepcomps,
    curstn,
    curstep,
    cyclemax,
    xtheormax
FROM {schema}.autosched_lot
WHERE lot = {_sql_literal(lot_id)}
ORDER BY source_row_id DESC
LIMIT 10
""".strip(),
    )


def master_toolgroups(
    fab_id: str,
    area: str | None = None,
    toolgroup: str | None = None,
) -> SqlTemplate:
    schema = _schema(fab_id)
    predicates = []
    required_slots = ["fab_id"]
    if area:
        predicates.append(f"area ILIKE '%' || {_sql_literal(area)} || '%'")
        required_slots.append("area")
    if toolgroup:
        predicates.append(f"toolgroup ILIKE '%' || {_sql_literal(toolgroup)} || '%'")
        required_slots.append("toolgroup")
    where_clause = "\nWHERE " + "\n  AND ".join(predicates) if predicates else ""

    return SqlTemplate(
        name="master_toolgroups",
        description="Toolgroup and area master-data lookup from SMT2020 General Data.",
        required_slots=tuple(required_slots),
        sql=f"""
SELECT
    {_sql_literal(schema)} AS fab_id,
    area,
    toolgroup,
    number_of_tools,
    toolgrouplocation,
    dispatching,
    ranking_1,
    ranking_2,
    ranking_3,
    tool_wake_up_ranking
FROM {schema}.toolgroups{where_clause}
ORDER BY area, toolgroup
LIMIT 50
""".strip(),
    )


def master_route_steps(
    fab_id: str,
    product: str,
    area: str | None = None,
    toolgroup: str | None = None,
) -> SqlTemplate:
    schema = _schema(fab_id)
    table_name = route_table_for_product(product, schema)
    predicates = []
    required_slots = ["fab_id", "product"]
    if area:
        predicates.append(f"area ILIKE '%' || {_sql_literal(area)} || '%'")
        required_slots.append("area")
    if toolgroup:
        predicates.append(f"toolgroup ILIKE '%' || {_sql_literal(toolgroup)} || '%'")
        required_slots.append("toolgroup")
    where_clause = "\nWHERE " + "\n  AND ".join(predicates) if predicates else ""

    return SqlTemplate(
        name="master_route_steps",
        description="Route step lookup from product-specific SMT2020 route tables.",
        required_slots=tuple(required_slots),
        sql=f"""
SELECT
    {_sql_literal(schema)} AS fab_id,
    route,
    step,
    step_description,
    area,
    toolgroup,
    processing_unit,
    processingtime_distribution,
    mean,
    "offset",
    pt_units,
    batch_minimum,
    batch_maximum,
    rework_probability_in_percent,
    processing_probability_in_percent_sampling
FROM {schema}.{table_name}{where_clause}
ORDER BY source_row_id
LIMIT 100
""".strip(),
    )


def release_plan_lookup(
    fab_id: str,
    product: str | None = None,
    route: str | None = None,
    release_scenario: str | None = None,
) -> SqlTemplate:
    schema = _schema(fab_id)
    predicates = []
    required_slots = ["fab_id"]
    if product:
        predicates.append(f"product_name ILIKE '%' || {_sql_literal(product)} || '%'")
        required_slots.append("product")
    if route:
        predicates.append(f"route_name ILIKE '%' || {_sql_literal(route)} || '%'")
        required_slots.append("route")
    if release_scenario:
        predicates.append(
            f"release_scenario ILIKE '%' || {_sql_literal(release_scenario)} || '%'"
        )
        required_slots.append("release_scenario")
    where_clause = "\nWHERE " + "\n  AND ".join(predicates) if predicates else ""

    return SqlTemplate(
        name="release_plan_lookup",
        description="Release-plan lookup from SMT2020 lotrelease tables.",
        required_slots=tuple(required_slots),
        sql=f"""
SELECT *
FROM (
    SELECT
        {_sql_literal(schema)} AS fab_id,
        'lotrelease' AS source_table,
        product_name,
        route_name,
        lot_name_type,
        priority,
        superhotlot,
        wafers_per_lot,
        start_date,
        due_date,
        release_scenario
    FROM {schema}.lotrelease{where_clause}
    UNION ALL
    SELECT
        {_sql_literal(schema)} AS fab_id,
        'lotrelease_variable_due_dates' AS source_table,
        product_name,
        route_name,
        lot_name_type,
        priority,
        superhotlot,
        wafers_per_lot,
        start_date,
        due_date,
        release_scenario
    FROM {schema}.lotrelease_variable_due_dates{where_clause}
    UNION ALL
    SELECT
        {_sql_literal(schema)} AS fab_id,
        'lotrelease_engineering' AS source_table,
        product_name,
        route_name,
        lot_name_type,
        priority,
        NULL AS superhotlot,
        wafers_per_lot,
        start_date,
        due_date,
        release_scenario
    FROM {schema}.lotrelease_engineering{where_clause}
) release_plan
ORDER BY start_date NULLS LAST, due_date NULLS LAST, product_name, lot_name_type
LIMIT 100
""".strip(),
    )


SC001_TEMPLATES = {
    "fab_status_summary": fab_status_summary,
    "process_group_status": process_group_status,
    "station_status": station_status,
    "product_status": product_status,
    "lot_status": lot_status,
}

GENERAL_DATA_TEMPLATES = {
    "master_toolgroups": master_toolgroups,
    "master_route_steps": master_route_steps,
    "release_plan_lookup": release_plan_lookup,
}
