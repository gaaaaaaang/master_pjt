# FAB AI Assistant 개발 TODO

## 개발 원칙

- Agent 구현보다 DB 스키마와 조회 API를 먼저 고정한다.
- Text2SQL은 LLM API가 SQL을 직접 생성하되, schema/table allowlist와 read-only validator를
  통과한 쿼리만 실행한다.
- RAG는 문서 chunk, metadata, Milvus collection schema를 먼저 고정한 뒤 붙인다.
- 모든 DB 접근은 read-only, timeout, row limit, allowlist 기반으로 제한한다.
- 첫 end-to-end 목표는 SC-001 현재 상태 조회이다.

## Phase 1. 데이터 적재 검증

- MySQL 또는 PostgreSQL에 적재된 SMT2020 테이블 목록을 확인한다.
- 각 테이블 row count를 확인한다.
- 주요 컬럼 샘플을 확인한다.
- LOT, 공정, 설비, 제품, 시간, WIP, Queue Time, 수율 관련 컬럼을 식별한다.
- SC-001, SC-002, SC-004에 필요한 최소 테이블을 먼저 고정한다.
- 현재 PostgreSQL에는 General Data workbook 기반 테이블만 적재되어 있으므로,
  live/current status 질문과 model/master-data lookup 질문을 분리한다.

## Phase 2. DB Access Layer 구현

- DB 연결 설정을 `.env` 기반으로 구성한다.
- read-only query executor를 구현한다.
- SQL injection 방지를 위한 검증 로직을 추가한다.
- 허용 테이블/컬럼 allowlist를 구성한다.
- query timeout과 row limit 기본값을 설정한다.

## Phase 3. 기본 조회 API 구현

- `/api/fab/master/toolgroups` 또는 동등한 내부 조회 함수를 먼저 구현한다.
- `/api/fab/master/routes` 또는 동등한 내부 조회 함수를 먼저 구현한다.
- `/api/fab/master/release-plan` 또는 동등한 내부 조회 함수를 먼저 구현한다.
- `/api/fab/status` endpoint를 구현한다.
- `/api/fab/trend` endpoint를 구현한다.
- `/api/fab/equipment/{id}` endpoint를 구현한다.
- LLM 없이 직접 SQL로 먼저 조회 결과를 검증한다.
- 프론트엔드와 백엔드 연결이 되는지 확인한다.

비고:

- `/api/fab/status`는 AutoSched `.rep` 적재 전에는 `data_unavailable`을 반환한다.
- General Data 기반 조회는 "현재 상태"가 아니라 "simulation/model input 기준"으로
  응답해야 한다.

## Phase 4. Text2SQL sub-agent 구현

- 자연어 질문에서 `query_type`을 분류한다.
- `status`, `master_data_lookup`, `release_plan_lookup`을 구분한다.
- 설비명, 공정명, 제품명, LOT ID, 기간, 지표를 slot으로 추출한다.
- deterministic parser와 LLM structured output으로 query_type/slot/schema context를 구성한다.
- LLM API를 호출해 PostgreSQL SELECT/WITH SQL을 직접 생성한다.
- LLM SQL은 schema-qualified allowlist와 read-only validator를 통과해야만 반환/실행한다.
- AutoSched 적재 후 SC-001 현재 상태 조회는 LLM이 `autosched_*` catalog 기반 SQL을 생성한다.
- SQL validation을 통해 read-only 쿼리만 실행되도록 제한한다.

## Phase 5. RAG + Milvus 구현

- 반도체 공정 이론 자료와 운영 문서를 텍스트로 추출한다.
- 문서를 500~800 token 단위로 chunking한다.
- source, process_id, equipment_id, scenario_id 등 metadata를 부여한다.
- embedding model은 `text-embedding-3-large`를 기본으로 사용한다.
- Vector DB는 Milvus를 사용한다.
- `retrieve_knowledge` sub-agent를 구현한다.
- SC-002 원인 진단 답변에서 SQL 근거와 RAG 근거를 결합한다.

