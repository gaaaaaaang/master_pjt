from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import psycopg
from psycopg.rows import dict_row

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from app.db.fab_catalog import ALLOWED_FABS

FABS = ("fab10", "fab11", "fab12", "fab13")
KST = ZoneInfo("Asia/Seoul")


SNAPSHOT_DDL = """
CREATE TABLE IF NOT EXISTS {schema}.live_process_snapshots_{schema} (
    snapshot_id BIGSERIAL PRIMARY KEY,
    interval_start TIMESTAMPTZ NOT NULL,
    interval_end TIMESTAMPTZ NOT NULL,
    inserted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    fab_id TEXT NOT NULL,
    area TEXT NOT NULL,
    toolgroup TEXT NOT NULL,
    operating_mode TEXT NOT NULL,
    event_type TEXT NOT NULL,
    event_severity TEXT NOT NULL,
    wip_lots INTEGER NOT NULL,
    queue_lots INTEGER NOT NULL,
    lot_starts INTEGER NOT NULL,
    lot_completions INTEGER NOT NULL,
    avg_queue_minutes NUMERIC(8,2) NOT NULL,
    avg_cycle_hours NUMERIC(8,2) NOT NULL,
    yield_percent NUMERIC(6,3) NOT NULL,
    defect_ppm INTEGER NOT NULL,
    utilization_percent NUMERIC(6,2) NOT NULL,
    down_minutes INTEGER NOT NULL,
    pm_minutes INTEGER NOT NULL,
    temperature_c NUMERIC(5,2) NOT NULL,
    humidity_percent NUMERIC(5,2) NOT NULL,
    bottleneck_score NUMERIC(6,3) NOT NULL,
    narrative TEXT NOT NULL,
    UNIQUE (interval_end, fab_id, area)
);
"""

EVENT_DDL = """
CREATE TABLE IF NOT EXISTS {schema}.live_process_events_{schema} (
    detail_id BIGSERIAL PRIMARY KEY,
    interval_start TIMESTAMPTZ NOT NULL,
    interval_end TIMESTAMPTZ NOT NULL,
    inserted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    fab_id TEXT NOT NULL,
    area TEXT NOT NULL,
    event_id TEXT NOT NULL,
    event_kind TEXT NOT NULL,
    equipment_id TEXT,
    lot_id TEXT,
    product_id TEXT,
    route_step TEXT NOT NULL,
    event_type TEXT NOT NULL,
    state TEXT NOT NULL,
    queue_minutes NUMERIC(8,2) NOT NULL,
    process_minutes NUMERIC(8,2) NOT NULL,
    wafers INTEGER NOT NULL,
    yield_loss_ppm INTEGER NOT NULL,
    rework_flag BOOLEAN NOT NULL,
    priority INTEGER NOT NULL,
    narrative TEXT NOT NULL,
    UNIQUE (interval_end, fab_id, event_id)
);
"""

MARKER_DDL = """
CREATE TABLE IF NOT EXISTS {schema}.fab_projection_runs_{schema} (
    projection_date DATE PRIMARY KEY,
    fab_id TEXT NOT NULL,
    raw_rows INTEGER NOT NULL,
    snapshot_rows INTEGER NOT NULL,
    event_rows INTEGER NOT NULL,
    projected_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Project daily FAB raw events into live_process tables.")
    parser.add_argument("--dsn", default=os.getenv("POSTGRES_DSN"))
    parser.add_argument("--target-date", help="KST date to project, YYYY-MM-DD. Defaults to yesterday in KST.")
    return parser.parse_args()


def target_date(value: str | None, now: datetime | None = None) -> date:
    if value:
        return date.fromisoformat(value)
    now = now or datetime.now(KST)
    return (now.astimezone(KST) - timedelta(days=1)).date()


def kst_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min, tzinfo=KST)
    end = start + timedelta(days=1)
    return start.astimezone(UTC), end.astimezone(UTC)


def ensure_tables(conn: psycopg.Connection[Any], fab_id: str) -> None:
    if fab_id not in ALLOWED_FABS:
        raise ValueError("Unsupported FAB")
    with conn.cursor() as cur:
        cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{fab_id}"')
        cur.execute(SNAPSHOT_DDL.format(schema=fab_id))
        cur.execute(EVENT_DDL.format(schema=fab_id))
        cur.execute(MARKER_DDL.format(schema=fab_id))


def load_raw_rows(
    conn: psycopg.Connection[Any],
    fab_id: str,
    start_utc: datetime,
    end_utc: datetime,
) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT *
            FROM {fab_id}.fab_process_raw_events_{fab_id}
            WHERE event_time >= %s AND event_time < %s
            ORDER BY event_time, event_id
            """,
            (start_utc, end_utc),
        )
        return [dict(row) for row in cur.fetchall()]


