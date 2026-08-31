# SC-001 Data Contract

## Purpose

SC-001 answers current status questions such as "지금 fab10 WIP 몇 개야?" or "Dry_Etch 공정 상태 어때?" using loaded SMT2020 data.

## Dataset Mapping

- `dataset 1` -> `fab10`
- `dataset 2` -> `fab11`
- `dataset 3` -> `fab12`
- `dataset 4` -> `fab13`

## Current Data Coverage

### PostgreSQL Audit Snapshot

Last checked against the local PostgreSQL container on 2026-09-01.

Loaded schemas:

- `fab10`
- `fab11`
- `fab12`
- `fab13`

Loaded table families:

- `toolgroups`
- `route_product_*`
- `lotrelease`
- `lotrelease_variable_due_dates`
- `lotrelease_engineering`
- `pm`
- `breakdown`
- `setups`
- `setup_matrix_implant_gas` where available
- `transport`

Not currently loaded:

- `autosched_perf`
- `autosched_stngrp`
- `autosched_stn`
- `autosched_part`
- `autosched_lot`
- `autosched_order`
- `autosched_semi`
- `autosched_stnfam`

This means the current PostgreSQL database can answer master-data, route, release-plan,
PM, breakdown, setup, and transport questions. It cannot yet answer live/current WIP,
station state, utilization, queue, cycle-time, or on-time status questions.

### General Data

The `fab10` through `fab13` schemas currently contain SMT2020 General Data workbook sheets. These are model input and master/reference data.

Use cases:

- Product, route, process step, toolgroup, setup, PM, breakdown master lookup
- Release plan lookup from `lotrelease*` tables

Limitations:

- These tables do not represent live factory state.
- WIP, station state, utilization, cycle time, and queue-related operational metrics should not be inferred only from General Data.
- Route data is stored as one table per product route, for example `route_product_3`,
  `route_product_10`, or engineering route tables such as `route_product_e3`.
  Text2SQL must resolve a product/route slot to an allowlisted route table before
  rendering SQL.
- `lotrelease_variable_due_dates` can be large. Current audited examples include
  167,129 rows in `fab11` and `fab13`, so every query against release-plan tables must
  require selective filters, explicit row limits, and deterministic ordering.
- Some compatibility tables intentionally contain zero rows where the source workbook
  did not include the optional sheet. Empty compatibility tables must be reported as
  "not provided in source data", not as "no matching events occurred".

### AutoSched Reports

AutoSched `.rep` files are UTF-16 tab-delimited report outputs. They are the right source for SC-001 operational status.

Primary report files:

- `perf.rep`: fab-level status. Key columns: `PERIOD`, `LOTSTARTS`, `LOTCOMPS`, `WIPLOTAVG`, `ONTIME%`, `CYCLEAVG`.
- `stngrp.rep`: process group status. Key columns: `STNGRP`, `LOTCOMPS`, `UTIL%`, `WIPLOTAVG`, `PROC%`, `DOWN%`, `PM%`.
- `stn.rep`: station/tool status. Key columns: `STN`, `LOTCOMPS`, `UTIL%`, `WIPLOTAVG`, `CURSTATE`, `DOWN%`, `PM%`.
- `part.rep`: product status. Key columns: `PART`, `LOTSTARTS`, `LOTCOMPS`, `WIPLOTAVG`, `WIPLOTCUR`, `ONTIME%`.
- `lot.rep`: lot-level completion/status detail. Key columns: `PART`, `LOT`, `STARTDATE`, `COMPDATE`, `DUEDATE`, `CURSTN`, `CURSTEP`, `STEPCOMPS`.

Supporting report files:

- `order.rep`: order-level delivery and WIP summary.
- `semi.rep`: weekly in/out, scrap, WIP, and cycle summary.
- `stnfam.rep`: station family summary.
- `cont.rep`: clock time report. Currently not useful for SC-001 because it has no observed data rows in inspected sample.

## SC-001 Query Rules

- For fab-level WIP: use `perf.rep` once loaded into PostgreSQL, preferably the latest non-WarmUp period.
- For process/area WIP: use `stngrp.rep`.
- For station/equipment state: use `stn.rep`.
- For product WIP: use `part.rep`.
- For lot detail: use `lot.rep`.
- Until AutoSched reports are loaded into PostgreSQL, API answers must expose that they are based only on General Data or return a clear limitation.
- While only General Data is loaded, SC-001 status templates that depend on
  `autosched_*` must return `data_unavailable` instead of falling back to master data.
- General Data may support adjacent lookup questions such as "fab10 Dry_Etch에는 어떤
  toolgroup이 있어?", "Product_3 route step 보여줘", "release plan의 due date는?",
  but these should be classified as `master_data_lookup` or `release_plan_lookup`,
  not as live status.

## Minimum Tables To Finalize Next

The next loader should create these report staging tables in each `fab10` through `fab13` schema:

- `autosched_perf`
- `autosched_stngrp`
- `autosched_stn`
- `autosched_part`
- `autosched_lot`
- `autosched_order`
- `autosched_semi`
- `autosched_stnfam`

Column names should be normalized to snake_case, and each table should include:

- `source_row_id`
- `source_file`
- `report_time`
- original report columns