## Phase 6. LangGraph orchestration 구현

- `InputNode`를 구현한다.
- `ContextNode`를 구현해 이전 대화 맥락을 조회한다.
- `RouterNode`를 구현해 query type을 분류한다.
- `SlotExtractionNode`를 구현한다.
- `PlannerNode`를 구현해 `query_type`, `missing_slots`, `selected_sub_agents`,
  `execution_steps`를 포함한 실행 계획을 생성한다.
- `SupervisorNode`를 구현해 Planner 결과에 따라 sub-agent 실행 순서, 재시도,
  clarification, data unavailable, unsupported 분기를 제어한다.
- `Text2SQLNode`, `RAGNode`, `ImpactNode`, `CaseSearchNode`, `VisualizationNode`를 조건부 실행한다.
- `ReflectionNode`에서 근거성, 질문 의도 일치, limitation 노출, 안전 경계 위반
  여부를 검증한다.
- `ComposerNode`에서 Reflection 결과를 반영해 최종 답변을 생성한다.
- `FeedbackNode`에서 사용자 평가를 저장한다.

### Planner 구현 기준

- 입력 질문과 slot 추출 결과를 기반으로 `status`, `master_data_lookup`,
  `release_plan_lookup`, `diagnosis`, `impact`, `trend`, `unsupported`를 구분한다.
- 실행 가능한 sub-agent 목록을 `selected_sub_agents`로 반환한다.
- 필수 slot이 부족하면 SQL 생성 전 `missing_slots`와 clarification 질문을 반환한다.
- AutoSched table이 필요한 live/current status는 loader 완료 전 `data_unavailable`
  경로로 계획한다.

### Supervisor 구현 기준

- Planner의 `selected_sub_agents` 순서대로 Text2SQL, RAG, Impact, CaseSearch,
  Visualization을 조건부 실행한다.
- sub-agent 결과가 `needs_clarification`, `data_unavailable`, `unsupported`,
  `failed`, `succeeded` 중 무엇인지에 따라 다음 실행 여부를 결정한다.
- SC-002는 Text2SQL 근거와 RAG 근거를 모두 시도하되, 한쪽 근거가 없으면 최종 답변에
  limitation을 포함한다.
- 자동 생산 조치나 설비 제어 요청은 실행하지 않고 안전 경계 응답으로 종료한다.

### Self-reflection 구현 기준

- SQL 결과 없이 실제 수치나 현재 상태를 단정하지 않는다.
- General Data 기반 조회를 live/current factory state처럼 표현하지 않는다.
- RAG 근거만으로 실제 원인을 확정하지 않는다.
- 영향도 계산 답변에는 입력 데이터, 계산식, 한계를 포함한다.
- 위험하거나 권한 밖인 요청은 거절 또는 사람 검토 권고 응답으로 처리한다.
- 최종 답변에 근거와 limitation이 누락되면 Composer로 보정 요청을 반환한다.

## Phase 7. 프론트엔드 연결

- 채팅 UI에서 `/api/chat`을 호출한다.
- 답변, 근거, 한계, 차트 데이터를 표시한다.
- 개발 모드에서는 `query_type`, `selected_sub_agents`, SQL, latency를 확인할 수 있게 한다.
- 정보 부족 시 clarification 질문을 표시한다.

## Phase 8. 테스트와 검증

- SC-001 현재 상태 조회 테스트를 작성한다.
- SC-002 원인 진단 테스트를 작성한다.
- SC-004 추세/비교 테스트를 작성한다.
- 정보 부족 질문 테스트를 작성한다.
- 위험하거나 권한 밖인 요청에 대한 거절/권고 응답을 테스트한다.
- API 단위 테스트와 핵심 sub-agent 단위 테스트를 작성한다.

## 바로 다음 작업