def severity_rank(severity: str) -> int:
    return {"normal": 0, "low": 1, "medium": 2, "high": 3}.get(severity, 0)


def decimal_avg(values: list[Any]) -> Decimal:
    if not values:
        return Decimal("0.00")
    total = sum(Decimal(str(value)) for value in values)
    return Decimal(f"{total / len(values):.2f}")


def projected_environment(fab_id: str, area: str, interval_end: datetime) -> tuple[Decimal, Decimal]:
    seed = sum(ord(char) for char in f"{fab_id}:{area}:{interval_end.isoformat()}")
    temperature = Decimal(f"{22.0 + (seed % 23) / 10:.2f}")
    humidity = Decimal(f"{42.0 + (seed % 17) / 10:.2f}")
    return temperature, humidity


def build_snapshots(raw_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[datetime, str], list[dict[str, Any]]] = defaultdict(list)
    for row in raw_rows:
        interval_end = row["created_at"]
        if interval_end.tzinfo is None:
            interval_end = interval_end.replace(tzinfo=UTC)
        groups[(interval_end, row["area"])].append(row)

    snapshots: list[dict[str, Any]] = []
    for (interval_end, area), rows in sorted(groups.items()):
        fab_id = rows[0]["fab_id"]
        interval_start = interval_end - timedelta(minutes=15)
        ranked = sorted(rows, key=lambda row: severity_rank(row["severity"]), reverse=True)
        top = ranked[0]
        wafers = sum(int(row["wafers"]) for row in rows)
        defects = sum(int(row["defect_count"]) for row in rows)
        yield_loss = sum(int(row["yield_loss_ppm"]) for row in rows)
        down_minutes = int(sum(Decimal(str(row["down_minutes"])) for row in rows))
        pm_minutes = int(sum(Decimal(str(row["pm_minutes"])) for row in rows))
        queue_lots = sum(1 for row in rows if row["event_type"] == "queue_enter" or Decimal(str(row["queue_minutes"])) >= 30)
        lot_starts = sum(1 for row in rows if row["event_type"] == "process_start")
        lot_completions = sum(1 for row in rows if row["event_type"] == "process_end")
        avg_queue = decimal_avg([row["queue_minutes"] for row in rows])
        avg_process = decimal_avg([row["process_minutes"] for row in rows])
        avg_cycle = Decimal(f"{(avg_queue + avg_process) / Decimal(60):.2f}")
        yield_percent = Decimal(f"{max(80, 99.5 - yield_loss / 10000):.3f}")
        defect_ppm = int(defects * 1_000_000 / max(1, wafers))
        utilization = Decimal(f"{min(99, max(35, 65 + float(avg_process) * 0.45 - down_minutes * 0.8 - pm_minutes * 0.4)):.2f}")
        bottleneck = Decimal(
            f"{min(1.0, queue_lots / 16 + down_minutes / 60 + pm_minutes / 80 + max(0, sum(int(row['wip_delta']) for row in rows)) / 250):.3f}"
        )
        temperature, humidity = projected_environment(fab_id, area, interval_end)
        snapshots.append(
            {
                "interval_start": interval_start,
                "interval_end": interval_end,
                "fab_id": fab_id,
                "area": area,
                "toolgroup": top["toolgroup"],
                "operating_mode": "incident" if severity_rank(top["severity"]) > 0 else "normal",
                "event_type": top["event_type"],
                "event_severity": top["severity"],
                "wip_lots": max(0, sum(max(0, int(row["wip_delta"])) for row in rows)),
                "queue_lots": queue_lots,
                "lot_starts": lot_starts,
                "lot_completions": lot_completions,
                "avg_queue_minutes": avg_queue,
                "avg_cycle_hours": avg_cycle,
                "yield_percent": yield_percent,
                "defect_ppm": defect_ppm,
                "utilization_percent": utilization,
                "down_minutes": down_minutes,
                "pm_minutes": pm_minutes,
                "temperature_c": temperature,
                "humidity_percent": humidity,
                "bottleneck_score": bottleneck,
                "narrative": f"{fab_id} {area} projected from {len(rows)} raw events for {interval_end.isoformat()}.",
            }
        )
    return snapshots


