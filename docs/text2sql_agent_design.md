# FAB Text2SQL Agent 상세 설계

## 1. 문서 목적

이 문서는 FAB AI Assistant의 Text2SQL sub-agent를 구현하기 전에 런타임 구조,
LangGraph 상태와 노드, 프롬프트 계약, SQL 안전 정책, 검증 및 fallback 정책을 고정한다.

설계 목표는 사용자의 질문을 곧바로 자유 형식 SQL로 번역하는 것이 아니다. 질문을
도메인 의도와 slot으로 구조화하고, 관련 스키마를 제한하고, 검증된 SQL template 또는
허용된 query plan으로 변환한 뒤, 실행 결과까지 확인하여 근거와 한계를 포함한 결과를
상위 agent에 반환하는 것이다.

이 설계는 다음 두 연구의 장점을 현재 프로젝트 상황에 맞게 조합한다.

- [MARS-SQL](https://arxiv.org/html/2511.01008v2): Grounding, Generation,
  Validation 역할 분리와 실행 피드백 기반의 반복 개선
- `A robust natural language text-to-SQL generation framework with dynamic
  strategies based on LLMs`(TriSQL): question-guided schema selection,
  structure-first generation, complexity-aware refinement와 fallback

현재 단계에서는 두 논문의 학습 방법을 그대로 재현하지 않는다. MARS-SQL의 GRPO 기반
강화학습, 8개 trajectory rollout, 별도 7B validation model은 초기 서비스의 데이터 규모와
비용에 비해 과하다. 대신 역할 분리, 실행 관찰, 후보 선택이라는 시스템 원리를 사용한다.
TriSQL의 학습형 schema selector와 skeleton decoder도 그대로 학습하지 않고, FAB 도메인
catalog, slot, template registry와 structured LLM output으로 구현한다.

## 2. 최종 아키텍처 결정

### 2.1 LangGraph와 SKILL.md의 역할

결론은 다음과 같다.

- 서비스 런타임 orchestration: **LangGraph 사용**
- Text2SQL 행동 규칙과 출력 형식: **코드로 검증되는 Pydantic schema + 버전 관리 prompt 사용**
- `SKILL.md`: **Codex 같은 개발 agent가 이 저장소에서 작업할 때 읽는 개발 지침으로만 사용**
- 운영 중인 FAB 챗봇이 매 요청마다 `SKILL.md`를 읽어 행동을 결정하게 만들지 않음

`SKILL.md`는 개발 도구의 작업 규칙을 전달하는 데 적합하지만, 애플리케이션 런타임의
상태 전이, 재시도 횟수, timeout, checkpoint, 조건부 분기를 보장하지 못한다. 또한 일반
FastAPI/LangGraph 실행 환경에서 자동으로 해석되는 표준도 아니다. 따라서 이를 agent
orchestrator로 사용하면 테스트 가능성과 관찰 가능성이 떨어진다.

운영 프롬프트는 `app/sub_agent/prompts/` 아래에 역할별 파일로 관리하거나 Python 상수로
관리한다. 초기에는 prompt가 짧으므로 Python 모듈 + Pydantic output model 조합을 권장한다.
prompt 변경이 잦아지면 `.md` 또는 `.yaml` 파일로 분리하되, 시작 시 한 번 로드하고
`prompt_version`을 로그에 남긴다. 파일명이 `SKILL.md`일 필요는 없다.

### 2.2 두 개의 LangGraph 계층

전체 FAB Assistant graph와 Text2SQL 전용 subgraph를 분리한다.

```text
Main FAB Graph

START
  -> input_normalizer
  -> context_resolver
  -> router
  -> planner
  -> clarification_gate
  -> supervisor
       -> text2sql_subgraph
       -> rag_subgraph
       -> impact_subgraph
       -> case_search_subgraph
       -> visualization_subgraph
  -> evidence_merger
  -> reflection
  -> composer
  -> feedback_logger
  -> END
```

```text
Text2SQL Subgraph

START
  -> intent_and_slot_extractor
  -> slot_normalizer
  -> ambiguity_gate
       -> clarification_result -> END
       -> schema_grounder
  -> query_plan_builder
  -> complexity_classifier
       -> deterministic_template_path
       -> constrained_generation_path
       -> unsupported_result
  -> static_sql_validator
  -> execution_node
  -> result_sanity_checker
  -> candidate_assessor
       -> accept_result -> result_packager -> END
       -> repair_router -> bounded_refiner -> static_sql_validator
       -> fallback_result -> END
```

Main graph는 어떤 sub-agent를 조합할지 결정하고, Text2SQL subgraph는 SQL 관련 책임만
가진다. Text2SQL subgraph 안에서 사용자 답변 문장을 완성하지 않는다. SQL, rows,
근거 metadata, confidence, limitations를 반환하고 최종 표현은 Composer가 담당한다.

### 2.3 왜 하나의 거대한 Agent로 만들지 않는가

한 번의 LLM 호출로 routing, schema 선택, SQL 생성, 검증, 답변 생성을 모두 수행하면
실패 원인을 구분할 수 없다. 특히 실행 가능한 SQL이 질문에 맞는 SQL이라는 보장은 없다.
이 설계는 다음 실패를 서로 다른 단계에서 식별한다.

- 질문 해석 실패
- 필수 slot 누락 또는 대화 맥락 오염
- 잘못된 table/column 선택
- 잘못된 template 또는 query shape 선택
- SQL policy 위반
- SQL 문법/타입/실행 실패
- 실행은 성공했지만 빈 결과 또는 의미적으로 부자연스러운 결과
- 데이터 freshness 또는 coverage 부족

## 3. 두 논문의 조합 방식

| 프로젝트 단계 | TriSQL에서 가져올 개념 | MARS-SQL에서 가져올 개념 | 프로젝트 구현 |
| - | - | - | - |
| Schema Grounding | 질문 중심 table/column 축소 | 별도 Grounding Agent | domain catalog + slot 기반 후보 축소, 애매할 때만 LLM rerank |
| Query Structure | skeleton-first generation | Generation Agent 역할 분리 | query plan과 template ID를 먼저 결정하고 SQL은 나중에 render |
| Complexity | low/medium/high별 전략 | 복잡할수록 multi-turn interaction | deterministic complexity score와 제한된 retry budget |
| Execution Feedback | initial/refined SQL 모두 실행 검증 | Think-Act-Observe | 정책 검증 후 DB 실행 결과를 observation으로 저장 |
| Candidate Validation | 초기/보정 후보 중 품질 선택 | Validation Agent의 trajectory 선택 | 규칙 기반 점수 우선, high에서만 LLM semantic judge 보조 |
| Fallback | 이전 실행 가능 후보로 복귀, 실패 시 escalation | 실행 오류를 다음 turn에 반영 | 오류 taxonomy별 repair, 최대 횟수 후 clarification/limitation |

중요한 차이는 이 프로젝트가 광범위한 cross-domain Text2SQL이 아니라 제한된 FAB
operational query를 먼저 다룬다는 점이다. 따라서 TriSQL의 구조 생성은 대부분 template
선택으로 대체할 수 있다. MARS-SQL의 다중 trajectory는 low/medium 질문에는 사용하지 않고,
high complexity가 활성화된 이후에만 최대 2개 후보로 제한한다.

## 4. 지원 범위와 단계별 자율성

### 4.1 초기 지원 범위

- SC-001 current status
  - FAB 상태
  - process group 상태
  - station/equipment 상태
  - product 상태
  - LOT 상태
- SC-004 trend/comparison은 AutoSched 이력 적재와 데이터 계약 확정 후 추가
- SC-002 diagnosis에서 Text2SQL은 추세와 이상 구간의 수치 근거만 반환
- SC-003 impact에서 Text2SQL은 계산에 필요한 원시 집계만 반환하고 영향 계산은 Impact
  sub-agent가 담당

### 4.1.1 현재 PostgreSQL 적재 상태에 따른 범위 조정

2026-09-01 기준 로컬 PostgreSQL에는 `fab10`부터 `fab13` schema가 있고, 적재된 테이블은
SMT2020 General Data workbook 기반이다. 현재 확인된 table family는 다음과 같다.

- `toolgroups`
- `route_product_*`
- `lotrelease`
- `lotrelease_variable_due_dates`
- `lotrelease_engineering`
- `pm`
- `breakdown`
- `setups`
- `setup_matrix_implant_gas`
- `transport`

아직 `autosched_perf`, `autosched_stngrp`, `autosched_stn`, `autosched_part`,
`autosched_lot` 같은 AutoSched report table은 없다. 따라서 SC-001의 live/current status
질문은 현재 DB만으로 답하면 안 된다. 특히 WIP, current station state, utilization,
queue time, cycle time, on-time rate는 General Data에서 추정하지 않는다.

이 때문에 Text2SQL 초기 범위는 두 층으로 나눈다.

1. `OPERATIONAL_STATUS`
   - 사용자가 "현재 WIP", "설비 상태", "utilization", "cycle time", "on-time"을 묻는 경우
   - `autosched_*` table이 없으면 `data_unavailable`
   - master data fallback 금지
2. `MODEL_MASTER_LOOKUP`
   - 사용자가 공정 route, toolgroup 구성, release plan, PM/Breakdown policy, setup time,
     transport time을 묻는 경우
   - 현재 PostgreSQL General Data로 답변 가능
   - 답변 limitations에 "simulation/model input data 기준"을 포함

즉, 첫 구현에서 SC-001 template만 만들면 사용자 체감 기능이 거의 막힐 수 있다. 먼저
`MODEL_MASTER_LOOKUP` template를 얇게 추가하고, AutoSched report 적재 후
`OPERATIONAL_STATUS` template를 활성화하는 순서가 현실적이다.

### 4.2 생성 모드

`generation_mode`는 세 단계로 고정한다.

1. `TEMPLATE_ONLY`
   - 현재 기본값
   - registry에 등록된 SQL template만 사용
   - LLM은 intent, slot, template 후보 선택에만 관여
2. `CONSTRAINED_PLAN`
   - SC-004에서 활성화
   - 허용된 SELECT dimension, metric, filter, group-by, order-by 조합으로 query AST 생성
   - SQL 문자열은 코드 renderer가 생성
3. `FREE_FORM_READ_ONLY`
   - 초기에는 비활성화
   - 충분한 evaluation set과 human review 기준을 통과한 후 feature flag로 제한적 허용

운영 첫 버전에서 LLM이 SQL 문자열을 직접 만드는 경로는 두지 않는 것이 맞다. template로
표현할 수 없는 질문은 억지로 SQL을 만들지 않고 지원 범위 밖임을 반환한다.

## 5. Text2SQL State 계약

LangGraph state는 자유 형식 `dict` 대신 `TypedDict` 또는 Pydantic model로 정의한다.

```python
class Text2SQLState(TypedDict, total=False):
    request_id: str
    conversation_id: str
    question: str
    normalized_question: str
    conversation_context: list[dict]

    scenario_id: Literal["SC-001", "SC-002", "SC-003", "SC-004", "UNKNOWN"]
    query_type: Literal[
        "status", "master_data_lookup", "release_plan_lookup",
        "diagnosis", "impact", "trend", "unsupported"
    ]
    intent: str
    slots: FabQuerySlots
    missing_slots: list[str]
    ambiguous_slots: list[str]
    clarification_question: str | None

    schema_candidates: list[SchemaCandidate]
    grounded_schema: GroundedSchema
    query_plan: QueryPlan
    complexity: Literal["low", "medium", "high"]
    complexity_reasons: list[str]
    generation_mode: str

    candidates: list[SqlCandidate]
    active_candidate_id: str | None
    observations: list[ExecutionObservation]
    validation: ValidationReport | None

    retry_count: int
    retry_budget: int
    fallback_reason: str | None
    status: Literal[
        "running", "needs_clarification", "succeeded",
        "unsupported", "failed", "data_unavailable"
    ]

    result: Text2SQLResult | None
    confidence: float
    limitations: list[str]
    trace: list[TraceEvent]
```

### 5.1 Slot model

```text
fab_id            fab10 | fab11 | fab12 | fab13
scope_type        fab | process_group | station | product | lot
process_group     normalized process group ID
station           normalized station/tool ID
product           normalized product/part ID
lot_id            normalized LOT ID
metric            wip | lotstarts | lotcomps | utilization | queue_time |
                  cycle_time | ontime_rate | state | yield | ...
data_source_type  operational_report | model_master | release_plan
time_range        start, end, timezone, relative expression
comparison_range  baseline start/end
aggregation       latest | avg | sum | min | max | count | percentile
granularity       current | hourly | daily | weekly
sort              field, direction
top_k             integer
```

각 slot은 값만 저장하지 않고 `source`와 `confidence`를 함께 저장한다.

```text
value: fab10
source: explicit_user | conversation_context | default | alias_match | llm_inference
confidence: 0.0 - 1.0
raw_text: "M10"
```

명시적 사용자 값이 대화 맥락과 충돌하면 항상 최신 사용자 값을 우선한다. default로 채운
값은 최종 limitations에 노출하고, 결과에 큰 영향을 주는 default는 clarification 대상으로
올린다.

### 5.2 QueryPlan model

SQL 문자열보다 먼저 다음 중간 표현을 만든다.

```json
{
  "scenario_id": "SC-001",
  "template_id": "sc001_station_status",
  "source_tables": ["fab12.autosched_stn"],
  "dimensions": ["stn", "report_time", "period"],
  "metrics": ["curstate", "wiplotavg", "util_percent"],
  "filters": [
    {"field": "stn", "operator": "contains", "slot": "station"},
    {"field": "period", "operator": "neq", "value": "WarmUp"}
  ],
  "time_semantics": "latest_report",
  "ordering": ["report_time desc", "source_row_id desc"],
  "row_limit": 20,
  "expected_result_shape": "station_status_rows"
}
```

이 중간 표현이 TriSQL의 SQL skeleton 역할을 한다. LLM이 query plan을 제안하더라도
`template_id`, table, metric, operator는 enum/allowlist로 제한된다. renderer는 slot을
parameter로 binding하고 SQL 문자열을 결정론적으로 생성한다.

## 6. 노드별 상세 플로우

### 6.1 `intent_and_slot_extractor`

입력:

- 현재 질문
- 명시적으로 전달된 `fab`, `line`, `process`
- 최소화된 최근 대화 context
- FAB alias dictionary
- 지원하는 scenario와 metric 목록

출력:

- `scenario_id`, `query_type`, `intent`
- slot 값, source, confidence
- `missing_slots`, `ambiguous_slots`
- 사용자가 실제로 요청한 출력 형태

한 번의 structured LLM call로 query type과 slot을 함께 추출하되, 정규식과 alias match를
먼저 수행한다. `fab10`, `Init_Lot_3_24`, `DE_BE_11`처럼 규칙성이 강한 ID는 deterministic
parser 결과를 LLM 결과보다 우선한다.

### 6.2 `slot_normalizer`

LLM이 만든 문자열을 DB entity와 연결한다.

- `M10`, `FAB 10`, `10번 fab` -> `fab10`
- `Dry Etch`, `드라이에치` -> catalog의 canonical process group
- station/product/lot은 allowlisted lookup query로 존재 여부 확인
- 상대 기간은 KST 기준의 절대 start/end로 변환
- `지난주`는 월요일 00:00부터 일요일 23:59:59로 명시적으로 고정

entity lookup 결과가 0개이면 없는 값으로 확정하지 말고 `UNKNOWN_ENTITY`로 분류한다.
여러 후보가 비슷하면 자동으로 첫 후보를 택하지 않고 clarification으로 보낸다.

### 6.3 `ambiguity_gate`

다음 조건이면 SQL을 생성하지 않는다.

- 필수 `fab_id`가 없고 대화 맥락에도 없음
- `scope_type=lot`인데 `lot_id`가 없음
- 비교 질문인데 기준 기간 또는 대상 기간을 안전하게 유추할 수 없음
- alias lookup 결과가 여러 개이고 우세 후보가 없음
- metric 의미가 서로 다른 두 지표로 해석 가능
- 사용자가 요구한 데이터가 현재 data contract에 없음

clarification은 한 번에 가장 정보 가치가 큰 질문 하나만 한다. 예:

```text
어느 FAB을 조회할까요? 현재 조회 가능한 대상은 fab10, fab11, fab12, fab13입니다.
```

질문이 모호하지만 모든 가능한 해석의 결과가 동일한 경우에는 clarification을 생략할 수
있다. 이 판단은 규칙으로만 한다.

### 6.4 `schema_grounder`

TriSQL의 Question-Guided Schema Selector와 MARS-SQL Grounding Agent를 결합한 단계다.

초기 버전은 다음 순서로 수행한다.

1. `scenario_id + scope_type`으로 table 후보를 deterministic하게 제한
2. `metric registry`로 필요한 column 후보를 추가
3. filter/join/order에 필요한 hidden column을 추가
4. 데이터 타입, PK/FK, column description을 포함한 compact schema context 구성
5. 후보가 둘 이상일 때만 LLM rerank

SC-001 table routing은 다음과 같이 고정한다.

| scope_type | table | 대표 식별 column |
| - | - | - |
| fab | `{fab}.autosched_perf` | `period`, `report_time` |
| process_group | `{fab}.autosched_stngrp` | `stngrp` |
| station | `{fab}.autosched_stn` | `stn` |
| product | `{fab}.autosched_part` | `part` |
| lot | `{fab}.autosched_lot` | `lot` |

현재 PostgreSQL에는 위 `autosched_*` table이 아직 없으므로, `schema_grounder`는 먼저
table existence catalog를 확인한다. operational report table이 없으면 SQL을 만들지 않고
`data_unavailable`을 반환한다.

General Data lookup routing은 별도 catalog로 둔다.

| user intent | table family | 대표 식별 column | 주의점 |
| - | - | - | - |
| toolgroup/area 구성 | `{fab}.toolgroups` | `area`, `toolgroup` | `bacthingtool`처럼 원천 오타성 컬럼을 그대로 사용 |
| route/step 조회 | `{fab}.route_product_*` | `route`, `step`, `area`, `toolgroup` | product/route slot으로 table allowlist를 먼저 확정 |
| release plan 조회 | `{fab}.lotrelease*` | `product_name`, `route_name`, `start_date`, `due_date` | 대용량 table은 필터와 limit 필수 |
| PM policy 조회 | `{fab}.pm` | `pm_event_name`, `type_name`, `pm_type` | 현재 상태가 아니라 정책/입력 조건 |
| breakdown policy 조회 | `{fab}.breakdown` | `down_event_name`, `type_name`, `down_type` | 실제 장애 이력으로 해석 금지 |
| setup time 조회 | `{fab}.setups` | `setup_group_name`, `current_setup`, `new_setup` | `minmal_number_of_runs` 원천 컬럼명 유지 |
| transport time 조회 | `{fab}.transport` | `from_location`, `to_location` | simulation input 기준 |

`route_product_*` table은 dynamic table name을 포함하므로 LLM이 직접 table명을 쓰게 하지
않는다. `schema_grounder`가 information_schema 또는 사전 생성 catalog에서 해당 fab의
route table 목록을 읽고, `Product_3` -> `route_product_3`,
`Product_E3` -> `route_product_e3`처럼 결정론적으로 매핑한다. 매핑 실패 또는 복수 후보는
clarification으로 보낸다.

grounding은 precision보다 recall을 우선한다. 필요한 column 누락은 SQL 자체를 틀리게 만들지만,
관련 column 몇 개가 더 포함되는 것은 prompt 비용만 조금 늘리기 때문이다. 다만 table을
과도하게 포함하면 join hallucination 위험이 있으므로 table은 엄격하게, column은 조금
넓게 선택한다.

### 6.5 `query_plan_builder`

질문에서 바로 SQL을 생성하지 않고 `QueryPlan`을 만든다.

선택 우선순위:

1. 정확히 일치하는 template
2. 같은 scenario의 composable query plan
3. 지원 범위 밖 반환

SC-001에서는 `scope_type`이 template 선택을 결정한다. LLM이 template을 선택할 수는 있지만,
코드의 compatibility table과 required slot validation이 최종 결정을 검증한다.

### 6.6 `complexity_classifier`

별도 BERT model을 학습하지 않고 deterministic score로 시작한다.

```text
base score = 0
+1 comparison 기간 존재
+1 group by 또는 시계열 granularity 존재
+1 table 2개 이상
+1 계산 metric 또는 derived metric 존재
+1 사용자 조건 3개 이상
+2 subquery/window/NOT EXISTS가 필요한 plan
+2 의미적 모호성이 남아 있음
```

- `low`: 0-1점, 단일 template, 단일 table, 현재 값 또는 단순 lookup
- `medium`: 2-3점, 기간 비교, group-by, 파생 지표, 제한된 join
- `high`: 4점 이상, 다중 join/subquery/window 또는 지원 경계에 가까운 질문

구조 복잡도뿐 아니라 semantic complexity를 별도 reason으로 기록한다. high라고 해서 자유
SQL을 자동 허용하지 않는다. 지원 가능한 query plan인지가 먼저다.

### 6.7 `deterministic_template_path`

low complexity의 기본 경로다.

- template ID 검증
- required slot 검증
- slot을 SQL parameter 또는 안전한 identifier mapping으로 binding
- SQL 생성
- candidate 1개 생성

현재 `sql_templates.py`는 문자열 escape를 직접 수행한다. 구현 시 값 slot은 psycopg parameter
binding으로 변경하고, schema/table identifier만 enum mapping으로 렌더링하는 것이 좋다.
SQL validator가 string literal 속 위험 keyword를 무시하더라도, parameter binding은 injection
방어와 query cache 측면에서 더 명확하다.

### 6.8 `constrained_generation_path`

medium/high 질문에서만 사용한다. LLM 출력은 SQL 문자열이 아니라 `QueryPlan` JSON이다.

- 허용된 source table만 선택
- 허용된 metric/dimension만 선택
- join path는 registry에 등록된 관계만 선택
- filter operator는 enum으로 제한
- row limit은 강제
- renderer가 PostgreSQL SQL 생성

high complexity에서 첫 plan이 실패했을 때만 두 번째 plan 후보를 만들 수 있다. 초기 최대
candidate 수는 2개다. MARS-SQL의 best-of-8은 평가 단계에서 효과가 검증되었지만 latency와
token 비용이 크므로 그대로 적용하지 않는다.

### 6.9 `static_sql_validator`

DB 실행 전에 다음 네 층으로 검증한다.

1. QueryPlan validation
   - template, table, column, operator, join path allowlist
2. SQL AST validation
   - `sqlglot` PostgreSQL parser 사용 권장
   - 단일 SELECT/CTE만 허용
   - AST에서 실제 table과 column 추출
3. Policy validation
   - 허용 schema/table/column
   - DDL/DML, locking, function allowlist, system catalog 접근 차단
4. Cost guard
   - row limit
   - timeout
   - 허용되지 않은 cross join 차단
   - 필요 시 `EXPLAIN (FORMAT JSON)` cost threshold

현재 regex 기반 `ReadOnlyQueryExecutor.validate`는 1차 방어로 유지할 수 있지만 최종 방어로는
부족하다. CTE alias, quoted identifier, function, nested query, schema-less CTE를 정확히 다루려면
AST validator를 추가해야 한다. DB 계정 자체도 반드시 read-only여야 한다.

### 6.10 `execution_node`

MARS-SQL의 Action-Observation과 TriSQL의 `Exec(sql, D, timeout)`에 해당한다.

각 실행은 다음 observation을 남긴다.

```text
candidate_id
executed_sql_fingerprint
success
error_class
sanitized_error_message
latency_ms
row_count
columns
null_ratio_by_column
sample_rows (민감정보 정책 범위 내 최소 샘플)
report_time_min/max
truncated
```

원본 DB 오류를 사용자에게 그대로 노출하지 않는다. LLM refiner에는 table/column 후보를
유출하지 않는 정제된 오류와 grounded schema만 전달한다.

### 6.11 `result_sanity_checker`

SQL 실행 성공만으로 정답으로 인정하지 않는다.

- 질문이 단일 수치를 요구했는데 결과가 다수 행인지
- requested metric column이 존재하는지
- 비교 질문인데 baseline/current 두 구간이 모두 있는지
- latest status인데 `report_time`이 지나치게 오래되지 않았는지
- percentage 범위가 0-100인지
- count/WIP가 음수인지
- 특정 entity 질문인데 반환 entity가 다른지
- 빈 결과가 정상적인 0인지, entity mismatch인지, data unavailable인지

빈 결과는 절대로 자동으로 `0`으로 해석하지 않는다. `COUNT(*)` 결과 0과 조회 행 자체가
없는 것은 다르게 처리한다.

### 6.12 `candidate_assessor`

후보 선택은 규칙 기반을 우선한다.

```text
hard reject:
- policy 위반
- 실행 실패
- required output column 누락
- 다른 FAB/entity 반환

quality score 예시:
+0.25 intent/template 일치
+0.20 required slots가 filter에 반영됨
+0.20 실행 성공
+0.15 expected result shape 일치
+0.10 freshness 만족
+0.10 결과 sanity 만족
```

low/medium은 이 점수로 충분하다. high에서 실행 가능한 후보가 2개이고 결과가 다르면 LLM
semantic judge를 보조로 사용한다. judge 입력에는 질문, query plan, grounded schema,
SQL, execution summary를 제공하고, raw chain-of-thought는 저장하거나 전달하지 않는다.

LLM judge가 실행 결과보다 우선할 수 없다. policy 위반 후보를 선택할 수도 없고, required
slot 누락을 덮을 수도 없다.

## 7. 프롬프트 설계

### 7.1 공통 원칙

- 역할별 prompt를 분리한다.
- 자연어 서술 대신 structured output을 강제한다.
- model output은 항상 Pydantic validation을 거친다.
- schema 전체를 넣지 않고 grounded schema만 넣는다.
- DB의 실제 값 샘플은 entity grounding에 필요한 최소 범위만 넣는다.
- prompt에 적힌 안전 규칙만 믿지 않고 코드와 DB 권한으로 다시 강제한다.
- 내부 chain-of-thought를 요구하지 않는다. `decision_reasons`는 짧고 검증 가능한 항목으로
  제한한다.
- prompt에는 버전, 지원 dialect, 기준 timezone을 명시한다.

### 7.2 Intent/slot extraction system prompt

```text
You are the intent and slot parser for a read-only semiconductor FAB analytics system.

Your task is only to classify the request and extract explicit or context-supported slots.
Do not write SQL. Do not answer the user. Do not invent entity IDs, metrics, or dates.

Supported scenarios:
- SC-001: current operational status
- SC-002: diagnosis requiring measured trends
- SC-003: impact-analysis input data
- SC-004: trend or period comparison

Rules:
1. Prefer values explicitly stated in the latest user message.
2. Use conversation context only when the latest message omits the value and the reference is clear.
3. Mark uncertain values as ambiguous instead of guessing.
4. Interpret relative dates in Asia/Seoul, but return normalized absolute ranges.
5. Return only the required JSON schema.
```

user prompt에는 다음 data만 넣는다.

```text
current_time_kst
latest_user_message
explicit_request_metadata
relevant_conversation_context
supported_metric_catalog
known_aliases
```

출력 model 핵심:

```json
{
  "scenario_id": "SC-001",
  "query_type": "status",
  "intent": "station_status",
  "slots": [],
  "missing_slots": [],
  "ambiguous_slots": [],
  "requested_output": "single_value"
}
```

### 7.3 Schema grounding prompt

deterministic routing 후 후보가 여러 개인 경우에만 호출한다.

```text
You select schema elements for one approved FAB query plan.

Select all tables and columns required to answer the question, including columns needed
for filtering, joining, grouping, ordering, freshness checks, and evidence timestamps.
Recall is more important than removing one extra column. Never select an element outside
the candidate catalog. Do not generate SQL.

Return:
- selected_tables
- selected_columns_by_table
- required_relationships
- unresolved_terms
- concise decision_reasons
```

MARS-SQL처럼 table별 LLM 호출을 하면 table 수만큼 latency가 증가한다. 현재는 scenario
catalog가 작으므로 전체 후보를 한 번에 structured call로 처리한다. schema가 커진 뒤에만
table-level parallel grounding을 검토한다.

### 7.4 Query plan prompt

```text
You create a constrained query plan for PostgreSQL. You do not write SQL.

Use only the provided templates, tables, columns, metrics, operators, and relationships.
Every user constraint must appear in the plan or be listed under unresolved_constraints.
Select the simplest plan that fully answers the request.
Do not add explanatory metrics that the user did not request unless they are required to
interpret the requested metric.

If no allowed plan can answer the request, set supported=false and explain the missing
capability using reason codes.
```

반드시 포함할 출력 필드:

```text
supported
template_id
dimensions
metrics
filters
time_semantics
aggregation
grouping
ordering
row_limit
expected_result_shape
unresolved_constraints
```

### 7.5 Repair prompt

repair prompt는 오류 유형별로 다르게 구성한다. 실패한 SQL을 무조건 통째로 다시 쓰라고
하지 않는다.

```text
You repair a constrained FAB query plan after one failed validation or execution attempt.

You may modify only the query-plan fields allowed by the supplied repair policy.
Use the sanitized observation and grounded schema. Do not introduce a new table, column,
metric, or entity. Preserve every confirmed user constraint.

Return one repaired QueryPlan JSON. If the failure cannot be repaired within the allowed
catalog, return repairable=false with a reason code.
```

오류별 허용 수정:

| error class | 허용 수정 | 금지 |
| - | - | - |
| syntax/render error | renderer 또는 plan operator 수정 | schema 확장 |
| undefined column | grounded column alias 재선택 | 비후보 column 생성 |
| type mismatch | cast/date operator 수정 | 의미가 다른 metric 대체 |
| empty result | entity exact/normalized match 재검토 | filter 삭제로 결과 만들기 |
| timeout | granularity 축소, 기간 clarification | 무제한 재실행 |
| semantic mismatch | template/query shape 재선택 | 사용자 조건 누락 |

### 7.6 Semantic validation prompt

```text
You are a strict semantic verifier for a read-only FAB analytics query.

Judge whether the candidate query plan and execution result fully answer the user's
question. Execution success alone is insufficient.

Check:
- every explicit user constraint is represented
- selected metric and aggregation match the question
- entity scope and time range are correct
- result shape can support the requested answer
- no conclusion requires data absent from the result

Return structured JSON only:
verdict = accept | reject | needs_clarification
reason_codes
missing_constraints
confidence
```

MARS-SQL의 `Yes/No` verifier보다 `needs_clarification`을 추가한다. FAB 운영 질문에서는 틀린
후보를 고르는 것보다 필요한 정보를 다시 묻는 것이 안전하기 때문이다.

## 8. Fallback 정책

### 8.1 기본 원칙

- fallback은 답을 반드시 만들어내는 장치가 아니다.
- 이전 후보가 실행 가능하고 의미 검증을 통과했다면 refiner가 만든 새 후보보다 이전 후보를
  우선할 수 있다.
- 재시도는 오류 taxonomy에 따라 달라야 한다.
- 같은 입력으로 같은 LLM call을 반복하지 않는다.
- retry마다 state에 무엇이 바뀌었는지 기록한다.
- 최대 budget을 넘으면 명시적인 clarification, unsupported, data_unavailable, failed 중 하나로
  종료한다.

### 8.2 기본 retry budget

| complexity | 후보 수 | repair 횟수 | LLM judge | 최대 DB 실행 |
| - | -: | -: | - | -: |
| low | 1 | 0 | 사용 안 함 | 1 |
| medium | 1 | 1 | 기본 사용 안 함 | 2 |
| high | 최대 2 | 후보당 1, 전체 최대 2 | 결과 충돌 시 사용 | 4 |

SC-001 template query는 low로 취급한다. 실패하면 LLM repair보다 `data_unavailable` 또는
개발 오류로 분류하는 것이 맞다. 이미 검증한 deterministic template가 실패했다면 prompt를
바꾸는 문제가 아닐 가능성이 높다.

`MODEL_MASTER_LOOKUP` query도 low로 시작한다. 단, release-plan table처럼 row 수가 큰
경우에는 `top_k`, product, route, date range 중 최소 하나의 selective constraint가 있어야
실행한다. selective constraint가 없으면 broad query를 실행하지 않고 clarification으로 보낸다.

### 8.3 오류별 전이

| 상황 | 다음 전이 | 사용자 결과 |
| - | - | - |
| 필수 slot 누락 | clarification | 한 가지 구체 질문 |
| entity 후보 여러 개 | clarification | 후보를 구분할 질문 |
| 지원하지 않는 metric/table | unsupported | 현재 가능한 범위와 누락 capability |
| AutoSched table 미적재 | data_unavailable | General Data로 추정하지 않고 coverage 한계 |
| live status 질문 + General Data만 존재 | data_unavailable | 현재 상태가 아니라 model input만 적재됐다고 설명 |
| master-data 질문 + General Data 존재 | 정상 실행 | simulation/model input 기준임을 limitation에 포함 |
| route_product table 복수 후보 | clarification | product/route 선택 질문 |
| release-plan broad scan 위험 | clarification | product, route, 기간 중 하나 요청 |
| policy validation 실패 | candidate 폐기, 보안 로그 | SQL/내부 오류 비노출 |
| syntax/type 오류, medium/high | 한 번 repair | 실패 시 명시적 실패 |
| timeout | 더 좁은 기간 요청 또는 제한된 plan | 자동 무제한 재시도 금지 |
| empty rows + entity 미확인 | entity 재검증 후 clarification | 0이라고 답하지 않음 |
| empty rows + valid count result | 정상 결과 | 0과 기준 시각 표시 |
| 두 executable 후보 결과 동일 | 단순한 plan 선택 | 정상 결과 |
| 두 executable 후보 결과 충돌 | semantic judge, 그래도 불명확하면 clarification | 추측 금지 |
| refiner가 기존 성공 후보를 악화 | 기존 후보로 rollback | 기존 근거 사용 |
| DB 연결 실패 | circuit breaker/data_unavailable | 잠시 조회 불가 표시 |

### 8.4 Circuit breaker

DB 연결 오류나 timeout이 연속 발생하면 같은 요청 안에서만 retry하지 말고 서비스 차원의
circuit breaker를 둔다. open 상태에서는 LLM 호출과 SQL 생성을 생략하고 즉시
`data_unavailable`을 반환하여 비용과 DB 부하를 줄인다.

## 9. 결과 계약

Text2SQL subgraph의 최종 반환은 자연어 답변이 아니라 다음 구조다.

```json
{
  "status": "succeeded",
  "query_type": "status",
  "scenario_id": "SC-001",
  "intent": "station_status",
  "resolved_slots": {},
  "query_plan": {},
  "template_id": "sc001_station_status",
  "sql": "...",
  "parameters": {},
  "columns": [],
  "rows": [],
  "row_count": 1,
  "evidence": [
    {
      "source_type": "database",
      "schema": "fab12",
      "table": "autosched_stn",
      "report_time": "...",
      "query_fingerprint": "..."
    }
  ],
  "confidence": 0.93,
  "limitations": [],
  "latency_ms": 120
}
```

SQL parameter에는 민감 값이 있을 수 있으므로 production 로그에는 redaction 정책을 적용한다.
사용자 응답에 SQL을 노출하는 것은 development mode 또는 권한 있는 사용자로 제한한다.

### 9.1 Confidence 계산

LLM self-reported confidence를 그대로 사용하지 않는다.

```text
0.25 intent/slot completeness
0.20 entity grounding certainty
0.20 template/query-plan compatibility
0.15 static validation
0.10 execution and result-shape validation
0.10 freshness/data coverage
```

hard failure가 있으면 점수와 무관하게 accept할 수 없다. default 또는 대화 맥락에서 가져온
핵심 slot이 있으면 confidence 상한을 낮춘다.

## 10. Main FAB Graph와의 연결

### 10.1 SC-001

```text
router(status)
  -> Text2SQL(template-only)
  -> evidence_merger
  -> reflection(metric/entity/freshness 확인)
  -> composer
```

### 10.2 SC-002

```text
router(diagnosis)
  -> planner
  -> Text2SQL(trend/anomaly evidence)
  -> RAG(process knowledge)
  -> optional CaseSearch
  -> evidence_merger
  -> reflection(DB 사실과 원인 가설을 구분)
  -> composer
```

Text2SQL 결과가 없으면 RAG 지식만으로 “실제 원인”이라고 단정하지 않는다. 문서상 가능한
원인 후보라고 표현한다.

### 10.3 SC-003

```text
router(impact)
  -> Text2SQL(required aggregates)
  -> Impact(deterministic calculation)
  -> reflection(calculation formula and baseline 확인)
  -> composer
```

LLM은 영향 수치를 직접 계산하지 않는다. 계산식과 입력 데이터 계약을 Impact sub-agent의
코드로 고정한다.

### 10.4 SC-004

```text
router(trend)
  -> Text2SQL(constrained plan)
  -> Visualization(optional)
  -> reflection(period, unit, percentage-point 확인)
  -> composer
```

## 11. 대화 맥락 처리

Text2SQL subgraph에 전체 대화 기록을 넣지 않는다. `context_resolver`가 다음 형태의 compact
context를 만든다.

```text
last_confirmed_fab
last_confirmed_scope_type/entity
last_metric
last_time_range
last_query_result_reference
```

후속 질문 “그럼 지난주는?”은 직전 metric/entity를 사용할 수 있다. 다만 새 질문에 명시된
값이 있으면 직전 context를 덮어쓴다. 오래된 context에는 TTL 또는 turn limit를 적용한다.
사용자가 “그거”, “이 공정”처럼 참조했는데 후보가 둘 이상이면 clarification한다.

## 12. 코드 구조 제안

```text
app/
  agents/
    graph.py                  # Main FAB graph
    state.py                  # Main state models
    supervisor.py
  sub_agent/
    text2sql/
      __init__.py
      graph.py                # Text2SQL subgraph builder
      state.py                # Text2SQLState and Pydantic models
      nodes/
        intent_slots.py
        normalize.py
        clarify.py
        ground_schema.py
        build_plan.py
        classify_complexity.py
        render_sql.py
        validate_sql.py
        execute.py
        assess_result.py
        refine.py
        package_result.py
      prompts.py              # versioned prompt templates
      catalog.py              # metric/entity/schema catalog
      templates/
        sc001.py
        sc004.py
      renderer.py
      policy.py
      errors.py
  db/
    read_only.py
    sql_ast.py
```

현재 `app/sub_agent/text2sql.py`와 `sql_templates.py`는 기능이 커지기 전에 package로
전환한다. 외부에서는 `build_text2sql_graph()`와 `Text2SQLResult`만 사용하도록 공개 API를
좁힌다.

## 13. 관찰 가능성과 로그

각 요청에 다음을 기록한다.

- request/conversation ID
- graph node와 edge transition
- scenario, intent, slot source/confidence
- grounded tables/columns
- query plan, template ID, prompt version, model ID
- SQL fingerprint와 parameter redaction 결과
- validation reason code
- 실행 latency, row count, timeout 여부
- retry/fallback 횟수와 원인
- final status, confidence, limitations

개인정보나 생산 민감 데이터가 포함된 row sample, LOT ID, 자유 텍스트는 별도 redaction
정책을 적용한다. LangSmith tracing을 켤 때도 동일한 정책을 적용해야 한다.

## 14. 테스트 및 평가 계획

### 14.1 단위 테스트

- alias와 slot normalization
- 필수 slot/ambiguity gate
- scenario + scope -> template mapping
- 모든 template의 AST/policy validation
- 값 parameter binding과 identifier allowlist
- complexity score 경계값
- 오류 taxonomy와 fallback edge
- empty result와 count zero 구분
- confidence 상한과 limitation 생성

### 14.2 Golden set

시나리오별 자연어 변형을 만든다.

- 정상 질문
- 띄어쓰기/한영 혼합/alias
- 필수 정보 누락
- 존재하지 않는 entity
- 대화형 follow-up
- 모순된 context
- prompt injection 문구가 포함된 질문
- 지원하지 않는 분석 요청
- 결과 없음, stale data, DB timeout

각 case의 gold는 SQL 문자열 하나가 아니라 다음으로 구성한다.

```text
expected scenario/intent
expected normalized slots
allowed template/query plan
required filters and metrics
expected clarification or failure status
result invariants
```

같은 결과를 내는 SQL이 여러 개일 수 있으므로 exact string match보다 plan과 execution
semantics를 평가한다.

### 14.3 핵심 지표

- intent accuracy
- required slot precision/recall
- schema/table recall
- template/query-plan accuracy
- policy violation rate: 목표 0%
- execution success rate
- execution correctness 또는 result invariant pass rate
- clarification precision
- false-zero rate: 목표 0%
- fallback recovery rate
- p50/p95 latency, LLM call count, DB execution count

### 14.4 단계별 release gate

1. SC-001 template-only
   - policy violation 0%
   - template selection 95% 이상
   - 명시적 entity 질문의 wrong-entity result 0%
2. SC-004 constrained-plan
   - 기간 normalization 95% 이상
   - comparison result invariant 95% 이상
3. Medium repair
   - no-repair baseline보다 execution correctness 개선
   - 성공 후보를 악화시키는 regression rate 측정
4. Free-form 검토
   - 별도 feature flag와 human approval
   - 위 단계의 기준을 만족하기 전에는 활성화하지 않음

## 15. 구현 순서

### Phase A. 계약과 결정론적 경로

1. `FabQuerySlots`, `QueryPlan`, `SqlCandidate`, `ExecutionObservation`,
   `Text2SQLResult` model 정의
2. SC-001 metric/entity/schema catalog 작성
3. 기존 SC-001 template를 parameter binding 방식으로 전환
4. template selector와 ambiguity gate 구현
5. AST + policy validator 보강
6. 실제 AutoSched staging table 기반 integration test

### Phase B. Text2SQL LangGraph subgraph

1. state와 node interface 구현
2. deterministic nodes 연결
3. retry budget와 fallback edge 구현
4. checkpoint와 trace metadata 연결
5. `ChatService`에서 subgraph 호출

이 단계까지는 LLM 없이도 다수 SC-001 질문을 처리할 수 있어야 한다.

### Phase C. Structured LLM 보조

1. intent/slot structured prompt
2. ambiguous schema 후보의 grounding prompt
3. constrained QueryPlan prompt
4. semantic verifier prompt
5. prompt version 및 eval logging

LLM이 없어도 deterministic parser로 처리 가능한 질문은 LLM call을 생략한다.

### Phase D. SC-004와 제한된 refinement

1. trend data contract 확정
2. time range/comparison normalizer
3. composable query renderer
4. medium complexity repair 1회
5. conflicting candidate semantic validation

### Phase E. 고도화 판단

운영 trace와 golden set에서 반복되는 실패가 충분히 모인 후에만 다음을 검토한다.

- learned schema reranker
- multiple candidate rollout 확대
- fine-tuned validator
- offline preference/SFT 또는 RL
- 제한적 free-form SQL

RL은 첫 구현의 전제가 아니라, prompt와 규칙으로 해결되지 않는 반복 실패가 데이터로 확인된
후의 최적화 단계다.

## 16. 최종 의사결정 요약

- 전체 orchestration과 fallback은 LangGraph로 구현한다.
- `SKILL.md`는 개발 workflow 문서이며 운영 agent의 runtime prompt loader로 사용하지 않는다.
- Text2SQL은 독립 subgraph로 만들고 Main FAB Graph에 node처럼 연결한다.
- 두 논문의 공통 3단계는 `Grounding -> Structure/Plan -> Validation`으로 통합한다.
- TriSQL의 complexity-aware 전략으로 retry와 candidate budget을 결정한다.
- MARS-SQL의 execution observation과 candidate selection을 가져오되 초기에는 single candidate
  + bounded repair를 기본으로 한다.
- SC-001은 template-only로 시작하고 SQL 문자열 생성에 LLM을 사용하지 않는다.
- SC-004부터 constrained QueryPlan을 도입한다.
- 실행 성공과 의미 정확성을 별도로 검증한다.
- fallback은 무조건 답을 생성하는 것이 아니라 clarification, rollback, unsupported,
  data_unavailable을 명시적으로 선택하는 정책이다.
- 자유 SQL과 RL은 evaluation data가 쌓인 뒤의 선택 사항이며 초기 뼈대에는 포함하지 않는다.
