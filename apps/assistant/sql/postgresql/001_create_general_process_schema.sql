-- General process data schema for PostgreSQL.
-- Run this script first in DBeaver, then run 002_seed_general_process_data.sql.

CREATE SCHEMA IF NOT EXISTS fab;

SET search_path TO fab, public;

CREATE TABLE IF NOT EXISTS fab_site (
    site_id TEXT PRIMARY KEY,
    site_name TEXT NOT NULL,
    region TEXT NOT NULL,
    timezone TEXT NOT NULL DEFAULT 'Asia/Seoul'
);

CREATE TABLE IF NOT EXISTS fab_area (
    area_id TEXT PRIMARY KEY,
    site_id TEXT NOT NULL REFERENCES fab_site(site_id),
    area_name TEXT NOT NULL,
    area_type TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS toolgroup (
    toolgroup_id TEXT PRIMARY KEY,
    area_id TEXT NOT NULL REFERENCES fab_area(area_id),
    toolgroup_name TEXT NOT NULL,
    process_stage TEXT NOT NULL,
    standard_cycle_minutes INTEGER NOT NULL CHECK (standard_cycle_minutes > 0)
);

CREATE TABLE IF NOT EXISTS equipment (
    equipment_id TEXT PRIMARY KEY,
    toolgroup_id TEXT NOT NULL REFERENCES toolgroup(toolgroup_id),
    equipment_name TEXT NOT NULL,
    model_name TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('AVAILABLE', 'RUNNING', 'PM', 'DOWN', 'HOLD')),
    installed_date DATE NOT NULL
);

CREATE TABLE IF NOT EXISTS product (
    product_id TEXT PRIMARY KEY,
    product_name TEXT NOT NULL,
    product_family TEXT NOT NULL,
    technology_node_nm INTEGER NOT NULL CHECK (technology_node_nm > 0)
);

CREATE TABLE IF NOT EXISTS process_route (
    route_id TEXT PRIMARY KEY,
    product_id TEXT NOT NULL REFERENCES product(product_id),
    route_name TEXT NOT NULL,
    revision TEXT NOT NULL,
    active_flag BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS process_route_step (
    route_step_id TEXT PRIMARY KEY,
    route_id TEXT NOT NULL REFERENCES process_route(route_id) ON DELETE CASCADE,
    step_no INTEGER NOT NULL CHECK (step_no > 0),
    operation_name TEXT NOT NULL,
    toolgroup_id TEXT NOT NULL REFERENCES toolgroup(toolgroup_id),
    queue_limit_hours NUMERIC(6,2) NOT NULL CHECK (queue_limit_hours > 0),
    takt_minutes INTEGER NOT NULL CHECK (takt_minutes > 0),
    UNIQUE (route_id, step_no)
);

CREATE TABLE IF NOT EXISTS lot (
    lot_id TEXT PRIMARY KEY,
    product_id TEXT NOT NULL REFERENCES product(product_id),
    route_id TEXT NOT NULL REFERENCES process_route(route_id),
    site_id TEXT NOT NULL REFERENCES fab_site(site_id),
    current_step_no INTEGER NOT NULL CHECK (current_step_no > 0),
    status TEXT NOT NULL CHECK (status IN ('RELEASED', 'WAITING', 'RUNNING', 'HOLD', 'DONE')),
    priority INTEGER NOT NULL CHECK (priority BETWEEN 1 AND 10),
    release_ts TIMESTAMPTZ NOT NULL,
    due_ts TIMESTAMPTZ NOT NULL,
    wafer_qty INTEGER NOT NULL CHECK (wafer_qty > 0),
    customer_name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS lot_operation_log (
    op_id TEXT PRIMARY KEY,
    lot_id TEXT NOT NULL REFERENCES lot(lot_id) ON DELETE CASCADE,
    route_step_id TEXT NOT NULL REFERENCES process_route_step(route_step_id),
    equipment_id TEXT REFERENCES equipment(equipment_id),
    queue_start_ts TIMESTAMPTZ NOT NULL,
    start_ts TIMESTAMPTZ,
    end_ts TIMESTAMPTZ,
    queue_minutes INTEGER NOT NULL CHECK (queue_minutes >= 0),
    cycle_minutes INTEGER CHECK (cycle_minutes IS NULL OR cycle_minutes >= 0),
    result_status TEXT NOT NULL CHECK (result_status IN ('PASS', 'FAIL', 'REWORK', 'HOLD')),
    operator_name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_yield (
    yield_id TEXT PRIMARY KEY,
    site_id TEXT NOT NULL REFERENCES fab_site(site_id),
    product_id TEXT NOT NULL REFERENCES product(product_id),
    work_date DATE NOT NULL,
    input_qty INTEGER NOT NULL CHECK (input_qty > 0),
    good_qty INTEGER NOT NULL CHECK (good_qty >= 0),
    scrap_qty INTEGER NOT NULL CHECK (scrap_qty >= 0),
    rework_qty INTEGER NOT NULL CHECK (rework_qty >= 0),
    yield_rate NUMERIC(6,4) NOT NULL CHECK (yield_rate BETWEEN 0 AND 1),
    UNIQUE (site_id, product_id, work_date)
);

CREATE INDEX IF NOT EXISTS idx_lot_status_site ON lot (site_id, status);
CREATE INDEX IF NOT EXISTS idx_lot_due_ts ON lot (due_ts);
CREATE INDEX IF NOT EXISTS idx_lot_op_lot_id ON lot_operation_log (lot_id);
CREATE INDEX IF NOT EXISTS idx_lot_op_step_id ON lot_operation_log (route_step_id);
CREATE INDEX IF NOT EXISTS idx_yield_work_date ON daily_yield (work_date);

CREATE OR REPLACE VIEW v_current_wip AS
SELECT
    l.lot_id,
    l.site_id,
    s.site_name,
    p.product_name,
    pr.route_name,
    l.current_step_no,
    step.operation_name AS current_operation,
    step.toolgroup_id,
    tg.toolgroup_name,
    tg.process_stage,
    l.status,
    l.priority,
    l.release_ts,
    l.due_ts,
    l.wafer_qty,
    l.customer_name
FROM lot l
JOIN fab_site s ON s.site_id = l.site_id
JOIN product p ON p.product_id = l.product_id
JOIN process_route pr ON pr.route_id = l.route_id
JOIN process_route_step step
    ON step.route_id = l.route_id
   AND step.step_no = l.current_step_no
JOIN toolgroup tg ON tg.toolgroup_id = step.toolgroup_id;

CREATE OR REPLACE VIEW v_queue_time_by_toolgroup AS
SELECT
    tg.toolgroup_id,
    tg.toolgroup_name,
    tg.process_stage,
    COUNT(*) AS operation_count,
    ROUND(AVG(op.queue_minutes)::numeric, 2) AS avg_queue_minutes,
    MAX(op.queue_minutes) AS max_queue_minutes
FROM lot_operation_log op
JOIN process_route_step step ON step.route_step_id = op.route_step_id
JOIN toolgroup tg ON tg.toolgroup_id = step.toolgroup_id
GROUP BY tg.toolgroup_id, tg.toolgroup_name, tg.process_stage;

CREATE OR REPLACE VIEW v_daily_yield_summary AS
SELECT
    dy.work_date,
    s.site_name,
    p.product_name,
    dy.input_qty,
    dy.good_qty,
    dy.scrap_qty,
    dy.rework_qty,
    dy.yield_rate
FROM daily_yield dy
JOIN fab_site s ON s.site_id = dy.site_id
JOIN product p ON p.product_id = dy.product_id;
