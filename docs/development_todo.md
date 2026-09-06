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

- 각 선택 agent 실행 직후 `agent_name`, `agent_intent`, `planner_plan`, `agent_output`,
  `success_criteria`, `evidence`, `limitations` 공통 계약으로 결과를 검증한다.
- agent별 검증 결과는 `agent_reflections`에 실행 순서대로 누적한다.
- `pass`가 아닌 결과는 `supervisor_reviews`에 추가해 최종 Reflection/Composer와 API로 전달한다.
- `needs_supervisor_review` 결과는 post-execution Supervisor가 `continue`,
  `retry_same_agent`, `replan`, `alternate_agent` 중 하나로 판정한다.
- 전체 retry 2회, agent별 retry 1회, replan 1회, alternate 1회 budget을 넘지 않는다.
- `data_unavailable`, `unsupported`, `needs_clarification`, `skipped`는 같은 agent를 재시도하지 않는다.
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
14. [x] Agent reflection 기반 bounded retry/replan/alternate routing 연결

## 최초 서비스 기획 대비 현황 점검 (2026-09-03)

전체 방향인 `Planner -> Supervisor -> specialist agent -> 검증 -> Composer`와 read-only,
근거/한계 노출, 자동 설비 제어 금지 원칙은 유지되고 있다. 다만 orchestration과 trace 기능이
실제 시나리오 데이터 capability보다 먼저 고도화되어, graph가 실행되더라도 일부 agent는 아직
실질적인 업무 답변을 만들지 못한다.

| 기획 항목 | 현재 상태 | 판단 및 후속 작업 |
| - | - | - |
| SC-001 현재 상태 조회 | 부분 완료 | AutoSched loader/catalog/Text2SQL 경로는 있으나 실제 적재 DB 기반 대표 질문 정확도와 freshness 검증이 release gate로 남아 있다. |
| 공정 지식 질의 | 부분 완료 | local/Milvus RAG 경로는 있으나 운영 corpus 적재 상태와 검색 품질 KPI가 고정되지 않았다. |
| SC-002 원인 진단 | 부분 완료 | Text2SQL + RAG 조합은 실행되지만 CaseSearch가 placeholder이고 실제 수치와 원인 후보의 결합 정확도 평가가 없다. |
| SC-003 영향도 | 미완료 | 최초 PoC 우선순위에서는 제외됐지만 graph 뼈대가 먼저 들어갔다. Impact 계산과 해석은 아직 placeholder다. |
| SC-004 추세/비교 | 부분 완료 | lotrelease 날짜별 건수 + line chart는 동작하지만 최초 기획의 수율/WIP 기간 비교 범위는 충족하지 못한다. |
| 대응 추천 질의 | 부분 완료 | incident playbook RAG와 안전 경계는 있으나 상황별 retrieval 평가와 담당자 검토 흐름이 없다. |
| 후속/맥락 질의 | 미완료 | conversation_id는 있으나 대화/SQL history 저장과 재호출 context가 없다. |
| 사용자 피드백 반영 | 미완료 | `/feedback`은 persistence placeholder이며 few-shot/reflection 개선 데이터로 연결되지 않는다. |
| 4개 기본 FAB 조회 API | 미완료 | chat/Text2SQL 내부 경로는 있으나 기획한 `/api/fab/*` direct endpoint 계약은 구현되지 않았다. |
| KPI 측정 | 미완료 | fixture는 있으나 SQL 정확도 75%, 복합 추론 70%, routing 90%, latency 기준의 자동 산출이 없다. |
| Agent별 reflection | 완료(범위 확장) | 최초에는 복합 질의 중심이었으나 현재는 선택된 모든 agent 결과를 공통 계약으로 검증한다. |

명시적인 기획 변경:

- 원안의 MySQL 단일 제약을 PostgreSQL schema 기반으로 변경했다.
- Text2SQL 설계 문서의 `SC-001 template-first / LLM optional`보다 LLM direct SQL을 먼저 활성화했다.
  allowlist/read-only validation은 유지하지만, LLM 장애 시 deterministic SC-001 경로가 없다는 차이가 있다.
- 실제 Impact/CaseSearch/feedback capability보다 Planner/Supervisor/SSE trace를 먼저 구현했다.

사용자 결정에 따른 실행 순서:

1. [x] 현재 진행 중인 오케스트레이션 확장을 완료한다.
   - Planner의 `execution_steps`를 기준으로 다음 agent를 직접 dispatch하고, 선택되지 않은
     node를 고정 순서로 통과하는 구조를 제거한다.
   - 최종 Reflection 결과를 `compose`, `replan`, `retry_target`, `human_review` 종료 상태로
     분기하되 기존 retry/replan budget을 공유한다.
   - 동일 agent/plan 반복, alternate 순환, budget 초과를 graph invariant 테스트로 차단한다.
   - 최종 응답에 `termination_reason`, 최종 선택 action, 전체 recovery trace를 노출한다.
2. [x] SC-001 실제 AutoSched DB golden query와 freshness/정확도 release gate를 만든다.
   - [x] fab10 원본 report 전체 row count와 source snapshot time을 고정한다.
   - [x] fab/process-group/station/product/lot 대표 golden query와 기대값을 fixture로 만든다.
   - [x] read-only release gate 스크립트와 단위 테스트를 구현한다.
   - [x] fab10 `autosched_*` 샘플 테이블을 전체 원본으로 재적재하고 release gate를 통과시킨다.
   - [x] 기존 시나리오와 다른 공정/설비/제품/lot/과거 기간 robustness query를 추가한다.
3. [ ] SC-002용 운영 RAG corpus와 retrieval 평가를 고정하고 CaseSearch의 데이터 소스를 연결한다.
4. [ ] SC-004 수율/WIP 기간 비교 data contract와 fixture를 추가한다.
5. [ ] 대화 history와 feedback persistence를 구현한다.
6. [ ] SC-003 Impact 계산식/기준값 계약은 PoC 핵심 범위 완료 후 구현한다.
7. [ ] KPI 자동 리포트를 추가해 최초 목표 수치를 실제로 측정한다.

현재 LangGraph stream 경로:

- `planner(LLM) -> supervisor(LLM) -> selected agent -> agent reflection -> post-execution supervisor(LLM)`
- post-execution supervisor는 `continue -> 다음 agent`, `retry_same_agent -> 같은 agent`,
  `replan -> planner`, `alternate_agent -> 호환 agent` 조건부 edge를 선택한다.
- 모든 실행이 끝나면 `reflection(LLM) -> composer(LLM)`로 종료한다.
- 최종 Reflection은 `compose`, `replan`, `retry_target`, `human_review` 중 하나를 선택한다.
- Dispatcher는 Planner의 `execution_steps` cursor를 따라 선택된 agent만 직접 호출한다.
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