def build_events(raw_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    projected = []
    for row in raw_rows:
        interval_end = row["created_at"]
        if interval_end.tzinfo is None:
            interval_end = interval_end.replace(tzinfo=UTC)
        interval_start = interval_end - timedelta(minutes=15)
        event_kind = "equipment" if row["lot_id"] is None or row["actor_role"] == "equipment" else "lot"
        projected.append(
            {
                "interval_start": interval_start,
                "interval_end": interval_end,
                "fab_id": row["fab_id"],
                "area": row["area"],
                "event_id": row["event_id"],
                "event_kind": event_kind,
                "equipment_id": row["equipment_id"],
                "lot_id": row["lot_id"],
                "product_id": row["product_id"],
                "route_step": row["step_id"],
                "event_type": row["event_type"],
                "state": row["event_status"],
                "queue_minutes": row["queue_minutes"],
                "process_minutes": row["process_minutes"],
                "wafers": row["wafers"],
                "yield_loss_ppm": row["yield_loss_ppm"],
                "rework_flag": row["rework_flag"],
                "priority": severity_rank(row["severity"]) * 100 + int(row["rework_flag"]) * 20,
                "narrative": row["narrative"],
            }
        )
    return projected


def replace_projection(
    conn: psycopg.Connection[Any],
    fab_id: str,
    day: date,
    start_utc: datetime,
    end_utc: datetime,
    snapshots: list[dict[str, Any]],
    events: list[dict[str, Any]],
    raw_count: int,
) -> dict[str, int]:
    with conn.cursor() as cur:
        cur.execute(
            f"DELETE FROM {fab_id}.live_process_snapshots_{fab_id} WHERE interval_end >= %s AND interval_end <= %s",
            (start_utc, end_utc),
        )
        cur.execute(
            f"DELETE FROM {fab_id}.live_process_events_{fab_id} WHERE interval_end >= %s AND interval_end <= %s",
            (start_utc, end_utc),
        )
        if snapshots:
            columns = tuple(snapshots[0])
            placeholders = ", ".join([f"%({column})s" for column in columns])
            cur.executemany(
                f"INSERT INTO {fab_id}.live_process_snapshots_{fab_id} ({', '.join(columns)}) VALUES ({placeholders})",
                snapshots,
            )
        if events:
            columns = tuple(events[0])
            placeholders = ", ".join([f"%({column})s" for column in columns])
            cur.executemany(
                f"INSERT INTO {fab_id}.live_process_events_{fab_id} ({', '.join(columns)}) VALUES ({placeholders})",
                events,
            )
        cur.execute(
            f"""
            INSERT INTO {fab_id}.fab_projection_runs_{fab_id} (
                projection_date, fab_id, raw_rows, snapshot_rows, event_rows
            )
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (projection_date) DO UPDATE SET
                raw_rows = EXCLUDED.raw_rows,
                snapshot_rows = EXCLUDED.snapshot_rows,
                event_rows = EXCLUDED.event_rows,
                projected_at = now()
            """,
            (day, fab_id, raw_count, len(snapshots), len(events)),
        )
    return {"raw_rows": raw_count, "snapshot_rows": len(snapshots), "event_rows": len(events)}


def run_projection(dsn: str, day: date) -> dict[str, Any]:
    start_utc, end_utc = kst_bounds(day)
    summary: dict[str, Any] = {}
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        for fab_id in FABS:
            ensure_tables(conn, fab_id)
            raw_rows = load_raw_rows(conn, fab_id, start_utc, end_utc)
            snapshots = build_snapshots(raw_rows)
            events = build_events(raw_rows)
            summary[fab_id] = replace_projection(
                conn, fab_id, day, start_utc, end_utc, snapshots, events, len(raw_rows)
            )
        conn.commit()
    return {
        "target_date_kst": day.isoformat(),
        "range_start_utc": start_utc.isoformat(),
        "range_end_utc": end_utc.isoformat(),
        "summary": summary,
    }


def main() -> None:
    args = parse_args()
    if not args.dsn:
        raise SystemExit("POSTGRES_DSN is required.")
    result = run_projection(args.dsn, target_date(args.target_date))
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
