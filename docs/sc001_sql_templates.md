# SC-001 SQL Templates

These templates are the first Text2SQL target for current status questions. They assume AutoSched reports will be loaded into `autosched_*` staging tables described in `docs/sc001_data_contract.md`.

## Template List

### 1. `sc001_fab_status_summary`

Question examples:

- "fab10 현재 WIP 몇 개야?"
- "fab13 전체 생산 상태 보여줘"

Source table:

- `{fab}.autosched_perf`

Main metrics:

- `lotstarts`
- `lotcomps`
- `wiplotavg`
- `ontime_percent`
- `cycleavg`

### 2. `sc001_process_group_status`

Question examples:

- "fab11 Dry_Etch 상태 어때?"
- "공정 그룹별 WIP 높은 순서 보여줘"

Source table:

- `{fab}.autosched_stngrp`

Main metrics:

- `stngrp`
- `lotcomps`
- `util_percent`
- `wiplotavg`
- `proc_percent`
- `down_percent`
- `pm_percent`

### 3. `sc001_station_status`

Question examples:

- "DE_BE_11 설비 상태 확인해줘"
- "fab12에서 WIP 높은 station 보여줘"

Source table:

- `{fab}.autosched_stn`

Main metrics:

- `stn`
- `curstate`
- `util_percent`
- `wiplotavg`
- `down_percent`
- `pm_percent`

### 4. `sc001_product_status`

Question examples:

- "part_3 현재 WIP 알려줘"
- "fab13 제품별 WIP 순위 보여줘"

Source table:

- `{fab}.autosched_part`

Main metrics:

- `part`
- `lotstarts`
- `lotcomps`
- `wiplotavg`
- `wiplotcur`
- `ontime_percent`

### 5. `sc001_lot_status`

Question examples:

- "Init_Lot_3_24 어디까지 진행됐어?"
- "이 lot 현재 공정 알려줘"

Source table:

- `{fab}.autosched_lot`

Main metrics:

- `lot`
- `part`
- `startdate`
- `compdate`
- `duedate`
- `stepcomps`
- `curstn`
- `curstep`

## Guardrails

- `fab_id` must be one of `fab10`, `fab11`, `fab12`, `fab13`.
- Generated SQL must pass `ReadOnlyQueryExecutor.validate`.
- Queries must stay schema-qualified and read-only.
- API implementation is intentionally deferred; the next implementation step is constrained Text2SQL template selection.
