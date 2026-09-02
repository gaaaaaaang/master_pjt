-- Sample general process data for PostgreSQL.
-- Run this after 001_create_general_process_schema.sql.

SET search_path TO fab, public;

INSERT INTO fab_site (site_id, site_name, region, timezone) VALUES
    ('fab10', 'Fab 10', 'KR', 'Asia/Seoul'),
    ('fab12', 'Fab 12', 'KR', 'Asia/Seoul')
ON CONFLICT (site_id) DO NOTHING;

INSERT INTO fab_area (area_id, site_id, area_name, area_type) VALUES
    ('A-LITHO', 'fab10', 'Lithography Area', 'process'),
    ('A-ETCH', 'fab10', 'Etch Area', 'process'),
    ('A-DEPO', 'fab10', 'Deposition Area', 'process'),
    ('A-METRO', 'fab10', 'Metrology Area', 'inspection'),
    ('A-FINAL', 'fab10', 'Final Test Area', 'inspection')
ON CONFLICT (area_id) DO NOTHING;

INSERT INTO toolgroup (toolgroup_id, area_id, toolgroup_name, process_stage, standard_cycle_minutes) VALUES
    ('TG-LITHO-01', 'A-LITHO', 'Litho Track 01', 'lithography', 45),
    ('TG-ETCH-01', 'A-ETCH', 'Dry Etch 01', 'etch', 38),
    ('TG-DEPO-01', 'A-DEPO', 'PECVD 01', 'deposition', 50),
    ('TG-METRO-01', 'A-METRO', 'CD-SEM 01', 'metrology', 20),
    ('TG-FINAL-01', 'A-FINAL', 'Final Test 01', 'final_test', 30)
ON CONFLICT (toolgroup_id) DO NOTHING;

INSERT INTO equipment (equipment_id, toolgroup_id, equipment_name, model_name, status, installed_date) VALUES
    ('EQ-LTH-001', 'TG-LITHO-01', 'Litho Scanner 001', 'ASML-NXT', 'AVAILABLE', '2024-03-01'),
    ('EQ-LTH-002', 'TG-LITHO-01', 'Litho Scanner 002', 'ASML-NXT', 'RUNNING', '2024-05-12'),
    ('EQ-ETCH-001', 'TG-ETCH-01', 'Dry Etcher 001', 'Lam-4600', 'AVAILABLE', '2023-11-20'),
    ('EQ-DEPO-001', 'TG-DEPO-01', 'PECVD 001', 'AMAT-CVD', 'PM', '2024-07-15'),
    ('EQ-MET-001', 'TG-METRO-01', 'CD-SEM 001', 'Hitachi-CD', 'AVAILABLE', '2024-01-18'),
    ('EQ-FIN-001', 'TG-FINAL-01', 'Final Tester 001', 'Teradyne-J750', 'AVAILABLE', '2024-04-22')
ON CONFLICT (equipment_id) DO NOTHING;

INSERT INTO product (product_id, product_name, product_family, technology_node_nm) VALUES
    ('PR-DRAM-1A', 'DRAM 1A', 'memory', 14),
    ('PR-NAND-1B', 'NAND 1B', 'memory', 10),
    ('PR-LOGIC-7N', 'Logic 7nm', 'logic', 7)
ON CONFLICT (product_id) DO NOTHING;

INSERT INTO process_route (route_id, product_id, route_name, revision, active_flag) VALUES
    ('RT-DRAM-A', 'PR-DRAM-1A', 'DRAM Main Flow', 'A', TRUE),
    ('RT-NAND-B', 'PR-NAND-1B', 'NAND Main Flow', 'B', TRUE),
    ('RT-LOGIC-C', 'PR-LOGIC-7N', 'Logic Main Flow', 'C', TRUE)
ON CONFLICT (route_id) DO NOTHING;

