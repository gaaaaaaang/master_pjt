# DB 설계 작업 영역

PostgreSQL 기준으로 SMT2020 일반 공정 데이터를 먼저 적재합니다.

## SMT2020 원천 staging 구조

실행 순서:

1. `apps/assistant/sql/postgresql/010_create_smt2020_fab_staging_schemas.sql`
2. `apps/assistant/scripts/load_smt2020_postgres_staging.py`
3. `apps/assistant/sql/postgresql/011_create_smt2020_compatibility_tables.sql`

스키마 매핑:

- `apps/assistant/data/smt2020/General Data/dataset 1` -> `fab10`
- `apps/assistant/data/smt2020/General Data/dataset 2` -> `fab11`
- `apps/assistant/data/smt2020/General Data/dataset 3` -> `fab12`
- `apps/assistant/data/smt2020/General Data/dataset 4` -> `fab13`

구조 원칙:

- dataset별 PostgreSQL schema를 생성합니다.
- Excel workbook의 각 sheet를 같은 schema 안의 `{sheet}_{fab}` table로 생성합니다.
- 예: `fab11.toolgroups_fab11`. 공통 메타 패턴은 `{fab}.toolgroups_{fab}`입니다.
- 컬럼명은 PostgreSQL에서 쓰기 쉽게 snake_case로 정리합니다.
- `Setup_Matrix_Implant_Gas`는 실제 헤더가 7행에 있어 7행을 컬럼 기준으로 사용합니다.
- 원본 workbook에 없는 optional sheet는 `011_create_smt2020_compatibility_tables.sql`에서 0건짜리 호환 테이블로 생성합니다.

DDL 재생성:

```bash
/Users/a11549/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 apps/assistant/scripts/generate_smt2020_postgres_ddl.py
```

데이터 적재:

```bash
cd /Users/a11549/Desktop/skax-git/master_pjt
/Users/a11549/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 apps/assistant/scripts/load_smt2020_postgres_staging.py
```

특정 fab만 적재:

```bash
/Users/a11549/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 apps/assistant/scripts/load_smt2020_postgres_staging.py --schema fab10
```

빠른 테스트용으로 테이블당 일부 row만 적재:

```bash
/Users/a11549/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 apps/assistant/scripts/load_smt2020_postgres_staging.py --max-rows-per-table 100
```

DBeaver 확인 쿼리:

```sql
SELECT table_schema, table_name
FROM information_schema.tables
WHERE table_schema IN ('fab10', 'fab11', 'fab12', 'fab13')
ORDER BY table_schema, table_name;

SELECT 'fab10.toolgroups_fab10' AS table_name, COUNT(*) FROM fab10.toolgroups_fab10
UNION ALL
SELECT 'fab11.lotrelease_fab11' AS table_name, COUNT(*) FROM fab11.lotrelease_fab11
UNION ALL
SELECT 'fab12.lotrelease_engineering_fab12' AS table_name, COUNT(*) FROM fab12.lotrelease_engineering_fab12
UNION ALL
SELECT 'fab13.route_product_10_fab13' AS table_name, COUNT(*) FROM fab13.route_product_10_fab13;
```

원본에 없는 optional sheet 확인:

```sql
SELECT 'fab10.lotrelease_engineering_fab10' AS table_name, COUNT(*) FROM fab10.lotrelease_engineering_fab10
UNION ALL
SELECT 'fab10.lotrelease_variable_due_dates_fab10', COUNT(*) FROM fab10.lotrelease_variable_due_dates_fab10
UNION ALL
SELECT 'fab11.lotrelease_engineering_fab11', COUNT(*) FROM fab11.lotrelease_engineering_fab11
UNION ALL
SELECT 'fab12.lotrelease_variable_due_dates_fab12', COUNT(*) FROM fab12.lotrelease_variable_due_dates_fab12;
```

## 참고

`apps/assistant/sql/postgresql/001_create_general_process_schema.sql`와 `apps/assistant/sql/postgresql/002_seed_general_process_data.sql`는 SMT2020 원천 구조를 그대로 옮긴 것이 아니라, 나중에 분석/서비스용으로 정규화할 수 있는 예시 모델입니다.

다음 단계에서 Milvus용 문서/벡터 데이터 구조를 분리합니다.

## 기존 DB의 FAB 접미사 마이그레이션

