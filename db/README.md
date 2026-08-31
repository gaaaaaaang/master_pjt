# DB 설계 작업 영역

PostgreSQL 기준으로 SMT2020 일반 공정 데이터를 먼저 적재합니다.

## SMT2020 원천 staging 구조

실행 순서:

1. `postgresql/010_create_smt2020_fab_staging_schemas.sql`
2. `scripts/load_smt2020_postgres_staging.py`
3. `postgresql/011_create_smt2020_compatibility_tables.sql`

스키마 매핑:

- `SMT_2020 - Final/General Data/dataset 1` -> `fab10`
- `SMT_2020 - Final/General Data/dataset 2` -> `fab11`
- `SMT_2020 - Final/General Data/dataset 3` -> `fab12`
- `SMT_2020 - Final/General Data/dataset 4` -> `fab13`

구조 원칙:

- dataset별 PostgreSQL schema를 생성합니다.
- Excel workbook의 각 sheet를 같은 schema 안의 table로 생성합니다.
- 컬럼명은 PostgreSQL에서 쓰기 쉽게 snake_case로 정리합니다.
- `Setup_Matrix_Implant_Gas`는 실제 헤더가 7행에 있어 7행을 컬럼 기준으로 사용합니다.
- 원본 workbook에 없는 optional sheet는 `011_create_smt2020_compatibility_tables.sql`에서 0건짜리 호환 테이블로 생성합니다.

DDL 재생성:

```bash
/Users/a11549/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 scripts/generate_smt2020_postgres_ddl.py
```

데이터 적재:

```bash
cd /Users/a11549/Desktop/skax-git/master_pjt
/Users/a11549/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 scripts/load_smt2020_postgres_staging.py
```

특정 fab만 적재:

```bash
/Users/a11549/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 scripts/load_smt2020_postgres_staging.py --schema fab10
```

빠른 테스트용으로 테이블당 일부 row만 적재:

```bash
/Users/a11549/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 scripts/load_smt2020_postgres_staging.py --max-rows-per-table 100
```

DBeaver 확인 쿼리:

```sql
SELECT table_schema, table_name
FROM information_schema.tables
WHERE table_schema IN ('fab10', 'fab11', 'fab12', 'fab13')
ORDER BY table_schema, table_name;

SELECT 'fab10.toolgroups' AS table_name, COUNT(*) FROM fab10.toolgroups
UNION ALL
SELECT 'fab11.lotrelease' AS table_name, COUNT(*) FROM fab11.lotrelease
UNION ALL
SELECT 'fab12.lotrelease_engineering' AS table_name, COUNT(*) FROM fab12.lotrelease_engineering
UNION ALL
SELECT 'fab13.route_product_10' AS table_name, COUNT(*) FROM fab13.route_product_10;
```

원본에 없는 optional sheet 확인:

```sql
SELECT 'fab10.lotrelease_engineering' AS table_name, COUNT(*) FROM fab10.lotrelease_engineering
UNION ALL
SELECT 'fab10.lotrelease_variable_due_dates', COUNT(*) FROM fab10.lotrelease_variable_due_dates
UNION ALL
SELECT 'fab11.lotrelease_engineering', COUNT(*) FROM fab11.lotrelease_engineering
UNION ALL
SELECT 'fab12.lotrelease_variable_due_dates', COUNT(*) FROM fab12.lotrelease_variable_due_dates;
```

## 참고

`postgresql/001_create_general_process_schema.sql`와 `postgresql/002_seed_general_process_data.sql`는 SMT2020 원천 구조를 그대로 옮긴 것이 아니라, 나중에 분석/서비스용으로 정규화할 수 있는 예시 모델입니다.

다음 단계에서 Milvus용 문서/벡터 데이터 구조를 분리합니다.
