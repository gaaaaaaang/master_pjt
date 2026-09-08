# 메타 카탈로그 기반 Text2SQL 계획 수립 제안

작성일: 2026-09-07. 상태: 공통 메타 DB, FAB 추론, 패턴 바인딩, 실제 DB 기반 스키마 탐색 적용 완료.
SQL 생성 전 독립 계획 검증과 일부 SQL-plan AST 대조까지 적용했다. 아래 상세 설계에는
미구현 확장 항목도 포함되므로 실제 완료 범위는 해커톤 기록 및 architecture.md를 기준으로 한다.

## 목적과 근거

전체 업무 테이블의 구조와 의미를 PostgreSQL 메타 카탈로그에 저장하고, Text2SQL이
질문에 필요한 메타데이터를 검색하여 조회 계획을 검증한 다음 SQL을 생성한다.
기존 MARS-SQL/TriSQL 기반 Grounding, Structure/Plan, Validation 원칙은 유지한다.

참고: [토스 PANDA 소개](https://toss.tech/article/da-assistant-panda).
글에서 확인되는 것은 표준 마트/컬럼 설명, 업무 용어 정의, dbt tags로 선별한 Manifest,
유사도와 계층 신뢰도를 결합한 테이블 선택, 실행 피드백 루프다. 별도 관계형 메타 테이블의
DDL은 공개하지 않았으므로 아래 DB 설계는 우리 프로젝트에 대한 제안이다.

## 초기 구현과의 차이 (고도화 전 기준)

- `apps/assistant/src/app/sub_agent/text2sql.py`의 `SCHEMA_CATALOG`는 Python 상수다.
- `_schema_context_for_question()`은 query type과 slot으로 후보를 선택하고 컬럼명,
  metric/date catalog 및 공통 규칙을 전달한다.
- LLM 경로는 `create_sql()` 한 번으로 SQL과 계획 속성을 받고,
  `_result_from_llm_output()`에서 `QueryPlan`을 구성한다.
- 결정론적 fast path는 이 LLM 경로보다 먼저 실행될 수 있다.
- 따라서 현재 계획 정보가 반환된다는 사실과, SQL 생성 전에 별도 계획을 검증한다는 것은 다르다.
- 초기 `text2sql_agent_design.md`의 template-only/AutoSched 미적재 설명은 현재
  `development_todo.md` 및 구현과 차이가 있다. 이번 제안은 현재 직접 SQL 생성 경로를 기준으로 한다.
  2026-09-07 실제 확인 결과 FAB10~13에 78개 업무 테이블이 있으며 AutoSched 보고 테이블은
  FAB10에만 존재한다. FAB별 가용성을 공통 구조와 분리해야 한다.

## 메타데이터 저장 구조

사용자 제안에 따라 **하나의 물리 메타 테이블 `agent_meta.table_catalog`에 논리 업무
테이블당 한 행**을 저장한다. FAB별 공통 정의는 복제하지 않고 `{fab}.autosched_part_{fab}`처럼
경로를 표현한다. 아래 네 종류의 정보 중 컬럼/관계/지표는 별도 테이블 대신 해당 행의
`columns`, `relationships`, `metrics` JSONB 필드에 저장한다.

공통 행의 주요 필드는 `table_id`, `table_pattern`, `description`, `aliases`,
`domain`, `data_source_type`, `row_grain`, `time_semantics`, `columns`, `relationships`,
`metrics`, `trust_tier`, `review_status`, `schema_fingerprint`, `fab_status`,
`catalog_version`, `updated_at`, `provenance`다. 아래 표는 정보별 상세 속성이다.

| 메타 정보 | 주요 필드 | 계획에서의 역할 |
| --- | --- | --- |
| 테이블 | 공통 `table_pattern`, 설명, grain, 시간 의미, 신뢰도; FAB별 존재 여부, 구조 확인, 데이터 기준 시각/기간 | 어떤 데이터를 어느 단위로 저장하는지, 요청 기간/용도를 충족하는지 판단 |
| `column_catalog` | `column_id`, `table_id`, `column_name`, `data_type`, `nullable`, `description`, `aliases`, `unit`, `semantic_role`, `allowed_values`, `aggregation_rule`, `review_status` | 질문의 용어를 실제 컬럼에 연결하고 단위/집계 오류 방지 |
| `relationship_catalog` | `relationship_id`, `left_table_id`, `right_table_id`, `join_key_pairs`, `cardinality`, `required_predicates`, `description`, `evidence_source`, `review_status` | 복합키와 시간 조건까지 포함하여 조인 가능 여부와 행 중복 위험 판단 |
| `metric_catalog` | `metric_id`, `name`, `aliases`, `definition`, `source_bindings`, `aggregation_rule`, `required_filters`, `time_semantics`, `unit`, `review_status` | WIP/가동률/처리량 등의 정의와 사용 가능한 소스를 고정 |

계약:

- `table_pattern`은 유일하고 JSONB 내부 컬럼명/지표 ID도 정의 범위 안에서 유일해야 한다.
  JSONB 내부 연결은 DB FK가 자동 보장하지 않으므로 게시 전에 앱 모델로 참조 무결성을 검증한다.
- 설명/컬럼/관계/지표는 FAB 공통으로 한 번만 저장한다. `fab_status` JSONB에는 FAB별
  `is_available`, 실제 구조 fingerprint, `last_synced_at`, `data_as_of`, 기간 범위를 저장한다.
  미확인 값은 null이며 공통 메타데이터를 복제하지 않는다.
- `{fab}`은 `fab10`, `fab11`, `fab12`, `fab13` 중 확정된 slot으로만 바인딩한다.
  예: `{fab}.toolgroups_{fab}` → `fab11.toolgroups_fab11`.
  사용자 원문을 그대로 치환하지 않는다. 스키마/테이블은 식별자 API로 구성하고 조건 값은
  SQL parameter로 바인딩한다. 조인 대상도 동일 FAB에 바인딩한다.
- 현재 `ROUTE_TABLES_BY_FAB`에는 FAB별 route 테이블 목록 차이가 있다. 같은 테이블의
  구조 차이와는 별개다. 공통 정의는 유지하고 실제 존재 여부는 FAB별로 확인한다.
- FAB 간 구조를 수집 시 비교하고 불일치가 있으면 해당 바인딩을 재검토 대상으로 처리한다.
  FAB 간 비교 요청은 명시적인 별도 계획으로 다룬다.
- 모든 메타 행에 버전, 작성/검토 출처, 수정 시각을 남긴다. 한 요청은 동일한 catalog version을 사용한다.
- 구조화된 aliases, join key pairs, source bindings, 조건은 JSONB로 저장하되 앱 모델로 검증한다.
- `source_bindings`는 지표가 적용되는 테이블/컬럼과 grain을 지정한다. 같은 이름의 지표가
  테이블마다 같다고 가정하지 않는다. 관계/지표의 모든 참조가 해당 버전 카탈로그에 존재해야 한다.
- `row_grain`은 한 행의 의미이고 PK와 구분한다. 확실하지 않은 grain/단위/시간 의미는 unknown이다.
- FK가 없더라도 검토된 논리 관계를 등록할 수 있다. 컬럼명이 비슷하다는 이유로 조인을 승인하지 않는다.
- `last_synced_at`은 메타 수집 시각, `data_as_of`는 데이터 기준 시각이다. 두 값을 혼용하지 않는다.
- 전체 업무 테이블을 목록화하되 검색/생성 후보는 사용 가능하고 검토되었으며 기존 접근 정책이 허용하는
  대상으로 제한한다. 등록만으로 실행 allowlist를 확대하지 않는다.

## 요청 처리 흐름

```text
질문 + slot
  → 카탈로그 검색: FAB / 데이터 용도 / 지표 / 기간 조건으로 후보 제한
  → 후보 순위: 이름·동의어·설명 관련성 + 요청 단위 적합성 + 검토 신뢰도
  → 선택 테이블의 컬럼·관계·지표 상세 로드
  → GroundedSchema 구성
  → SQL 작성 전 Semantic QueryPlan 생성
  → 계획 검증
  → 기존 생성 경로로 SQL 작성
  → SQL과 계획 일치 검사 + 기존 read-only 검증
  → 실행 및 결과 검증
  → 필요 시 카탈로그 재검색 / 계획 수정 / 제한된 재시도
```

전체 목록을 저장하는 것과 전체 스키마를 매번 LLM에 넣는 것은 구분한다.
첫 단계에서는 테이블 요약을 검색하고, 다음 단계에서 선택된 대상의 상세만 전달한다.
초기는 PostgreSQL 이름/동의어 검색과 규칙 기반 ranking으로 시작할 수 있다.
임베딩 검색은 평가 결과가 필요성을 보여줄 때 추가한다. 관련 없는 후보는 높은 신뢰도로도
통과시키지 않으며, 필요한 join/filter/order 컬럼은 검색 점수가 낮아도 포함한다.
후보 누락이 의심되면 횟수를 제한하여 검색 범위를 넓힌다.

상위 Planner는 Text2SQL/RAG/Impact 등 작업 조합을 결정하고, Text2SQL 내부 planner는
선택한 메타데이터에 근거해 테이블/컬럼/필터/조인/집계/시간 기준을 결정한다.

## SQL 생성 전 계획 계약

기존 `QueryPlan`에 다음 정보를 추가하거나 별도 검증 모델을 둔다.

- `catalog_version`, 선택한 table/column/relationship/metric ID
- `selection_reasons`: 테이블 선택 근거와 제외 후보의 중요한 사유
- `source_tables`, `dimensions`, `metrics`, 구조화된 `filters`, `joins`
- `aggregation`, `group_by`, `time_basis`, `time_range`, `row_grain`, `ordering`, `row_limit`
- `expected_result_shape`, `assumptions`, `missing_metadata`

예시 질문: “FAB10 제품별 현재 WIP를 보여줘.”

1. 제품 단위 operational report 후보를 검색한다.
2. 현재 코드상 `autosched_part`에 `part`, `wiplotcur`, `wiplotavg`가 있으므로,
   검토된 지표 정의에서 현재 WIP에 대응하는 컬럼과 단위를 확인한다.
3. `wiplotcur`가 요구 의미와 일치한다고 검증된 경우 그 컬럼을 선택한다.
   기간 평균 WIP인 `wiplotavg`로 임의 대체하지 않는다.
4. 최신 snapshot 선택 범위, 제품별 행 grain, WarmUp 제외 기준을 계획에 명시한다.
   누적/중복 report를 전부 합산하지 않는다.
5. 조인이 필요 없으면 `joins=[]`로 명시하고 계획을 검증한 뒤 SQL을 생성한다.
6. 반환 데이터는 실제 실시간 데이터라는 주장 대신 확인된 report 기준 시각을 표시한다.

질문만으로 현재값/평균값이 구분되지 않으면 검토된 기본 의미가 있는지 확인하고,
결과를 바꾸는 모호함이 남으면 clarification으로 보낸다.

## 두 논문에서 가져온 구조와의 연결

| 기존 설계 원칙 | 이번 변경 |
| --- | --- |
| MARS-SQL Grounding 역할 분리 | 메타 카탈로그 검색과 상세 검증을 Grounding의 근거로 사용 |
| TriSQL question-guided schema selection | 테이블 요약 검색 → 관련 컬럼/관계/지표 선택 |
| TriSQL structure-first generation | SQL 생성 전 계획을 별도 생성하고 검증 |
| MARS-SQL 실행 피드백 기반 개선 | 오류를 SQL 문법, 스키마 선택, 지표/조인 의미 문제로 나눠 해당 단계로 복귀 |
| 기존 complexity/retry 정책 | 탐색과 계획 수정에도 전체 요청 retry budget 적용 |

이는 프로젝트 문서에 정리된 연구 원칙을 확장하는 제안이며 논문의 학습/모델 구조를 그대로 재현한다는 뜻은 아니다.

## 카탈로그 생성과 갱신

1. 수집 스크립트가 허용된 FAB 스키마의 PostgreSQL catalog를 읽어 테이블, 컬럼, 타입,
   nullable, PK/FK, 기존 comment를 수집한다. 기존 상수와 SQL/loader 문서는 seed 자료로 쓴다.
   FAB 간 구조를 비교하여 공통 정의는 한 번 저장하고 존재 여부/확인 시각은 FAB별 상태로 기록한다.
2. 설명, 한글 동의어, grain, 단위, 지표 정의, 논리 조인, 기본 시간 기준은 업무 정의로 보강한다.
   LLM 설명 초안을 만들 수 있지만 자동으로 검토 완료 처리하지 않는다.
3. 수집용 쓰기 경로와 요청 처리용 읽기 경로를 분리한다. Text2SQL 요청은 메타데이터를 수정하지 않는다.
4. 신규/변경/삭제 스키마를 감지하여 새 버전을 검증한 후 게시한다. 삭제 대상을 이전 캐시로 조회하지 않는다.
   수집 중간 상태를 요청에 노출하지 않는다.
5. 구조 변경 시 수동 설명을 무조건 덮어쓰지 않는다. 영향받는 지표/관계는 재검토 대상으로 전환한다.

## 구현 순서 및 검증 기준

1. DB migration, 검증 모델, 수집/seed 스크립트, 읽기 repository를 추가한다.
2. `_schema_context_for_question()`의 근거를 카탈로그로 옮기고 버전/선택 근거를 trace에 남긴다.
3. SQL 생성 전에 계획 생성/검증 단계를 추가하고 기존 LLM 생성기에 검증된 계획을 전달한다.
4. 생성 SQL의 실제 table/column/join/filter/aggregation을 파싱하여 계획과 대조한다.
   LLM이 반환한 `source_tables` 목록만으로 일치를 판단하지 않는다.
5. 결정론적 fast path도 동일 카탈로그의 스키마/지표 계약을 확인하게 한다.
   LLM을 호출할 필요는 없지만 변경된 스키마 검증을 우회해서는 안 된다.
6. 기존 Text2SQL 평가에 table/column recall, plan correctness, 잘못된 join/지표 선택률,
   schema drift 대응, latency/token cost를 추가한다.

필수 회귀 사례: 현재 WIP vs 평균 WIP, 계획 데이터 vs 상태 report, FAB별 route 차이,
release start_date vs due_date, 잘못된 다대다 조인의 합계 증폭, 누락 컬럼/폐기 테이블,
메타 검색 후보 0건, 미검토 관계, 지원하지 않는 Queue Time.

성공 기준은 SQL 실행 성공률만의 상승이 아니다. 같은 평가 질문에서 올바른 테이블/컬럼/지표와
조회 단위를 선택하는 비율이 개선되고, 기존 실행 정확도와 데이터 부족 시 응답 계약이 유지되어야 한다.

## 현재 적용 결과: 전체 FAB 데이터 조회 및 과거 오류 복구

- `agent_meta.table_catalog` 한 테이블에 32개 논리 정의를 저장하여 78개 물리 테이블을 관리한다.
- `columns`는 공통 컬럼 정의이며 실제 FAB 구조가 다른 경우에만 `fab_status.columns_override`를 저장한다.
- `aliases`, `relationships`, `metrics`의 업무 정의 보강은 별도 검토 작업이며, 현재 자동 수집 항목은
  이름, 기존 설명, 타입, nullable, FAB별 가용성이다. 없는 의미 정보를 검토 완료로 간주하지 않는다.
- 조회 시 현재 PostgreSQL catalog에서 전체 테이블/컬럼을 확인하고 저장된 메타 설명을 결합한다.
  DB의 현재 구조가 가용성의 기준이므로 과거 대화/메타 snapshot이 새로운 테이블을 숨기지 않는다.
- 기본 허용 범위는 FAB10~13 내 실제 SELECT 가능한 업무 테이블 전체다. read-only 실행 정책은 유지한다.
- Planner/Supervisor가 과거 대화의 DB 오류만으로 `data_unavailable`을 선언하면 새로운
  Text2SQL 조회를 먼저 수행한다. 실제 도구의 데이터 없음/연결 오류는 그대로 구분하여 반환한다.
- `alternate_agent`는 다른 specialist로 전환하는 기능이다. 비어 있다고 다른 DB 테이블 접근이
  금지된 것은 아니다. Composer와 Supervisor는 이 차이를 명시한 프롬프트를 사용한다.
- 지원되는 결정론적 쿼리는 그대로 사용하되, 실제 카탈로그에 소스가 없으면 생성 경로에서 다른
  관련 소스를 검토한다. 시뮬레이션 snapshot/event 사용 시 데이터 종류를 명시한다.

갱신 명령: `.venv/bin/python apps/assistant/scripts/sync_metadata_catalog.py`.
업무 데이터는 수정하지 않고 메타 정보만 갱신한다.

## 수동 업무 설명의 현재 편집 계약

- 물리 컬럼 이름·타입·nullable·존재 여부는 요청 시 PostgreSQL에서 확인한다. `columns`와 `fab_status`는 수집 산출물이므로 수동 편집의 영구 저장 위치로 사용하지 않는다.
- 업무 설명은 `semantics.managed_by = "editor"`와 함께 `semantics.column_meanings.<column>.description`에 저장한다. 이 설정은 동기화 시 semantics/metrics를 보존하며, 조회 시 존재하는 컬럼의 설명으로 전달된다.
- 현재 PostgreSQL column comment가 있으면 이를 우선한다. comment가 비어 있으면 수동 semantics 설명, 코드에서 확인한 기본 설명 순으로 보완한다. 존재하지 않는 컬럼을 새로 만들어 넣거나 타입을 바꾸지 않는다.
- `managed_by=editor`는 편집 소유권 표시다. 업무 검토 완료나 SSOT 신뢰도 승인을 자동으로 의미하지 않는다. review_status와 검토 근거는 별도로 관리한다.
- 관계는 같은 FAB의 현재 테이블/컬럼으로 바인딩되며 누락 키가 있으면 사용하지 않는다. PK/FK의 일반 자동 수집, 변경 영향의 자동 재검토 상태 전환, 관리자 편집 UI는 아직 미구현이다.
