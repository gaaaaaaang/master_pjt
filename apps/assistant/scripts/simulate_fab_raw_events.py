from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from app.db.fab_catalog import ALLOWED_FABS

FABS = ("fab10", "fab11", "fab12", "fab13")
AREAS = ("photo", "etch", "deposition", "implant", "cmp", "metrology")
SOURCE_TYPES = ("simulator", "operator_report", "equipment_signal", "engineer_note", "scheduler_decision")
ACTOR_ROLES = ("operator", "engineer", "scheduler", "equipment")
INTERVAL_MINUTES = 20


@dataclass(frozen=True)
class FabProfile:
    base_wip: int
    base_yield: float
    base_defect: int
    base_process_minutes: int
    volatility: float
    incident_rate: float
    personality: str


@dataclass(frozen=True)
class IncidentSpec:
    event_type: str
    source_type: str
    actor_role: str
    default_area: str
    severity_weights: tuple[int, int, int]
    duration_minutes: tuple[int, int]
    wip_delta: tuple[int, int]
    yield_loss_ppm: tuple[int, int]


PROFILES = {
    "fab10": FabProfile(1260, 97.3, 410, 42, 0.45, 0.10, "stable_hvlm"),
    "fab11": FabProfile(920, 95.8, 680, 47, 0.80, 0.22, "aging_lvhm"),
    "fab12": FabProfile(1110, 96.2, 590, 51, 1.05, 0.25, "new_process_mix"),
    "fab13": FabProfile(1460, 96.7, 530, 45, 1.20, 0.32, "high_load_expansion"),
}

INCIDENTS = {
    "equipment_down": IncidentSpec(
        "equipment_down", "equipment_signal", "equipment", "etch", (15, 55, 30), (40, 110), (45, 140), (120, 420)
    ),
    "pm_overrun": IncidentSpec(
        "pm_overrun", "operator_report", "operator", "cmp", (55, 35, 10), (30, 80), (20, 75), (40, 160)
    ),
    "yield_dip": IncidentSpec(
        "yield_dip", "engineer_note", "engineer", "metrology", (25, 50, 25), (40, 120), (25, 90), (180, 620)
    ),
    "bottleneck_alarm": IncidentSpec(
        "bottleneck_alarm", "scheduler_decision", "scheduler", "photo", (20, 55, 25), (50, 160), (80, 220), (70, 240)
    ),
    "recipe_tuning": IncidentSpec(
        "recipe_tuning", "engineer_note", "engineer", "deposition", (65, 30, 5), (20, 70), (10, 55), (30, 180)
    ),
}