1. [x] 적재된 DB 테이블 목록 확인
2. [x] 각 테이블 row count 확인
3. [x] 주요 컬럼 샘플 확인
4. [x] SC-001 현재 상태 조회용 SQL 3~5개 작성
5. [x] SQL을 FastAPI endpoint로 감싸기
6. [x] General Data 기반 master/release lookup schema catalog 작성
7. [x] AutoSched `.rep` PostgreSQL loader 작성
8. [x] Planner structured-output LLM 실행 계획 구현
9. [x] Supervisor structured-output LLM 실행 승인 및 sub-agent 제어 구현
10. [x] Self-reflection 검증 기준 및 sub-agent 구현
11. [x] LangGraph node/state 연결
12. [x] SC-001 end-to-end 테스트 확장
13. [x] fab10 lotrelease 날짜별 route 건수 Text2SQL + line chart E2E 연결

현재 LangGraph stream 경로:

- `planner(LLM) -> supervisor(LLM) -> text2sql(LLM) -> rag -> case_search -> impact -> visualization -> reflection(LLM) -> composer(LLM)`
- Planner가 선택하지 않은 sub-agent node는 실행 결과를 만들지 않고 통과한다.
- `/api/chat/stream`은 Planner plan, Text2SQL query plan/SQL/result, chart spec,
  reflection, final response를 SSE로 순차 전송한다.
- `lotrelease` 날짜별 건수는 named SQL template가 아니라 allowlisted semantic query
  plan(`source_tables`, `select_items`, `filters`, `group_by`, `order_by`, `aggregation`)에서
  SQL을 렌더링한다.
- `apps/assistant/scripts/load_autosched_postgres_reports.py`는 UTF-16 tab-delimited AutoSched `.rep`를
  `{fab}.autosched_*` staging table로 적재한다.
- Text2SQL은 Azure OpenAI 호환 `chat/completions` API를 호출해 SQL을 직접 생성한다.
- `sql_templates.py`는 legacy 검증 유틸로 남아 있지만, `app/sub_agent/text2sql.py` 런타임 경로에서는
  template 함수를 호출하지 않는다.

남은 고도화:

- Planner/Supervisor는 structured output LLM으로 intent와 실행 경로를 결정하고, Text2SQL의
  deterministic slot parser는 LLM SQL 호출 전 안전 전처리로 유지한다.
- `/api/chat`과 `/api/chat/stream`은 모두 같은 LangGraph를 실행하며 pattern router fallback은 없다.
- `lotrelease` 외 AutoSched operational report, PM, breakdown 대표 질문 fixture를 추가했고,
  schema context에는 metric/date catalog와 parsed slot을 함께 제공한다.
- `lotrelease`의 `최근`, `일별`, `날짜 기준` 질문은 `start_date`와 `due_date` 중 기준이
  명확하지 않으면 SQL 생성 전 clarification으로 중단한다.
- `/api/chat/stream`은 elapsed time, retry budget, timeout, disconnect/cancellation telemetry를
  SSE event data에 포함한다.
- RAG, CaseSearch, Impact는 실제 저장소/모델 연결 전까지 limitation을 반환한다.

최근 추가된 검증 fixture:

- `apps/assistant/tests/fixtures/text2sql_fab10_eval.json`: fab10 Text2SQL 대표 질문 47개.
- `apps/assistant/tests/fixtures/scenario_acceptance_questions.json`: SC-001~SC-004 acceptance 질문 18개.

비고:

- 5번은 일정상 endpoint 구현을 바로 진행하지 않고, Text2SQL schema catalog와 LLM direct SQL 호출을
  먼저 구현하는 방향으로 대체 결정했다.
- SC-001 endpoint는 AutoSched `.rep` 적재와 LLM direct SQL validation 이후 연결한다.
- 현재 LLM API key 인증 실패 시 Text2SQL은 `failed`로 종료하고 SQL을 생성하지 않는다.
- Planner, Supervisor, Self-reflection, Composer는 모두 Azure Chat Completions를 호출한다.
