from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


FabId = Literal["fab10", "fab11", "fab12", "fab13"]

ALLOWED_FABS: set[str] = {"fab10", "fab11", "fab12", "fab13"}


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


SC001_TEMPLATES = {
    "fab_status_summary": fab_status_summary,
    "process_group_status": process_group_status,
    "station_status": station_status,
    "product_status": product_status,
    "lot_status": lot_status,
}