INSERT INTO process_route_step (
    route_step_id,
    route_id,
    step_no,
    operation_name,
    toolgroup_id,
    queue_limit_hours,
    takt_minutes
) VALUES
    ('RST-DRAM-010', 'RT-DRAM-A', 10, 'Photo', 'TG-LITHO-01', 12.00, 45),
    ('RST-DRAM-020', 'RT-DRAM-A', 20, 'Etch', 'TG-ETCH-01', 8.00, 38),
    ('RST-DRAM-030', 'RT-DRAM-A', 30, 'Depo', 'TG-DEPO-01', 10.00, 50),
    ('RST-DRAM-040', 'RT-DRAM-A', 40, 'Metrology', 'TG-METRO-01', 4.00, 20),
    ('RST-DRAM-050', 'RT-DRAM-A', 50, 'Final Test', 'TG-FINAL-01', 6.00, 30),
    ('RST-NAND-010', 'RT-NAND-B', 10, 'Photo', 'TG-LITHO-01', 10.00, 45),
    ('RST-NAND-020', 'RT-NAND-B', 20, 'Etch', 'TG-ETCH-01', 6.00, 38),
    ('RST-NAND-030', 'RT-NAND-B', 30, 'Depo', 'TG-DEPO-01', 8.00, 50),
    ('RST-NAND-040', 'RT-NAND-B', 40, 'Metrology', 'TG-METRO-01', 3.00, 20),
    ('RST-NAND-050', 'RT-NAND-B', 50, 'Final Test', 'TG-FINAL-01', 5.00, 30),
    ('RST-LOGIC-010', 'RT-LOGIC-C', 10, 'Photo', 'TG-LITHO-01', 9.00, 45),
    ('RST-LOGIC-020', 'RT-LOGIC-C', 20, 'Etch', 'TG-ETCH-01', 7.00, 38),
    ('RST-LOGIC-030', 'RT-LOGIC-C', 30, 'Depo', 'TG-DEPO-01', 9.00, 50),
    ('RST-LOGIC-040', 'RT-LOGIC-C', 40, 'Metrology', 'TG-METRO-01', 3.00, 20),
    ('RST-LOGIC-050', 'RT-LOGIC-C', 50, 'Final Test', 'TG-FINAL-01', 4.00, 30)
ON CONFLICT (route_step_id) DO NOTHING;

INSERT INTO lot (
    lot_id,
    product_id,
    route_id,
    site_id,
    current_step_no,
    status,
    priority,
    release_ts,
    due_ts,
    wafer_qty,
    customer_name
) VALUES
    ('LOT-260829-001', 'PR-DRAM-1A', 'RT-DRAM-A', 'fab10', 20, 'RUNNING', 8, '2026-08-26 08:00:00+09', '2026-08-31 18:00:00+09', 25, 'Alpha Memory'),
    ('LOT-260829-002', 'PR-DRAM-1A', 'RT-DRAM-A', 'fab10', 30, 'WAITING', 6, '2026-08-25 09:30:00+09', '2026-08-30 18:00:00+09', 25, 'Alpha Memory'),
    ('LOT-260829-003', 'PR-NAND-1B', 'RT-NAND-B', 'fab10', 10, 'RELEASED', 9, '2026-08-28 07:45:00+09', '2026-09-01 18:00:00+09', 25, 'Blue Storage'),
    ('LOT-260829-004', 'PR-NAND-1B', 'RT-NAND-B', 'fab10', 40, 'HOLD', 10, '2026-08-24 10:15:00+09', '2026-08-29 18:00:00+09', 25, 'Blue Storage'),
    ('LOT-260829-005', 'PR-LOGIC-7N', 'RT-LOGIC-C', 'fab12', 20, 'RUNNING', 7, '2026-08-27 11:20:00+09', '2026-08-30 12:00:00+09', 24, 'Nova Systems'),
    ('LOT-260829-006', 'PR-LOGIC-7N', 'RT-LOGIC-C', 'fab12', 50, 'DONE', 4, '2026-08-20 08:00:00+09', '2026-08-28 18:00:00+09', 24, 'Nova Systems')
ON CONFLICT (lot_id) DO NOTHING;