RAW_EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS {schema}.fab_process_raw_events_{schema} (
    raw_event_row_id BIGSERIAL PRIMARY KEY,
    event_id TEXT NOT NULL UNIQUE,
    fab_id TEXT NOT NULL,
    event_time TIMESTAMPTZ NOT NULL,
    event_end_time TIMESTAMPTZ,
    interval_id TEXT NOT NULL,
    source_type TEXT NOT NULL,
    actor_role TEXT NOT NULL,
    area TEXT NOT NULL,
    toolgroup TEXT NOT NULL,
    equipment_id TEXT,
    chamber_id TEXT,
    lot_id TEXT,
    product_id TEXT,
    route_id TEXT,
    step_id TEXT,
    event_type TEXT NOT NULL,
    event_status TEXT NOT NULL,
    severity TEXT NOT NULL,
    wafers INTEGER NOT NULL DEFAULT 0,
    queue_minutes NUMERIC(8,2) NOT NULL DEFAULT 0,
    process_minutes NUMERIC(8,2) NOT NULL DEFAULT 0,
    hold_minutes NUMERIC(8,2) NOT NULL DEFAULT 0,
    down_minutes NUMERIC(8,2) NOT NULL DEFAULT 0,
    pm_minutes NUMERIC(8,2) NOT NULL DEFAULT 0,
    rework_flag BOOLEAN NOT NULL DEFAULT false,
    defect_count INTEGER NOT NULL DEFAULT 0,
    yield_loss_ppm INTEGER NOT NULL DEFAULT 0,
    wip_delta INTEGER NOT NULL DEFAULT 0,
    narrative TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

STATE_DDL = """
CREATE TABLE IF NOT EXISTS {schema}.fab_simulation_state_{schema} (
    fab_id TEXT PRIMARY KEY,
    personality TEXT NOT NULL,
    health_score NUMERIC(6,3) NOT NULL,
    wip_level INTEGER NOT NULL,
    yield_trend NUMERIC(7,3) NOT NULL,
    active_incident_id TEXT,
    bottleneck_area TEXT,
    recovery_progress NUMERIC(6,3) NOT NULL DEFAULT 0,
    last_event_time TIMESTAMPTZ,
    state_json JSONB NOT NULL DEFAULT '{{}}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

INCIDENT_DDL = """
CREATE TABLE IF NOT EXISTS {schema}.fab_incidents_{schema} (
    incident_id TEXT PRIMARY KEY,
    fab_id TEXT NOT NULL,
    area TEXT NOT NULL,
    event_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    expected_end_at TIMESTAMPTZ NOT NULL,
    recovered_at TIMESTAMPTZ,
    recovery_progress NUMERIC(6,3) NOT NULL DEFAULT 0,
    narrative TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate stateful FAB raw process events.")
    parser.add_argument("--dsn", default=os.getenv("POSTGRES_DSN"))
    parser.add_argument("--run-at", help="ISO timestamp. Defaults to now UTC.")
    parser.add_argument("--interval-minutes", type=int, default=INTERVAL_MINUTES)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def parse_run_at(value: str | None) -> datetime:
    if not value:
        return datetime.now(UTC)
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def floor_time(value: datetime, interval_minutes: int) -> datetime:
    minute = value.minute - (value.minute % interval_minutes)
    return value.replace(minute=minute, second=0, microsecond=0)


def interval_key(fab_id: str, interval_start: datetime, interval_end: datetime) -> str:
    return f"{fab_id}:{interval_start.isoformat()}:{interval_end.isoformat()}"


def seed_for(*parts: object) -> int:
    key = ":".join(str(part) for part in parts)
    return int(hashlib.sha256(key.encode()).hexdigest()[:16], 16)


def table_name(fab_id: str, logical: str) -> str:
    if fab_id not in ALLOWED_FABS:
        raise ValueError("Unsupported FAB")
    return f"{logical}_{fab_id}"


def create_tables(conn: psycopg.Connection[Any], fab_id: str) -> None:
    if fab_id not in ALLOWED_FABS:
        raise ValueError("Unsupported FAB")
    with conn.cursor() as cur:
        cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{fab_id}"')
        cur.execute(RAW_EVENTS_DDL.format(schema=fab_id))
        cur.execute(STATE_DDL.format(schema=fab_id))
        cur.execute(INCIDENT_DDL.format(schema=fab_id))


def load_state(conn: psycopg.Connection[Any], fab_id: str) -> dict[str, Any]:
    profile = PROFILES[fab_id]
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT *
            FROM {fab_id}.{table_name(fab_id, "fab_simulation_state")}
            WHERE fab_id = %s
            """,
            (fab_id,),
        )
        row = cur.fetchone()
    if row:
        return dict(row)
    return {
        "fab_id": fab_id,
        "personality": profile.personality,
        "health_score": Decimal("0.920"),
        "wip_level": profile.base_wip,
        "yield_trend": Decimal(f"{profile.base_yield:.3f}"),
        "active_incident_id": None,
        "bottleneck_area": None,
        "recovery_progress": Decimal(0),
        "last_event_time": None,
        "state_json": {},
    }


def active_incident(conn: psycopg.Connection[Any], fab_id: str) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT *
            FROM {fab_id}.{table_name(fab_id, "fab_incidents")}
            WHERE status IN ('active', 'recovering')
            ORDER BY started_at DESC
            LIMIT 1
            """
        )
        row = cur.fetchone()
    return dict(row) if row else None


def choose_new_incident(fab_id: str, rng: random.Random, state: dict[str, Any]) -> dict[str, Any] | None:
    profile = PROFILES[fab_id]
    health_penalty = max(0.0, 0.92 - float(state["health_score"]))
    if rng.random() > profile.incident_rate + health_penalty:
        return None
    event_type = rng.choice(tuple(INCIDENTS))
    spec = INCIDENTS[event_type]
    return {
        "event_type": event_type,
        "source_type": spec.source_type,
        "actor_role": spec.actor_role,
        "area": rng.choice(AREAS) if rng.random() < 0.35 else spec.default_area,
        "severity": rng.choices(("low", "medium", "high"), weights=spec.severity_weights, k=1)[0],
        "duration_minutes": rng.randint(*spec.duration_minutes),
        "wip_delta": rng.randint(*spec.wip_delta),
        "yield_loss_ppm": rng.randint(*spec.yield_loss_ppm),
    }


def incident_id(fab_id: str, event_type: str, event_time: datetime) -> str:
    stamp = event_time.strftime("%Y%m%d%H%M")
    return f"{fab_id}-{event_type}-{stamp}".lower()


def resolve_incident(
    fab_id: str,
    interval_start: datetime,
    interval_end: datetime,
    rng: random.Random,
    state: dict[str, Any],
    prior_incident: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if prior_incident:
        expected_end = prior_incident["expected_end_at"]
        if expected_end.tzinfo is None:
            expected_end = expected_end.replace(tzinfo=UTC)
        recovery_end = expected_end + timedelta(minutes=40)
        if interval_start >= recovery_end:
            return {
                **prior_incident,
                "status": "recovered",
                "severity": "normal",
                "recovered_at": recovery_end,
                "recovery_progress": Decimal("1.000"),
            }
        if interval_start >= expected_end:
            elapsed = (interval_start - expected_end).total_seconds()
            progress = Decimal(f"{min(0.950, 0.250 + elapsed / (40 * 60) * 0.700):.3f}")
            return {
                **prior_incident,
                "status": "recovering",
                "severity": "low",
                "recovery_progress": progress,
            }
        return {**prior_incident, "status": "active"}

    new_incident = choose_new_incident(fab_id, rng, state)
    if not new_incident:
        return None
    event_time = random_time(interval_start, interval_end, rng)
    return {
        "incident_id": incident_id(fab_id, new_incident["event_type"], event_time),
        "fab_id": fab_id,
        "area": new_incident["area"],
        "event_type": new_incident["event_type"],
        "severity": new_incident["severity"],
        "status": "active",
        "started_at": event_time,
        "expected_end_at": event_time + timedelta(minutes=new_incident["duration_minutes"]),
        "recovered_at": None,
        "recovery_progress": Decimal(0),
        "narrative": f"{fab_id} {new_incident['area']} {new_incident['event_type']} generated by simulated field actor.",
        "source_type": new_incident["source_type"],
        "actor_role": new_incident["actor_role"],
        "wip_delta": new_incident["wip_delta"],
        "yield_loss_ppm": new_incident["yield_loss_ppm"],
    }


def random_time(start: datetime, end: datetime, rng: random.Random) -> datetime:
    seconds = max(1, int((end - start).total_seconds()))
    return start + timedelta(seconds=rng.randint(0, seconds - 1))


def event_id(fab_id: str, interval: str, area: str, kind: str, index: int) -> str:
    digest = hashlib.sha1(f"{fab_id}:{interval}:{area}:{kind}:{index}".encode()).hexdigest()[:10]
    return f"{fab_id}-{area}-{kind}-{digest}".lower()


def toolgroup(fab_id: str, area: str) -> str:
    return f"{fab_id.upper()}_{area.upper()}_TG01"


def equipment_id(fab_id: str, area: str, index: int) -> str:
    return f"{toolgroup(fab_id, area)}_EQ{index % 8 + 1:02d}"


def lot_id(fab_id: str, area: str, event_time: datetime, index: int) -> str:
    return f"{fab_id.upper()}-{area[:2].upper()}-{event_time:%m%d%H%M}-{index:03d}"


def severity_factor(severity: str) -> float:
    return {"normal": 0.0, "low": 0.45, "medium": 0.9, "high": 1.55}[severity]


def raw_event(
    *,
    fab_id: str,
    interval: str,
    interval_start: datetime,
    interval_end: datetime,
    area: str,
    index: int,
    event_type: str,
    event_status: str,
    source_type: str,
    actor_role: str,
    severity: str,
    rng: random.Random,
    incident: dict[str, Any] | None,
) -> dict[str, Any]:
    start = random_time(interval_start, interval_end, rng)
    process_minutes = Decimal(f"{rng.uniform(8, 58):.2f}")
    queue_base = rng.uniform(1, 28)
    factor = severity_factor(severity)
    queue_minutes = Decimal(f"{queue_base + factor * rng.uniform(12, 95):.2f}")
    down_minutes = Decimal(0)
    pm_minutes = Decimal(0)
    hold_minutes = Decimal(0)
    rework = False
    defect_count = max(0, int(rng.gauss(1 + factor * 2, 1.5)))
    yield_loss = max(0, int(rng.gauss(20 + factor * 130, 30)))
    wip_delta = rng.randint(-2, 4) + int(factor * rng.randint(3, 14))
    if event_type == "equipment_down":
        down_minutes = Decimal(f"{min(20, rng.uniform(4, 20)):.2f}")
        wip_delta += int(12 * max(1, factor))
    elif event_type == "pm_overrun":
        pm_minutes = Decimal(f"{min(20, rng.uniform(5, 20)):.2f}")
    elif event_type in {"hold_start", "hold_release"}:
        hold_minutes = Decimal(f"{rng.uniform(15, 120):.2f}")
    elif event_type == "rework_route":
        rework = True
        defect_count += rng.randint(1, 6)
        yield_loss += rng.randint(120, 360)

    event_end = start + timedelta(minutes=float(process_minutes))
    event_end = min(event_end, interval_end)
    row_lot_id = None if actor_role == "equipment" else lot_id(fab_id, area, start, index)
    row_equipment_id = equipment_id(fab_id, area, index)
    status_text = event_status
    if incident and incident.get("event_type") == event_type:
        status_text = str(incident.get("status", event_status))
    return {
        "event_id": event_id(fab_id, interval, area, event_type, index),
        "fab_id": fab_id,
        "event_time": start,
        "event_end_time": event_end,
        "interval_id": interval,
        "source_type": source_type,
        "actor_role": actor_role,
        "area": area,
        "toolgroup": toolgroup(fab_id, area),
        "equipment_id": row_equipment_id,
        "chamber_id": f"{row_equipment_id}_CH{index % 4 + 1:02d}",
        "lot_id": row_lot_id,
        "product_id": None if row_lot_id is None else f"PROD-{index % 8 + 1:02d}",
        "route_id": None if row_lot_id is None else f"ROUTE-{area.upper()}-{index % 3 + 1}",
        "step_id": f"{area.upper()}_STEP_{index % 12 + 1:02d}",
        "event_type": event_type,
        "event_status": status_text,
        "severity": severity,
        "wafers": 0 if row_lot_id is None else rng.choice((24, 25)),
        "queue_minutes": queue_minutes,
        "process_minutes": process_minutes,
        "hold_minutes": hold_minutes,
        "down_minutes": down_minutes,
        "pm_minutes": pm_minutes,
        "rework_flag": rework,
        "defect_count": defect_count,
        "yield_loss_ppm": yield_loss,
        "wip_delta": wip_delta,
        "narrative": narrative(fab_id, area, event_type, severity, source_type, actor_role),
        "created_at": interval_end,
    }


def narrative(
    fab_id: str,
    area: str,
    event_type: str,
    severity: str,
    source_type: str,
    actor_role: str,
) -> str:
    return f"{fab_id} {area} {actor_role} {source_type} reported {severity} {event_type}."


def build_raw_events(
    fab_id: str,
    interval_start: datetime,
    interval_end: datetime,
    state: dict[str, Any] | None = None,
    prior_incident: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any] | None]:
    profile = PROFILES[fab_id]
    state = state or {
        "fab_id": fab_id,
        "personality": profile.personality,
        "health_score": Decimal("0.920"),
        "wip_level": profile.base_wip,
        "yield_trend": Decimal(f"{profile.base_yield:.3f}"),
        "active_incident_id": None,
        "bottleneck_area": None,
        "recovery_progress": Decimal(0),
        "state_json": {},
    }
    interval = interval_key(fab_id, interval_start, interval_end)
    rng = random.Random(seed_for(interval, state.get("active_incident_id") or "none"))
    incident = resolve_incident(fab_id, interval_start, interval_end, rng, state, prior_incident)
    rows: list[dict[str, Any]] = []

    for area in AREAS:
        area_incident = (
            incident
            if incident and incident["status"] != "recovered" and incident["area"] == area
            else None
        )
        severity = area_incident["severity"] if area_incident else "normal"
        baseline_types = ("queue_enter", "process_start", "process_end", "equipment_state", "metrology_check")
        for idx, event_type in enumerate(baseline_types, start=1):
            rows.append(
                raw_event(
                    fab_id=fab_id,
                    interval=interval,
                    interval_start=interval_start,
                    interval_end=interval_end,
                    area=area,
                    index=idx,
                    event_type=event_type,
                    event_status="completed" if event_type.endswith("end") else "active",
                    source_type="equipment_signal" if "equipment" in event_type else "simulator",
                    actor_role="equipment" if "equipment" in event_type else "scheduler",
                    severity=severity,
                    rng=rng,
                    incident=area_incident,
                )
            )
        if area_incident:
            event_type = area_incident["event_type"]
            spec = INCIDENTS[event_type]
            rows.append(
                raw_event(
                    fab_id=fab_id,
                    interval=interval,
                    interval_start=interval_start,
                    interval_end=interval_end,
                    area=area,
                    index=90,
                    event_type=event_type,
                    event_status=str(area_incident["status"]),
                    source_type=str(area_incident.get("source_type") or spec.source_type),
                    actor_role=str(area_incident.get("actor_role") or spec.actor_role),
                    severity=str(area_incident["severity"]),
                    rng=rng,
                    incident=area_incident,
                )
            )
        for idx in range(6, 16):
            event_type = rng.choices(
                ("queue_enter", "process_start", "process_end", "hold_start", "hold_release", "rework_route"),
                weights=(22, 24, 31, 7, 8, 8 if severity != "normal" else 2),
                k=1,
            )[0]
            rows.append(
                raw_event(
                    fab_id=fab_id,
                    interval=interval,
                    interval_start=interval_start,
                    interval_end=interval_end,
                    area=area,
                    index=idx,
                    event_type=event_type,
                    event_status="completed" if event_type in {"process_end", "hold_release"} else "started",
                    source_type=rng.choice(SOURCE_TYPES),
                    actor_role=rng.choice(ACTOR_ROLES),
                    severity=severity,
                    rng=rng,
                    incident=area_incident,
                )
            )

    total_wip_delta = sum(int(row["wip_delta"]) for row in rows)
    total_yield_loss = sum(int(row["yield_loss_ppm"]) for row in rows)
    recovery_progress = Decimal(0)
    active_incident_id = None
    bottleneck_area = None
    if incident and incident["status"] != "recovered":
        active_incident_id = incident["incident_id"]
        bottleneck_area = incident["area"]
        if incident["status"] == "recovering":
            recovery_progress = Decimal(str(incident.get("recovery_progress", "0.450")))
    next_state = {
        "fab_id": fab_id,
        "personality": profile.personality,
        "health_score": Decimal(f"{max(0.45, min(0.99, float(state['health_score']) - total_yield_loss / 250000 + 0.01)):.3f}"),
        "wip_level": max(0, int(state["wip_level"]) + total_wip_delta),
        "yield_trend": Decimal(f"{max(80.0, min(99.9, float(state['yield_trend']) - total_yield_loss / 100000)):.3f}"),
        "active_incident_id": active_incident_id,
        "bottleneck_area": bottleneck_area,
        "recovery_progress": recovery_progress,
        "last_event_time": interval_end,
        "state_json": {
            "last_interval_id": interval,
            "raw_events_generated": len(rows),
            "profile": profile.personality,
        },
    }
    return rows, next_state, incident


def insert_raw_events(conn: psycopg.Connection[Any], fab_id: str, rows: list[dict[str, Any]]) -> int:
    columns = tuple(rows[0])
    placeholders = ", ".join([f"%({column})s" for column in columns])
    assignments = ", ".join(
        f"{column} = EXCLUDED.{column}" for column in columns if column != "event_id"
    )
    sql = f"""
        INSERT INTO {fab_id}.{table_name(fab_id, "fab_process_raw_events")} ({", ".join(columns)})
        VALUES ({placeholders})
        ON CONFLICT (event_id) DO UPDATE SET {assignments}
    """
    with conn.cursor() as cur:
        cur.executemany(sql, rows)
    return len(rows)


def upsert_state(conn: psycopg.Connection[Any], fab_id: str, state: dict[str, Any]) -> None:
    payload = {**state, "state_json": Jsonb(state["state_json"])}
    with conn.cursor() as cur:
        cur.execute(
            f"""
            INSERT INTO {fab_id}.{table_name(fab_id, "fab_simulation_state")} (
                fab_id, personality, health_score, wip_level, yield_trend,
                active_incident_id, bottleneck_area, recovery_progress,
                last_event_time, state_json
            )
            VALUES (
                %(fab_id)s, %(personality)s, %(health_score)s, %(wip_level)s,
                %(yield_trend)s, %(active_incident_id)s, %(bottleneck_area)s,
                %(recovery_progress)s, %(last_event_time)s, %(state_json)s
            )
            ON CONFLICT (fab_id) DO UPDATE SET
                personality = EXCLUDED.personality,
                health_score = EXCLUDED.health_score,
                wip_level = EXCLUDED.wip_level,
                yield_trend = EXCLUDED.yield_trend,
                active_incident_id = EXCLUDED.active_incident_id,
                bottleneck_area = EXCLUDED.bottleneck_area,
                recovery_progress = EXCLUDED.recovery_progress,
                last_event_time = EXCLUDED.last_event_time,
                state_json = EXCLUDED.state_json,
                updated_at = now()
            """,
            payload,
        )


def upsert_incident(conn: psycopg.Connection[Any], fab_id: str, incident: dict[str, Any] | None) -> None:
    if not incident:
        return
    with conn.cursor() as cur:
        cur.execute(
            f"""
            INSERT INTO {fab_id}.{table_name(fab_id, "fab_incidents")} (
                incident_id, fab_id, area, event_type, severity, status,
                started_at, expected_end_at, recovered_at, recovery_progress, narrative
            )
            VALUES (
                %(incident_id)s, %(fab_id)s, %(area)s, %(event_type)s, %(severity)s,
                %(status)s, %(started_at)s, %(expected_end_at)s, %(recovered_at)s,
                %(recovery_progress)s, %(narrative)s
            )
            ON CONFLICT (incident_id) DO UPDATE SET
                severity = EXCLUDED.severity,
                status = EXCLUDED.status,
                expected_end_at = EXCLUDED.expected_end_at,
                recovered_at = EXCLUDED.recovered_at,
                recovery_progress = EXCLUDED.recovery_progress,
                narrative = EXCLUDED.narrative,
                updated_at = now()
            """,
            incident,
        )


def run_simulation(
    *,
    dsn: str | None,
    run_at: datetime,
    interval_minutes: int,
    dry_run: bool,
) -> dict[str, Any]:
    interval_end = floor_time(run_at, interval_minutes)
    interval_start = interval_end - timedelta(minutes=interval_minutes)
    result: dict[str, Any] = {
        "interval_start": interval_start.isoformat(),
        "interval_end": interval_end.isoformat(),
        "dry_run": dry_run,
        "summary": {},
    }
    if dry_run:
        for fab_id in FABS:
            rows, state, incident = build_raw_events(fab_id, interval_start, interval_end)
            result["summary"][fab_id] = summary_for(rows, state, incident)
        return result
    if not dsn:
        raise RuntimeError("POSTGRES_DSN is required unless --dry-run is used.")
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        for fab_id in FABS:
            create_tables(conn, fab_id)
            state = load_state(conn, fab_id)
            incident = active_incident(conn, fab_id)
            rows, next_state, next_incident = build_raw_events(
                fab_id, interval_start, interval_end, state, incident
            )
            inserted = insert_raw_events(conn, fab_id, rows)
            upsert_incident(conn, fab_id, next_incident)
            upsert_state(conn, fab_id, next_state)
            result["summary"][fab_id] = {
                **summary_for(rows, next_state, next_incident),
                "rows_inserted": inserted,
            }
        conn.commit()
    return result


def summary_for(
    rows: list[dict[str, Any]],
    state: dict[str, Any],
    incident: dict[str, Any] | None,
) -> dict[str, Any]:
    high_rows = [row for row in rows if row["severity"] == "high"]
    return {
        "raw_event_rows": len(rows),
        "active_incident": None if not incident or incident["status"] == "recovered" else {
            "incident_id": incident["incident_id"],
            "event_type": incident["event_type"],
            "area": incident["area"],
            "severity": incident["severity"],
            "status": incident["status"],
        },
        "high_severity_rows": len(high_rows),
        "wip_level": state["wip_level"],
        "yield_trend": str(state["yield_trend"]),
        "health_score": str(state["health_score"]),
    }


def main() -> None:
    args = parse_args()
    result = run_simulation(
        dsn=args.dsn,
        run_at=parse_run_at(args.run_at),
        interval_minutes=args.interval_minutes,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
