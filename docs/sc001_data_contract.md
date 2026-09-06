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

Last checked against the local PostgreSQL database on 2026-09-03.

Loaded schemas:

- `fab10`
- `fab11`
- `fab12`
- `fab13`

General Data table families are loaded in `fab10` through `fab13`:

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

AutoSched report tables currently exist only in `fab10`. On 2026-09-03, the tables were
recreated from the complete dataset 1 `HVLM_Model` source reports without scenario-specific
synthetic rows:

| table | DB rows | source rows | DB max report time |
| - | -: | -: | - |
| `autosched_perf` | 4 | 4 | 2021-12-31 00:00:00 |
| `autosched_stngrp` | 48 | 48 | 2021-12-31 00:00:00 |
| `autosched_stn` | 5,772 | 5,772 | 2021-12-31 00:00:00 |
| `autosched_part` | 8 | 8 | 2021-12-31 00:00:00 |
| `autosched_lot` | 85,772 | 85,772 | null |
| `autosched_order` | 24 | 24 | 2021-12-31 00:00:00 |
| `autosched_semi` | 418 | 418 | null |
| `autosched_stnfam` | 424 | 424 | 2021-12-31 00:00:00 |

`fab11` through `fab13` do not currently contain `autosched_*` tables. Master-data and
release-plan questions remain available there, but SC-001 operational answers are currently
release-gated only for `fab10`.

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
- When the required AutoSched table is absent or the source-snapshot release gate has not
  passed, API answers must expose the limitation instead of presenting the result as complete.
- SC-001 status templates that depend on unavailable or incomplete `autosched_*` data must
  return `data_unavailable` instead of falling back to General Data.
- General Data may support adjacent lookup questions such as "fab10 Dry_Etch에는 어떤
  toolgroup이 있어?", "Product_3 route step 보여줘", "release plan의 due date는?",
  but these should be classified as `master_data_lookup` or `release_plan_lookup`,
  not as live status.

## Remaining Load Scope

The loader supports these report staging tables. `fab10` must first be recreated from the full
source reports; the same tables can then be added to `fab11` through `fab13` when those source
reports are brought into scope:

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

## Golden Query Release Gate

The source-snapshot contract is stored in
`apps/assistant/tests/fixtures/sc001_fab10_golden.json`. It fixes the complete source row counts,
the latest source report time (`2021-12-31 00:00:00`), and representative fab, process-group,
station, product, and lot query results. A separate robustness subset uses different entities,
an active lot, a historical period, and two natural-language phrasings per query type so the
gate is not tied only to the original scenario examples.

Run the read-only gate with:

```bash
PYTHONPATH=apps/assistant/src:apps/assistant uv run python \
  apps/assistant/scripts/check_sc001_release_gate.py
```

Freshness means equality with the latest SMT2020 source snapshot, not proximity to the current
wall clock. The fully reloaded local `fab10` database passes this gate as of 2026-09-03.