INSERT INTO lot_operation_log (
    op_id,
    lot_id,
    route_step_id,
    equipment_id,
    queue_start_ts,
    start_ts,
    end_ts,
    queue_minutes,
    cycle_minutes,
    result_status,
    operator_name
) VALUES
    ('OP-001', 'LOT-260829-001', 'RST-DRAM-010', 'EQ-LTH-002', '2026-08-26 08:00:00+09', '2026-08-26 10:00:00+09', '2026-08-26 10:45:00+09', 120, 45, 'PASS', 'Kim'),
    ('OP-002', 'LOT-260829-001', 'RST-DRAM-020', 'EQ-ETCH-001', '2026-08-26 11:00:00+09', '2026-08-26 13:10:00+09', '2026-08-26 13:48:00+09', 130, 38, 'PASS', 'Kim'),
    ('OP-003', 'LOT-260829-002', 'RST-DRAM-010', 'EQ-LTH-001', '2026-08-25 09:30:00+09', '2026-08-25 14:00:00+09', '2026-08-25 14:45:00+09', 270, 45, 'PASS', 'Lee'),
    ('OP-004', 'LOT-260829-002', 'RST-DRAM-020', 'EQ-ETCH-001', '2026-08-25 15:20:00+09', '2026-08-25 19:00:00+09', '2026-08-25 19:38:00+09', 220, 38, 'PASS', 'Lee'),
    ('OP-005', 'LOT-260829-003', 'RST-NAND-010', 'EQ-LTH-002', '2026-08-28 07:45:00+09', NULL, NULL, 0, NULL, 'HOLD', 'Park'),
    ('OP-006', 'LOT-260829-004', 'RST-NAND-030', 'EQ-DEPO-001', '2026-08-24 10:15:00+09', '2026-08-24 16:10:00+09', '2026-08-24 17:00:00+09', 355, 50, 'PASS', 'Park'),
    ('OP-007', 'LOT-260829-005', 'RST-LOGIC-010', 'EQ-LTH-002', '2026-08-27 11:20:00+09', '2026-08-27 15:00:00+09', '2026-08-27 15:45:00+09', 220, 45, 'PASS', 'Choi'),
    ('OP-008', 'LOT-260829-005', 'RST-LOGIC-020', 'EQ-ETCH-001', '2026-08-27 16:30:00+09', '2026-08-27 19:20:00+09', '2026-08-27 19:58:00+09', 170, 38, 'PASS', 'Choi'),
    ('OP-009', 'LOT-260829-006', 'RST-LOGIC-050', 'EQ-FIN-001', '2026-08-28 08:30:00+09', '2026-08-28 09:05:00+09', '2026-08-28 09:35:00+09', 35, 30, 'PASS', 'Han')
ON CONFLICT (op_id) DO NOTHING;

INSERT INTO daily_yield (
    yield_id,
    site_id,
    product_id,
    work_date,
    input_qty,
    good_qty,
    scrap_qty,
    rework_qty,
    yield_rate
) VALUES
    ('YLD-20260825-DRAM', 'fab10', 'PR-DRAM-1A', '2026-08-25', 1200, 1182, 8, 10, 0.9850),
    ('YLD-20260826-DRAM', 'fab10', 'PR-DRAM-1A', '2026-08-26', 1180, 1164, 7, 9, 0.9864),
    ('YLD-20260827-DRAM', 'fab10', 'PR-DRAM-1A', '2026-08-27', 1210, 1193, 6, 11, 0.9859),
    ('YLD-20260825-NAND', 'fab10', 'PR-NAND-1B', '2026-08-25', 980, 956, 12, 12, 0.9755),
    ('YLD-20260826-NAND', 'fab10', 'PR-NAND-1B', '2026-08-26', 1000, 978, 10, 12, 0.9780),
    ('YLD-20260827-NAND', 'fab10', 'PR-NAND-1B', '2026-08-27', 995, 973, 9, 13, 0.9779),
    ('YLD-20260827-LOGIC', 'fab12', 'PR-LOGIC-7N', '2026-08-27', 640, 631, 4, 5, 0.9859),
    ('YLD-20260828-LOGIC', 'fab12', 'PR-LOGIC-7N', '2026-08-28', 650, 642, 3, 5, 0.9877)
ON CONFLICT (yield_id) DO NOTHING;
