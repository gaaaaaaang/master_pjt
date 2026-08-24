# DB 설계 작업 영역

실제 SMT2020 파일의 컬럼 매핑이 확정되면 아래 순서로 migration을 추가합니다.

- `001_create_staging_tables.sql`: 원본 적재용
- `002_create_fab_views.sql`: WIP, Queue Time, Yield 조회용 표준 뷰
- `003_readonly_user.sql`: Agent 조회 전용 계정

