from __future__ import annotations

import importlib
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

sim = importlib.import_module("simulate_fab_raw_events")


RAW_COLUMNS = {
    "event_id",
    "fab_id",
    "event_time",
    "event_end_time",
    "interval_id",
    "source_type",
    "actor_role",
    "area",
    "toolgroup",
    "equipment_id",
    "chamber_id",
    "lot_id",
    "product_id",
    "route_id",
    "step_id",
    "event_type",
    "event_status",
    "severity",
    "wafers",
    "queue_minutes",
    "process_minutes",
    "hold_minutes",
    "down_minutes",
    "pm_minutes",
    "rework_flag",
    "defect_count",
    "yield_loss_ppm",
    "wip_delta",
    "narrative",
    "created_at",
}


def test_floor_time_uses_twenty_minute_bucket():
    run_at = datetime(2026, 9, 9, 10, 46, 33, tzinfo=UTC)
    assert sim.floor_time(run_at, 20) == datetime(2026, 9, 9, 10, 40, tzinfo=UTC)


def test_raw_events_match_contract_and_have_event_timestamps_inside_interval():
    interval_start = datetime(2026, 9, 9, 10, 20, tzinfo=UTC)
    interval_end = interval_start + timedelta(minutes=20)

    rows, state, incident = sim.build_raw_events("fab13", interval_start, interval_end)

    assert rows
    assert set(rows[0]) == RAW_COLUMNS
    assert state["fab_id"] == "fab13"
    assert state["personality"] == "high_load_expansion"
    assert state["last_event_time"] == interval_end
    assert incident is None or incident["fab_id"] == "fab13"
    assert {row["fab_id"] for row in rows} == {"fab13"}
    assert {row["area"] for row in rows} == set(sim.AREAS)
    assert {row["source_type"] for row in rows} <= set(sim.SOURCE_TYPES)
    assert {row["actor_role"] for row in rows} <= set(sim.ACTOR_ROLES)
    assert all(interval_start <= row["event_time"] < interval_end for row in rows)
    assert all(row["event_end_time"] <= interval_end for row in rows)
    assert all(row["created_at"] == interval_end for row in rows)


def test_active_incident_continues_and_drives_area_events():
    interval_start = datetime(2026, 9, 9, 10, 20, tzinfo=UTC)
    interval_end = interval_start + timedelta(minutes=20)
    incident = {
        "incident_id": "fab13-bottleneck_alarm-202609091015",
        "fab_id": "fab13",
        "area": "etch",
        "event_type": "bottleneck_alarm",
        "severity": "high",
        "status": "active",
        "started_at": interval_start - timedelta(minutes=5),
        "expected_end_at": interval_end + timedelta(minutes=30),
        "recovered_at": None,
        "recovery_progress": 0,
        "narrative": "existing incident",
    }

    rows, state, next_incident = sim.build_raw_events(
        "fab13", interval_start, interval_end, prior_incident=incident
    )

    assert next_incident is not None
    assert next_incident["incident_id"] == incident["incident_id"]
    assert state["active_incident_id"] == incident["incident_id"]
    assert state["bottleneck_area"] == "etch"
    etch_rows = [row for row in rows if row["area"] == "etch"]
    assert any(row["event_type"] == "bottleneck_alarm" for row in etch_rows)
    assert any(row["severity"] == "high" for row in etch_rows)


def test_incident_lifecycle_recovers_and_stops_driving_raw_events():
    interval_start = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)
    interval_end = interval_start + timedelta(minutes=20)
    incident = {
        "incident_id": "fab11-equipment_down-202609091000",
        "fab_id": "fab11",
        "area": "cmp",
        "event_type": "equipment_down",
        "severity": "high",
        "status": "recovering",
        "started_at": interval_start - timedelta(hours=2),
        "expected_end_at": interval_start - timedelta(minutes=45),
        "recovered_at": None,
        "recovery_progress": 0,
        "narrative": "existing incident",
    }

    rows, state, next_incident = sim.build_raw_events(
        "fab11", interval_start, interval_end, prior_incident=incident
    )

    assert next_incident is not None
    assert next_incident["status"] == "recovered"
    assert state["active_incident_id"] is None
    assert state["bottleneck_area"] is None
    assert not any(row["event_type"] == "equipment_down" for row in rows)
