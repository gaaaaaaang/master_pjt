# FAB AI Assistant 개발 TODO

## 개발 원칙

- Agent 구현보다 DB 스키마와 조회 API를 먼저 고정한다.
- 자유 SQL 생성보다 SQL template 기반 Text2SQL부터 구현한다.
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
- `slots` 기반으로 SQL template을 선택한다.
- General Data lookup SQL template을 먼저 구현한다.
- AutoSched 적재 후 SC-001 현재 상태 조회 SQL template을 활성화한다.
- SC-004 추세/비교 SQL template을 다음으로 구현한다.
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
- `PlannerNode`를 구현해 실행 계획을 생성한다.
- `SupervisorNode`를 구현해 sub-agent 실행 순서와 재시도를 제어한다.
- `Text2SQLNode`, `RAGNode`, `ImpactNode`, `CaseSearchNode`, `VisualizationNode`를 조건부 실행한다.
- `ReflectionNode`에서 근거성, 일관성, 안전 경계를 검증한다.
- `ComposerNode`에서 최종 답변을 생성한다.
- `FeedbackNode`에서 사용자 평가를 저장한다.

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
6. [ ] General Data 기반 master/release lookup SQL template 작성
7. [ ] AutoSched `.rep` PostgreSQL loader 작성

비고:

- 5번은 일정상 endpoint 구현을 바로 진행하지 않고, Text2SQL template selection을 먼저 구현하는 방향으로 대체 결정했다.
- SC-001 endpoint는 AutoSched `.rep` 적재와 Text2SQL template selection 이후 연결한다.
- 현재 DB에는 `autosched_*` 테이블이 없으므로, 기존 SC-001 status template은 실행 전
  table availability check가 필요하다.