2026-09-07 로컬 DB의 78개 테이블을 `{fab}.{logical_table}_{fab}` 규칙으로 변경했습니다.
501,910행의 변경 전후 행 수와 테이블 OID가 동일함을 확인했습니다. 스키마와 컬럼은 유지합니다.

프로젝트 루트에서 실행합니다. 기본은 충돌 검사용 dry-run이고 `--apply`가 있어야 변경됩니다.

```bash
.venv/bin/python apps/assistant/scripts/migrate_fab_table_names.py
.venv/bin/python apps/assistant/scripts/migrate_fab_table_names.py --apply
```

마이그레이션은 한 트랜잭션에서 이름만 변경하며, 재실행하면 이미 변경된 테이블은 건너뜁니다.
DDL/적재 스크립트도 새 이름을 사용하므로 기존 DB에는 먼저 마이그레이션을 적용해야 합니다.
코드와 DB를 함께 이전 버전으로 되돌릴 때는 같은 스크립트에 `--reverse --apply`를 사용합니다.
외부 SQL 소비자도 새 이름으로 변경해야 하며, 과거 이름의 호환 view는 만들지 않습니다.

FAB 선택은 현재 질문 → 요청 FAB → 최근 사용자 대화 순서입니다. `FAB 11`, `FAB-11`,
`팹11`, `11번 팹`, `M11`을 정규화합니다. FAB이 없거나 복수/지원 밖이면 SQL 전에 확인 질문을 합니다.
제품명이나 설비명만으로 FAB을 추측하지 않습니다. `{fab}` 바인딩은 공통 naming helper에서
허용된 FAB과 논리 테이블명을 검증한 뒤 SQL 생성 전에 수행합니다.

공통 메타 DB `agent_meta.table_catalog`는 32개 논리 정의로 78개 물리 테이블을 관리합니다.
`.venv/bin/python apps/assistant/scripts/sync_metadata_catalog.py`로 메타 정보를 갱신합니다.
조회 시 현재 DB의 실제 테이블/컬럼도 읽어 저장 메타나 과거 실패로 조회를 막지 않습니다.
시뮬레이션 snapshot/event를 포함한 FAB10~13 업무 데이터 전체를 read-only로 탐색합니다.
실제 메타를 쓰는 LLM 경로에는 SQL 전 구조화 계획 검증과 SQL-plan AST 대조가 적용됩니다.
범위/한계와 회귀 결과는 `docs/text2sql_hackathon_20260907.md`에 기록합니다.

공통 카탈로그의 `semantics` JSONB에는 코드에서 확인한 grain, 출처, 컬럼 의미,
집계 주의사항, 검토 상태를 저장합니다. `semantic_metadata.py`의 정의는 업무 담당자가
검토한 SSOT가 아닌 `code_grounded` 단계입니다. 관리자가 직접 관리할 때는
`semantics.managed_by`를 `editor`로 지정하면 동기화가 의미/지표 정의를 덮어쓰지 않습니다.
메타의 지표는 해당 FAB의 실제 존재 컬럼에 한해 제공됩니다. 합성 `etch`와 모델
`Dry_Etch`, 합성 toolgroup과 모델 toolgroup은 같은 조인 키라고 가정하지 않습니다.

### 접근 불가 응답 복구 검증 (2026-09-07)

테이블명 이전 전의 `relation does not exist` 오류가 대화에 남아 Planner가 새 조회도
거절하던 문제를 수정했습니다. `alternate_agent`는 전문 agent 전환 후보이며 DB 소스
접근 정책이 아닙니다. 현재 DB 조회 결과가 과거 assistant의 접근 불가 설명보다 우선합니다.
명시적인 toolgroup 목록 요청은 이전 WIP 추이 의도를 상속하지 않습니다.

전체 단위/회귀 테스트 373개와 실제 DB 결정론적 평가 79개가 통과했습니다.
사용자 승인 후 설정된 LLM을 사용해 FAB10 WIP 추이(3행), FAB11 시뮬레이션 WIP(18행)의
성공 응답과 차트를 확인했습니다. 기존 실패/WIP 대화를 이어 화면과 동일한
`/api/chat/stream` 경로로 FAB10 Dry_Etch toolgroup 목록(32행)을 조회해 올바른 SQL과
`succeeded` 최종 응답을 확인했습니다. UI 자체의 시각 검증은 포함하지 않습니다.
