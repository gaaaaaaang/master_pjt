from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import psycopg
from psycopg.rows import dict_row

FABS = ("fab10", "fab11", "fab12", "fab13")
AREAS = ("photo", "etch", "deposition", "implant", "cmp", "metrology")
EQUIPMENT_EVENTS_PER_AREA = 8
LOT_EVENTS_PER_AREA = 16


@dataclass(frozen=True)
class FabProfile:
    base_wip: int
    throughput: int
    yield_pct: float
    defect_ppm: int
    util_pct: float
    volatility: float
    risk_bias: float
    tendency: str


PROFILES = {
    "fab10": FabProfile(1260, 186, 97.3, 410, 87.5, 0.45, 0.25, "stable_hvlm"),
    "fab11": FabProfile(920, 132, 95.8, 680, 81.0, 0.80, 0.48, "aging_lvhm"),
    "fab12": FabProfile(1110, 154, 96.2, 590, 83.5, 1.05, 0.55, "new_process_mix"),
    "fab13": FabProfile(1460, 205, 96.7, 530, 89.2, 1.20, 0.62, "high_load_expansion"),
}


EVENTS = {
    "fab10": ("none", "minor_metrology_recheck", "photo_queue_watch"),
    "fab11": ("none", "pm_overrun", "etch_tool_down", "carrier_wait"),
    "fab12": ("none", "recipe_tuning", "yield_watch", "rework_burst"),
    "fab13": ("none", "wip_surge", "bottleneck_alarm", "sorter_hold", "yield_dip"),
}


CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS {schema}.live_process_snapshots (
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

CREATE_DETAIL_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS {schema}.live_process_events (
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Insert one two-hour simulated live process snapshot for fab10-fab13."
    )
    parser.add_argument("--dsn", default=os.getenv("POSTGRES_DSN"))
    parser.add_argument("--run-at", help="ISO timestamp for interval end. Defaults to now UTC.")
    return parser.parse_args()


def parse_run_at(value: str | None) -> datetime:
    if not value:
        return datetime.now(UTC)
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def seed_for(fab_id: str, interval_end: datetime) -> int:
    key = f"{fab_id}:{interval_end.isoformat(timespec='hours')}"
    return int(hashlib.sha256(key.encode()).hexdigest()[:16], 16)


def choose_event(fab_id: str, rng: random.Random, prior_event: str | None, hour_index: int) -> str:
    profile = PROFILES[fab_id]
    if prior_event and prior_event != "none" and rng.random() < 0.45:
        return "recovery_watch"
    periodic_pressure = 0.18 if hour_index % 12 in {4, 6, 8} else 0.0
    if rng.random() > profile.risk_bias + periodic_pressure:
        return "none"
    return rng.choice([event for event in EVENTS[fab_id] if event != "none"])


def severity_for(event_type: str, rng: random.Random) -> str:
    if event_type == "none":
        return "normal"
    if event_type == "recovery_watch":
        return rng.choice(("low", "medium"))
    if event_type in {"etch_tool_down", "bottleneck_alarm", "yield_dip"}:
        return rng.choice(("medium", "high"))
    return rng.choice(("low", "medium"))


def event_impact(event_type: str, severity: str) -> dict[str, float]:
    level = {"normal": 0.0, "low": 0.6, "medium": 1.0, "high": 1.6}[severity]
    impacts = {
        "none": (0, 0, 0, 0, 0),
        "recovery_watch": (45, 10, -0.18, 45, 8),
        "minor_metrology_recheck": (30, 8, -0.10, 25, 0),
        "photo_queue_watch": (70, 18, -0.12, 35, 4),
        "pm_overrun": (95, 28, -0.22, 70, 22),
        "etch_tool_down": (150, 44, -0.55, 170, 48),
        "carrier_wait": (85, 24, -0.14, 40, 10),
        "recipe_tuning": (60, 18, -0.20, 95, 6),
        "yield_watch": (75, 20, -0.42, 150, 4),
        "rework_burst": (115, 34, -0.48, 180, 6),
        "wip_surge": (160, 42, -0.18, 65, 4),
        "bottleneck_alarm": (210, 58, -0.34, 110, 18),
        "sorter_hold": (125, 36, -0.28, 90, 14),
        "yield_dip": (100, 30, -0.70, 240, 8),
    }
    wip, queue, yld, defect, down = impacts[event_type]
    return {
        "wip": wip * level,
        "queue": queue * level,
        "yield": yld * level,
        "defect": defect * level,
        "down": down * level,
    }


def get_prior_state(conn: psycopg.Connection[Any], schema: str) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT event_type, yield_percent, wip_lots, utilization_percent
            FROM {schema}.live_process_snapshots
            WHERE fab_id = %s
            ORDER BY interval_end DESC, snapshot_id DESC
            LIMIT 1
            """,
            (schema,),
        )
        return cur.fetchone()


def build_rows(
    fab_id: str,
    interval_start: datetime,
    interval_end: datetime,
    prior: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    profile = PROFILES[fab_id]
    rng = random.Random(seed_for(fab_id, interval_end))
    hour_index = int(interval_end.timestamp() // 3600)
    event_type = choose_event(fab_id, rng, prior["event_type"] if prior else None, hour_index)
    severity = severity_for(event_type, rng)
    impact = event_impact(event_type, severity)
    pressure = 1.0 + (0.05 if 8 <= interval_end.hour < 20 else -0.03)
    rows: list[dict[str, Any]] = []

    for idx, area in enumerate(AREAS):
        area_bias = 0.72 + idx * 0.11 + rng.uniform(-0.06, 0.08)
        affected = area in {"photo", "etch", "metrology"} if event_type != "none" else False
        local_impact = 1.0 if affected else 0.35
        wip = int((profile.base_wip / len(AREAS)) * area_bias * pressure + impact["wip"] * local_impact)
        starts = max(12, int(profile.throughput * area_bias / len(AREAS) + rng.gauss(0, 4)))
        comps = max(8, int(starts * rng.uniform(0.88, 1.08) - impact["down"] * local_impact / 6))
        queue = max(3, int(wip * rng.uniform(0.12, 0.25) + impact["queue"] * local_impact))
        util = max(45.0, min(98.5, profile.util_pct + rng.gauss(0, 3 * profile.volatility) - impact["down"] * 0.12))
        yield_pct = max(
            88.0,
            min(99.2, profile.yield_pct + rng.gauss(0, 0.18 * profile.volatility) + impact["yield"] * local_impact),
        )
        defect = max(80, int(profile.defect_ppm + rng.gauss(0, 35 * profile.volatility) + impact["defect"] * local_impact))
        down = max(0, int(rng.uniform(0, 5) + impact["down"] * local_impact))
        pm = max(0, int(rng.uniform(0, 8) + (18 if event_type == "pm_overrun" and affected else 0)))
        queue_min = max(4.0, queue * rng.uniform(1.8, 3.4) / max(comps, 1) * 10)
        cycle_hours = max(6.0, 28.0 + queue_min / 8 + rng.gauss(0, 1.5))
        bottleneck = min(1.0, max(0.05, queue / max(wip, 1) + down / 120 + (98 - util) / 160))
        rows.append(
            {
                "interval_start": interval_start,
                "interval_end": interval_end,
                "fab_id": fab_id,
                "area": area,
                "toolgroup": f"{fab_id.upper()}_{area.upper()}_TG{idx + 1:02d}",
                "operating_mode": profile.tendency,
                "event_type": event_type,
                "event_severity": severity,
                "wip_lots": wip,
                "queue_lots": queue,
                "lot_starts": starts,
                "lot_completions": comps,
                "avg_queue_minutes": Decimal(f"{queue_min:.2f}"),
                "avg_cycle_hours": Decimal(f"{cycle_hours:.2f}"),
                "yield_percent": Decimal(f"{yield_pct:.3f}"),
                "defect_ppm": defect,
                "utilization_percent": Decimal(f"{util:.2f}"),
                "down_minutes": down,
                "pm_minutes": pm,
                "temperature_c": Decimal(f"{22.0 + rng.gauss(0, 0.35):.2f}"),
                "humidity_percent": Decimal(f"{43.0 + rng.gauss(0, 1.2):.2f}"),
                "bottleneck_score": Decimal(f"{bottleneck:.3f}"),
                "narrative": narrative(fab_id, area, event_type, severity),
            }
        )
    return rows


def build_detail_rows(
    fab_id: str,
    interval_start: datetime,
    interval_end: datetime,
    summary_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rng = random.Random(seed_for(f"{fab_id}:detail", interval_end))
    detail_rows: list[dict[str, Any]] = []
    for summary in summary_rows:
        area = summary["area"]
        event_type = summary["event_type"]
        severity = summary["event_severity"]
        for idx in range(EQUIPMENT_EVENTS_PER_AREA):
            equipment_id = f"{summary['toolgroup']}_EQ{idx + 1:02d}"
            state = equipment_state(event_type, severity, idx, rng)
            detail_rows.append(
                {
                    "interval_start": interval_start,
                    "interval_end": interval_end,
                    "fab_id": fab_id,
                    "area": area,
                    "event_id": f"{area}-eq-{idx + 1:02d}",
                    "event_kind": "equipment_state",
                    "equipment_id": equipment_id,
                    "lot_id": None,
                    "product_id": None,
                    "route_step": f"{area.upper()}_STEP_{idx % 5 + 1:02d}",
                    "event_type": event_type,
                    "state": state,
                    "queue_minutes": Decimal(f"{rng.uniform(0.0, 18.0):.2f}"),
                    "process_minutes": Decimal(f"{rng.uniform(12.0, 56.0):.2f}"),
                    "wafers": 0,
                    "yield_loss_ppm": equipment_yield_loss(state, rng),
                    "rework_flag": False,
                    "priority": 3 if state in {"DOWN", "PM_DELAY"} else 5,
                    "narrative": f"{equipment_id} reported {state} during the interval.",
                }
            )
        for idx in range(LOT_EVENTS_PER_AREA):
            lot_state = lot_event_state(event_type, severity, rng)
            lot_id = lot_identifier(fab_id, area, interval_end, idx)
            rework = lot_state in {"REWORK", "HOLD_REVIEW"}
            yield_loss = lot_yield_loss(lot_state, event_type, rng)
            detail_rows.append(
                {
                    "interval_start": interval_start,
                    "interval_end": interval_end,
                    "fab_id": fab_id,
                    "area": area,
                    "event_id": f"{area}-lot-{idx + 1:02d}",
                    "event_kind": "lot_progress",
                    "equipment_id": f"{summary['toolgroup']}_EQ{idx % EQUIPMENT_EVENTS_PER_AREA + 1:02d}",
                    "lot_id": lot_id,
                    "product_id": f"PROD-{rng.randint(1, 8):02d}",
                    "route_step": f"{area.upper()}_STEP_{idx % 9 + 1:02d}",
                    "event_type": event_type,
                    "state": lot_state,
                    "queue_minutes": Decimal(f"{lot_queue_minutes(lot_state, rng):.2f}"),
                    "process_minutes": Decimal(f"{rng.uniform(18.0, 92.0):.2f}"),
                    "wafers": rng.choice((24, 25)),
                    "yield_loss_ppm": yield_loss,
                    "rework_flag": rework,
                    "priority": rng.choice((1, 2, 3, 4, 5)),
                    "narrative": f"{lot_id} moved through {area} as {lot_state}.",
                }
            )
    return detail_rows


def narrative(fab_id: str, area: str, event_type: str, severity: str) -> str:
    if event_type == "none":
        return f"{fab_id} {area} ran close to plan during this two-hour window."
    if event_type == "recovery_watch":
        return f"{fab_id} {area} is stabilizing after the prior interval issue; monitor residual queue."
    return f"{fab_id} {area} observed {severity} {event_type}; metrics reflect the estimated two-hour impact."


def equipment_state(event_type: str, severity: str, idx: int, rng: random.Random) -> str:
    if event_type == "none":
        return rng.choices(("RUNNING", "IDLE", "QUAL_CHECK"), weights=(82, 14, 4), k=1)[0]
    if event_type == "recovery_watch":
        return rng.choices(("RUNNING", "QUAL_CHECK", "IDLE"), weights=(70, 18, 12), k=1)[0]
    if severity == "high" and idx in {0, 3}:
        return rng.choice(("DOWN", "PM_DELAY"))
    if severity == "medium" and idx in {1, 4}:
        return rng.choice(("QUAL_CHECK", "PM_DELAY", "STARVED"))
    return rng.choices(("RUNNING", "IDLE", "QUAL_CHECK", "STARVED"), weights=(68, 12, 12, 8), k=1)[0]


def equipment_yield_loss(state: str, rng: random.Random) -> int:
    base = {
        "RUNNING": 0,
        "IDLE": 0,
        "QUAL_CHECK": 45,
        "STARVED": 35,
        "DOWN": 120,
        "PM_DELAY": 90,
    }[state]
    return max(0, int(base + rng.gauss(0, 12)))


def lot_event_state(event_type: str, severity: str, rng: random.Random) -> str:
    if event_type == "none":
        return rng.choices(("STARTED", "COMPLETED", "QUEUED"), weights=(30, 48, 22), k=1)[0]
    if severity in {"medium", "high"}:
        return rng.choices(
            ("STARTED", "COMPLETED", "QUEUED", "HOLD_REVIEW", "REWORK"),
            weights=(20, 32, 25, 13, 10),
            k=1,
        )[0]
    return rng.choices(
        ("STARTED", "COMPLETED", "QUEUED", "HOLD_REVIEW", "REWORK"),
        weights=(26, 42, 22, 6, 4),
        k=1,
    )[0]


def lot_identifier(fab_id: str, area: str, interval_end: datetime, idx: int) -> str:
    stamp = interval_end.strftime("%m%d%H")
    return f"{fab_id.upper()}-{area[:2].upper()}-{stamp}-{idx + 1:03d}"


def lot_queue_minutes(state: str, rng: random.Random) -> float:
    if state == "QUEUED":
        return rng.uniform(35.0, 120.0)
    if state in {"HOLD_REVIEW", "REWORK"}:
        return rng.uniform(65.0, 180.0)
    return rng.uniform(2.0, 36.0)


def lot_yield_loss(state: str, event_type: str, rng: random.Random) -> int:
    state_base = {
        "STARTED": 12,
        "COMPLETED": 18,
        "QUEUED": 28,
        "HOLD_REVIEW": 160,
        "REWORK": 260,
    }[state]
    event_extra = 80 if event_type in {"yield_watch", "yield_dip", "rework_burst"} else 0
    return max(0, int(state_base + event_extra + rng.gauss(0, 25)))


def insert_rows(conn: psycopg.Connection[Any], schema: str, rows: list[dict[str, Any]]) -> int:
    columns = tuple(rows[0])
    placeholders = ", ".join([f"%({column})s" for column in columns])
    assignments = ", ".join(
        f"{column} = EXCLUDED.{column}"
        for column in columns
        if column not in {"interval_end", "fab_id", "area"}
    )
    sql = """
        INSERT INTO {schema}.live_process_snapshots ({columns})
        VALUES ({placeholders})
        ON CONFLICT (interval_end, fab_id, area)
        DO UPDATE SET {assignments}
    """.format(
        schema=schema,
        columns=", ".join(columns),
        placeholders=placeholders,
        assignments=assignments,
    )
    with conn.cursor() as cur:
        cur.executemany(sql, rows)
    return len(rows)


def insert_detail_rows(conn: psycopg.Connection[Any], schema: str, rows: list[dict[str, Any]]) -> int:
    columns = tuple(rows[0])
    placeholders = ", ".join([f"%({column})s" for column in columns])
    assignments = ", ".join(
        f"{column} = EXCLUDED.{column}"
        for column in columns
        if column not in {"interval_end", "fab_id", "event_id"}
    )
    sql = """
        INSERT INTO {schema}.live_process_events ({columns})
        VALUES ({placeholders})
        ON CONFLICT (interval_end, fab_id, event_id)
        DO UPDATE SET {assignments}
    """.format(
        schema=schema,
        columns=", ".join(columns),
        placeholders=placeholders,
        assignments=assignments,
    )
    with conn.cursor() as cur:
        cur.executemany(sql, rows)
    return len(rows)


def main() -> None:
    args = parse_args()
    if not args.dsn:
        raise SystemExit("POSTGRES_DSN is required.")
    interval_end = parse_run_at(args.run_at).replace(minute=0, second=0, microsecond=0)
    interval_start = interval_end - timedelta(hours=2)
    summary = {}

    with psycopg.connect(args.dsn, row_factory=dict_row) as conn:
        for fab_id in FABS:
            with conn.cursor() as cur:
                cur.execute(CREATE_TABLE_SQL.format(schema=fab_id))
                cur.execute(CREATE_DETAIL_TABLE_SQL.format(schema=fab_id))
            prior = get_prior_state(conn, fab_id)
            rows = build_rows(fab_id, interval_start, interval_end, prior)
            detail_rows = build_detail_rows(fab_id, interval_start, interval_end, rows)
            inserted = insert_rows(conn, fab_id, rows)
            detail_inserted = insert_detail_rows(conn, fab_id, detail_rows)
            summary[fab_id] = {
                "snapshot_rows": inserted,
                "event_rows": detail_inserted,
                "rows": inserted + detail_inserted,
                "event": rows[0]["event_type"],
                "severity": rows[0]["event_severity"],
                "avg_yield": round(sum(float(row["yield_percent"]) for row in rows) / len(rows), 3),
                "total_wip": sum(row["wip_lots"] for row in rows),
            }
        conn.commit()

        with conn.cursor() as cur:
            checks = {}
            for fab_id in FABS:
                cur.execute(
                    f"""
                    SELECT
                        (
                            SELECT count(*)
                            FROM {fab_id}.live_process_snapshots
                            WHERE interval_end = %s
                        ) AS snapshot_rows,
                        (
                            SELECT count(*)
                            FROM {fab_id}.live_process_events
                            WHERE interval_end = %s
                        ) AS event_rows
                    """,
                    (interval_end, interval_end),
                )
                verified = cur.fetchone()
                checks[fab_id] = {
                    "snapshot_rows": verified["snapshot_rows"],
                    "event_rows": verified["event_rows"],
                    "rows": verified["snapshot_rows"] + verified["event_rows"],
                }

    print(
        json.dumps(
            {
                "interval_start": interval_start.isoformat(),
                "interval_end": interval_end.isoformat(),
                "summary": summary,
                "verified_rows": checks,
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
