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
- PostgreSQL에는 General Data와 AutoSched report 테이블이 적재되어 있으며, AutoSched snapshot
  기반 상태 조회와 model/master-data lookup을 데이터 소스 계약으로 구분한다.

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

- `/api/fab/status`는 AutoSched `.rep` snapshot 범위 안에서 조회하며, 직접 지원하지 않는
  Queue Time 지표는 `data_unavailable`과 필요한 데이터 계약을 반환한다.
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
| SC-001 현재 상태 조회 | local release gate 완료 | AutoSched loader/catalog/golden query와 deterministic current-snapshot SQL을 연결했다. 전체 graph의 Azure Composer 포함 평가는 별도다. |
| 공정 지식 질의 | local gate 완료 | local RAG 10문항 Recall@3/MRR은 1.0이다. Milvus embedding 경로 평가는 별도다. |
| SC-002 원인 진단 | local contract 완료 | SQL 관측값, 문서 기반 후보, 유사 사례를 `diagnosis_synthesis`로 분리하고 결론을 `candidate_only`로 제한한다. 실제 verified incident 연동은 남아 있다. |
| SC-003 영향도 | local contract 완료 | 4문항 deterministic gate에서 계산 2건과 근거 부족 거절 2건을 검증한다. 승인되지 않은 Queue Time/output, downtime/output, cycle/ontime 인과값은 생성하지 않는다. |
| SC-004 추세/비교 | local contract 완료 | lotrelease line chart, 제품/Period 다중 metric grouped bar, 명시 날짜 범위와 최근 N일 집계를 지원한다. 임의 자연어 월/분기 범위는 남아 있다. |
| 대응 추천 질의 | 부분 완료 | incident playbook RAG와 안전 경계는 있으나 상황별 retrieval 평가와 담당자 검토 흐름이 없다. |
| 후속/맥락 질의 | 구현/실측 필요 | SQLite history와 FAB/process/product/route/equipment/date context 복원을 구현했다. 8턴 stateful Azure 평가는 남아 있다. |
| 사용자 피드백 반영 | 구현/운영 검증 필요 | `/feedback`을 conversation snapshot과 assistant turn에 저장하고 다음 agent history로 전달한다. 운영 데이터 retention 정책은 남아 있다. |
| 4개 기본 FAB 조회 API | 미완료 | chat/Text2SQL 내부 경로는 있으나 기획한 `/api/fab/*` direct endpoint 계약은 구현되지 않았다. |
| KPI 측정 | 미완료 | fixture는 있으나 SQL 정확도 75%, 복합 추론 70%, routing 90%, latency 기준의 자동 산출이 없다. |
| Agent별 reflection | 완료(범위 확장) | 최초에는 복합 질의 중심이었으나 현재는 선택된 모든 agent 결과를 공통 계약으로 검증한다. |

명시적인 기획 변경:

- 원안의 MySQL 단일 제약을 PostgreSQL schema 기반으로 변경했다.
- Text2SQL은 초기에 LLM direct SQL을 먼저 활성화했으나, 현재는 62문항 fixture의
  status/master/release/trend/guard를 deterministic query contract로 우선 처리한다.
  contract 미지원 질의만 allowlisted schema context를 사용한 LLM direct SQL로 내려간다.
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
3. [ ] SC-002용 운영 RAG corpus와 retrieval 평가를 고정하고 CaseSearch의 실제 데이터 소스를 연결한다.
   - [x] CaseSearch를 provenance 필수 incident-case JSONL 계약과 lexical ranking에 연결했다.
   - [x] case store 부재/빈 검색을 성공으로 오인하지 않고 `data_unavailable`로 노출한다.
   - [x] provenance가 명시된 simulated incident corpus와 시나리오별 Top-1 회귀 4문항을 추가했다.
   - [ ] 검증된 incident case corpus를 적재하고 retrieval Recall@K/MRR fixture를 고정한다.
   - [x] incident playbook/process basics 8문항 golden retrieval fixture와 local evaluator를 추가했다.
   - [x] chunk별 issue metadata, portable chunk ID, 한영 동의어/조사 정규화, IDF coverage,
     metadata boost, 목차 감점으로 local `Recall@3=1.0`, `MRR=1.0`을 달성했다.
   - [x] SQL 관측값, playbook 후보, 유사 사례를 분리한 evidence matrix와
     `candidate_only` 결론 수준을 추가하고 Answer Supervisor가 보정 문구를 검증한다.
   - [x] verified/simulated case와 simulation playbook의 reliability를 분리하고,
     simulation-only 근거를 실제 원인 corroboration으로 승격하지 않도록 고정했다.
   - [x] 복수 issue 후보와 후속 검증 항목을 구조화하고 `example`, `references` 같은 문서
     구조 태그는 원인 후보 taxonomy에서 제외했다.
4. [ ] SC-004 수율/WIP 기간 비교 data contract와 fixture를 추가한다.
   - [x] Text2SQL chart intent가 다중 y 지표를 표현하도록 확장했다.
   - [x] Visualization이 wide comparison row를 metric/value long-form으로 정규화하고
     grouped bar chart contract를 생성하도록 구현했다.
   - [x] React trace UI에 다중 series grouped bar renderer를 연결했다.
   - [x] `Period_2`/`Period_3` 같은 명시적 period 집합과 복수 metric slot을 보존하고,
     grouped bar로 정규화하는 비교 계약을 추가했다.
   - [x] fab10 local DB에서 Period_2/Period_3 WIP·ontime 2행 golden SQL을 검증했다.
   - [x] `YYYY-MM-DD` 명시 범위를 inclusive end에서 SQL `[start, end)`로 정규화하고,
     최근 N일 제품 비교를 report date 범위 AVG 집계로 추가한다.
   - [ ] 자연어 월/분기 또는 사용자 timezone을 지정한 범위 계약을 추가한다.
5. [x] 대화 history와 feedback persistence를 구현한다.
   - [x] user/assistant exchange와 feedback을 local SQLite transaction으로 저장한다.
   - [x] FAB/process/product/route/equipment/date basis를 후속 질문 context로 복원한다.
   - [x] negative feedback과 comment를 다음 Planner/Reflection/Composer history에 전달한다.
   - [x] SC-001~SC-004 후속 질문 4개 sequence/8개 turn 평가 fixture와 CLI를 추가했다.
   - [x] 시나리오/후속질문 CLI는 `--live`를 명시해야 Azure/DB를 호출하도록 fail-closed 처리했다.
   - [ ] 운영 환경의 retention, anonymization, verified feedback promotion 정책을 확정한다.
6. [ ] SC-003 Impact 계산식/기준값 계약을 확장한다.
   - [x] utilization `%p` 변화의 capacity/lotcomps 1차 민감도와 cycle-time 변화 후 값을 계산한다.
   - [x] 계산 입력, 공식, numeric-column 평균 집계법, source table provenance, 가정/한계를
     `impact_calculation`에 기록하고 SC-003 4문항 offline gate를 추가했다.
   - [ ] Queue Time/output, downtime/output, cycle/ontime은 승인된 인과 모델과 기준값을 연결한다.
7. [ ] KPI 자동 리포트를 추가해 최초 목표 수치를 실제로 측정한다.
   - [x] SC-001~SC-004 routing/execution/final-answer 및 agent별 success/reflection pass를
     분리 집계하는 `evaluate_agent_scenarios.py`를 추가했다.
   - [x] final-answer 품질을 question alignment, evidence grounding, limitation visibility,
     provenance calibration 4개 차원과 평균 점수로 독립 집계한다.
   - [x] LLM Answer Supervisor의 `approved`와 별도로 deterministic verifier를 재실행해
     false-positive 승인을 scenario gate에서 실패 처리한다.
   - [x] Text2SQL/RAG/CaseSearch/Impact/Answer Quality 18개 local metric을
     `agent_quality_baseline.json`과 비교하는 `evaluate_release_quality.py`를 추가했다.
   - [x] baseline 하락, minimum 미달, metric 삭제를 각각 release failure로 처리하고 현재
     PostgreSQL 실행 보고서를 `output/evals/release_quality.json`에 저장한다.
   - [x] 표기/단위/기간/retrieval/chart 변형 21건의 adversarial fixture를 추가하고 Planner,
     Text2SQL, RAG, CaseSearch, Impact, Visualization별 pass rate를 baseline v2에 포함했다.
   - [x] unrelated negative query 8건의 RAG/CaseSearch abstention rate를 baseline v3에 추가했다.
   - [x] diagnosis calibration 5건의 candidate-only, taxonomy precision, provenance calibration을
     baseline v6 필수 지표로 추가했다.
   - [x] multi-turn context 6건의 normalization, entity switch, explicit override 정확도를
     baseline v7 필수 지표로 추가했다.
   - [x] Answer Quality 날짜 정렬 fixture를 12건으로 확장해 달력 월·분기와 상대 월·분기 누락을
     baseline v8에서 false-accept 회귀로 차단했다.
   - [x] temporal multi-series, result-column series, unknown chart type 변형을 추가해 adversarial
     fixture를 24건으로 확장하고 baseline v9에 고정했다.
   - [x] SC-002 알람·재공 정체, 납기 악화, 예방정비 지연 paraphrase를 positive retrieval에 추가해
     RAG 10건과 CaseSearch 6건을 baseline v10에 고정했다.
   - [x] CaseSearch가 지원하지 않는 FAB 도메인 질의를 일반 단어 overlap만으로 반환하지 않도록
     incident-signal 계약과 근거리 negative 3건을 추가해 baseline v11에 고정했다.
   - [x] taxonomy 없는 RAG/사례를 diagnosis 후보와 corroboration에서 제외하고 SQL-only,
     playbook-only 부분 근거 fixture 및 evidence eligibility metric을 baseline v12에 추가했다.
   - [x] 원인 후보가 0개인 diagnosis에서 최종 답변이 새 후보를 발명하지 못하도록 Answer Supervisor
     no-candidate disclosure 계약과 positive/negative fixture를 baseline v13에 추가했다.
   - [x] Impact 변화량을 가까운 방향 절과 결합하고 방향 없음·부호 충돌·물리 범위 초과를 거절하는
     9건의 계산/안전 fixture를 baseline v14에 추가했다.
   - [x] Visualization의 시간축 정렬, 중복 line key, invalid series 계약을 검증하는 adversarial
     3건을 추가해 전체 27건을 baseline v15에 고정했다.
   - [x] 원인+수치 영향, 원인+추세 복합 요청의 추가 agent를 보존하는 Planner normalization과
     fallback fixture를 추가해 전체 29건을 baseline v16에 고정했다.
   - [x] compound diagnosis의 Visualization/Impact downstream 요구에 따라 Text2SQL을 trend/status로
     선택하는 실행 계약과 SQL fixture를 추가해 전체 31건을 baseline v17에 고정했다.
   - [x] compound diagnosis+trend/impact를 실제 LangGraph dispatcher와 specialist node로 실행하는
     통합 회귀를 추가해 chart와 impact evidence 생성을 baseline v18에 고정했다.
   - [x] Answer Supervisor가 compound trend/impact facet과 성공한 Impact estimate 값 노출을 검사하는
     positive/negative 4건을 추가해 Answer Quality 18건을 baseline v19에 고정했다.
   - [x] SC-001 상태 답변의 SQL 결과값을 요청 지표별·Product/설비 대상별로 검사하고, 일부 지표나
     일부 대상 값만 답한 결함 케이스를 추가해 Answer Quality 22건을 baseline v20에 고정했다.
   - [x] SC-001 상태 답변의 숫자를 질문 입력값과 SQL numeric cell에 대조해 근거 없는 운영 수치를
     거절하고, 사용자 임계값 허용 케이스와 함께 Answer Quality 24건을 baseline v21에 고정했다.
   - [x] SC-004의 성공한 chart specification을 evidence로 남기고, 명시적 그래프 요청의 허위 성공
     주장을 거절하는 fixture를 추가해 Answer Quality 26건을 baseline v22에 고정했다.
   - [x] SC-003 Impact 답변의 숫자를 질문·SQL baseline·계산 inputs/estimates/formulae에 대조해
     근거 없는 추가 영향값을 거절하고 Answer Quality 28건을 baseline v23에 고정했다.
   - [x] SC-003 Impact의 delta/change estimate 부호와 답변 방향 표현을 대조해 부호 충돌을
     거절하고 절댓값+감소 표현을 허용해 Answer Quality 30건을 baseline v24에 고정했다.
   - [x] SC-002 diagnosis 답변 숫자를 SQL·RAG·유사 사례·synthesis evidence에 대조하고 사례 ID를
     오탐 없이 보존해 Answer Quality 32건을 baseline v25에 고정했다.
   - [x] SC-003 복합 문장에서 estimate 숫자와 가장 가까운 방향어를 결합해 입력 방향이 결과의
     방향 충돌을 가리는 문제를 막고 Answer Quality 34건을 baseline v26에 고정했다.
   - [x] SC-004 line chart의 시작·종료·절대 변화·변화율 summary를 결정적으로 생성해 evidence에
     기록하고 trend 숫자 hallucination을 막아 Answer Quality 36건, adversarial 32건을 v27에 고정했다.
   - [x] SC-004 trend summary의 percent_delta 부호와 답변의 최근접 방향 표현을 대조하고 다중
     series 문맥을 분리해 Answer Quality 38건을 baseline v28에 고정했다.
   - [x] DB 실행 release gate에 read-only PostgreSQL preflight를 추가해 connection failure를
     agent 품질 회귀와 분리하고 `infrastructure_unavailable` 상태로 보고하도록 baseline v29에 고정했다.
   - [x] 변화량 없는 원인+추세+영향 복합 질문도 Impact를 실행하고 LLM이 누락한 명시적 compound
     agent를 질문 기반 normalization으로 복구해 adversarial 33건을 baseline v30에 고정했다.
   - [x] 비교 표현 없는 복수 Product 현재 상태 질문을 `IN` 기반 다중 행·다중 지표 SQL로 확장해
     실제 PostgreSQL 2행 반환을 확인하고 Text2SQL 59문항을 baseline v31에 고정했다.
   - [x] 한국어 조사와 붙은 복수 장비 및 추가 숫자 suffix를 지원하고, 장비별 복수 지표
     상태·추세 SQL, 조합 chart series, 장비별 최종 답변 값 검증을 baseline v32에 고정했다.
   - [x] 복수 장비 추세의 각 장비·지표별 근거값 누락을 `trend_summary`로 검출하고,
     Planner와 deterministic Composer 경로까지 Answer Quality 42건, adversarial 37건의
     baseline v33으로 고정했다.
   - [x] RAG·CaseSearch의 한국어 incident 표현 변형을 확장하고 CaseSearch에 issue-type
     정합성 필터를 추가했다. RAG 14건, CaseSearch 10건 Top-1과 retrieval abstention
     12건, adversarial 39건을 baseline v34에 고정했다.
   - [x] Impact에 상대 utilization percent와 metric별 복수 변화 계산을 추가하고, perf와
     station-group utilization의 동일 시점 cross-source baseline 및 cycle duration 시간 변환을
     실제 PostgreSQL로 검증했다. Text2SQL 63건, Impact 13건, Answer Quality 44건,
     adversarial 42건을 baseline v35에 고정했다.
   - [x] Diagnosis 후보를 verified case, playbook, 관측 metric 정합성, 교차 출처 지지로
     순위화하고 출처 간 부분 일치와 경쟁 후보를 구분했다. 유효 taxonomy 필터를 Top-K보다
     먼저 적용하고, 1순위 후보 누락을 Answer Supervisor가 거절하도록 baseline v36에 고정했다.
   - [x] Visualization line x축을 temporal/numeric domain으로 제한하고 숫자 문자열 정렬,
     동일 시점의 표현 변형 중복 검출, 범주형 pseudo-trend 거절을 adversarial 45건의
     baseline v37에 고정했다.
   - [x] ConversationMemory의 release date basis를 Text2SQL 한국어 별칭과 정렬하고,
     납기일 기준 상속 및 투입일 기준 전환을 8개 multi-turn fixture와 별도 inheritance
     metric으로 baseline v38에 고정했다.
   - [x] Planner fallback의 현재 metric `뭐야` 질문을 status로 우선 분류하고 Impact 전후 비교
     차트 요청에 Visualization을 추가해 adversarial 47건의 baseline v39에 고정했다.
   - [x] Text2SQL status에서 metric 임계값 및 상위/하위 N개를 typed slot과 안전한
     WHERE/ORDER BY/LIMIT으로 보존하고, 실제 PostgreSQL EX/EM 65/65의 baseline v40에 고정했다.
   - [x] Text2SQL metric threshold의 한국어 percent 표기를 지원하고 1~200 밖 Top-N은
     clarification으로 차단해 실제 PostgreSQL EX/EM 67/67의 baseline v41에 고정했다.
   - [x] Answer Supervisor가 status 임계값의 값·비교 방향과 상위/하위 N을 최종 답변에서
     보존하는지 검사하고 Answer Quality 48/48의 baseline v42에 고정했다.
   - [x] CaseSearch가 명시된 equipment type과 process group이 다른 사례를 제외하도록 하고,
     target mismatch negative 2건을 추가해 retrieval abstention 14/14의 baseline v43에 고정했다.
   - [x] Text2SQL status의 명시 metric을 선택된 AutoSched table schema와 검증해 지원하지 않는
     임계값·정렬 조건의 묵시적 누락을 차단하고, 정렬 metric 없는 Top-N을 clarification으로
     처리해 실제 PostgreSQL EX/EM 70/70의 baseline v44에 고정했다.
   - [x] Text2SQL status의 개수 없는 높은/낮은 순과 상위/하위, 오름/내림차순을 정렬 slot으로
     보존하고 metric 없는 정성적 순위는 clarification으로 차단해 실제 PostgreSQL EX/EM
     72/72의 baseline v45에 고정했다.
   - [x] Text2SQL status 임계값의 비교 기호와 at least/at most/above/below 영문 표현을
     안전한 operator slot으로 정규화하고 실제 PostgreSQL EX/EM 75/75의 baseline v46에 고정했다.
   - [x] Text2SQL 임계값의 percent 단위를 slot으로 보존하고 비율 지표의 0~100 범위 및
     비율이 아닌 지표의 percent 단위 오용을 clarification으로 차단해 실제 PostgreSQL EX/EM
     77/77의 baseline v47에 고정했다.
   - [x] Answer Supervisor가 기호·영문 임계값과 개수 없는 정성적 정렬의 동치 표현을 허용하면서
     최종 답변의 조건 누락은 거절하도록 확장해 Answer Quality 52/52의 baseline v48에 고정했다.
   - [x] ConversationMemory가 후속 지시·bare threshold에서만 metric을 상속하고 명시 metric 전환을
     우선하도록 확장했다. memory→Planner→Text2SQL 통합 회귀와 별도 selection inheritance metric을
     추가해 context 10/10의 baseline v49에 고정했다.
   - [x] Impact가 동일 metric의 복수 변화 중 첫 항목만 임의 적용하지 않도록 전체 계산을 보류하고,
     cycle time 0 이하 projection 방어를 추가해 Impact 15/15의 baseline v50에 고정했다.
   - [x] RAG가 복합 chunk 본문의 모든 issue_type을 읽고 명시 issue mismatch를 local·Milvus
     결과에서 제외하도록 했다. 기존 Recall@3/MRR 1.0과 abstention을 유지하면서 incident evidence
     alignment 1.0을 별도 metric으로 추가해 baseline v51에 고정했다.
   - [x] RAG의 query-matched issue provenance를 Diagnosis까지 전달해 복합 chunk의 첫 metadata
     issue로 후보가 왜곡되지 않게 하고 mismatch evidence를 후보 자격에서 제외해 Diagnosis
     11/11의 baseline v52에 고정했다.
   - [ ] 실제 Azure/DB 평가 실행은 DB schema/query 결과의 외부 전송 승인을 받은 뒤 기준선을 고정한다.

## Agent 성능 고도화 진행 (2026-09-06)

- [x] Composer 이후 `answer_supervisor`를 추가해 원 질문, plan, evidence, limitation과 최종
  답변을 직접 비교한다.
- [x] FAB/product/route/lot/equipment 식별자와 WIP, Queue Time, Cycle Time, Ontime,
  Utilization, Down, PM, Lot completion, Output, Capacity 지표 누락을 deterministic하게 검출한다.
- [x] 최종 답변이 불완전하면 supplied evidence 범위에서 1회 교정하고 교정 결과를 재검증한다.
- [x] Impact agent가 Text2SQL row를 baseline으로 사용하도록 연결하고, utilization `%p`와
  cycle-time 변화의 투명한 1차 민감도 계산을 구현했다.
- [x] 인과 모델이나 시간당 처리율이 없는 Queue Time/output, downtime/lot completion,
  cycle-time/ontime 관계는 근거 없는 수치를 생성하지 않고 필요한 입력을 limitation으로 반환한다.
- [x] Text2SQL 58문항 fixture를 현재 AutoSched 적재 상태와 LLM direct-SQL 계약에 맞게 갱신했다.
- [x] EX는 실행 상태/행 수, EM은 대소문자 비의존 SQL fragment와 결과 컬럼/차트/date slot의
  semantic contract를 검사하도록 평가기를 강화했다.
- [x] Queue Time 직접 지표 부재, Product_N/part_N alias, WarmUp 컬럼 오용, PM area join,
  breakdown type prefix, 상대 날짜 snapshot 범위를 deterministic하게 처리한다.
- [x] status/master/release/trend 58문항 전체를 `deterministic_only` release gate로 고정하고,
  SQL이 필요한 48문항은 LLM 호출 없이 read-only SQL을 생성한다.
- [x] 달력 월/분기와 여러 달 범위를 exclusive-end 기간으로 파싱하고 실제 일/주/월 SQL 버킷과
  chart x축을 일치시켰다. 역전 범위와 잘못된 월/분기는 SQL 생성 전 clarification으로 차단한다.
- [x] 날짜 경계와 분기 범위 8문항을 추가해 deterministic gate를 58문항, SQL 실행 대상을 48문항으로 확장했다.
- [x] current station/product/process 조회와 제품 비교는 최신 `report_time` snapshot만 사용하고,
  station ID는 prefix가 아닌 exact match로 처리한다.
- [x] breakdown equipment type은 toolgroup prefix를 `toolgroups.area`를 통해 breakdown
  `type_name`에 연결해 데이터 기반으로 조회한다.
- [x] Supervisor 교정문은 deterministic 재검증을 통과한 경우에만 최종 답변에 적용한다.
- [x] diagnosis의 Text2SQL 근거가 없어도 RAG와 CaseSearch를 독립적으로 계속 실행한다.
- [x] diagnosis 근거를 관측 데이터/문서 후보/유사 사례로 합성하고, 실제 원인 확정 대신
  `candidate_only`로 표시하지 않은 답변을 deterministic verifier가 거부하도록 했다.
- [x] SQL 실행 성공이어도 결과 행이 없으면 운영 관측 근거 또는 숫자 주장 근거로 인정하지 않는다.
- [x] simulated incident/playbook을 사용한 진단 답변은 시뮬레이션 출처를 명시하지 않으면
  Answer Supervisor deterministic 검증을 통과하지 못하도록 했다.
- [x] diagnosis 사례 `issue_type`을 허용 taxonomy로 필터링하고 station down을 equipment down으로
  정규화했다. synthetic source가 `verified`로 표시돼도 unverified reference로 강등한다.
- [x] Conversation memory가 공백·하이픈 FAB/공정/설비/date-basis를 canonicalize하고, 현재 발화의
  대상 변경을 과거 context보다 우선해 다음 지시어가 오래된 FAB나 제품으로 돌아가지 않게 했다.
- [x] explicit/relative date context, status SQL sample의 실제 metric 값, 사용자에게 보이는
  limitation과 Impact 계산 경계를 최종 답변 필수 계약으로 추가했다.
- [x] deterministic Composer fallback도 성공한 Text2SQL sample row를 답변에 포함해 LLM 장애 시
  metric 이름만 있고 값은 없는 응답을 방지한다.
- [x] SC-001~SC-004별 positive/negative 최종 답변 8건을 고정해 Answer Supervisor verifier의
  classification accuracy, positive accept, negative reject를 각각 측정한다.
- [x] local release-quality composite gate는 41개 metric의 현재값/baseline/minimum/delta와 평가
  mode를 기록하며 Azure full graph 미측정 상태를 명시한다.
- [x] `FAB 10`/`FAB-10`, `DE-BE-11`, `/`·`.` 날짜, `지난 일주일`, `퍼센트포인트`·`퍼센트`
  표현을 canonical slot/unit/date로 정규화한다.
- [x] RAG가 `멈춤/정지/비가동`을 equipment-down intent로 연결해 paraphrase Top-1을 복구했다.
- [x] RAG와 CaseSearch가 알람·재공 정체·납기·예방정비 표현과 한국어 조사를 정규화하고,
  명시적 PM intent를 일반 equipment-down보다 우선해 SC-002 paraphrase Top-1을 복구했다.
- [x] CaseSearch는 질문과 사례가 incident signal을 공유할 때만 결과를 반환해 제품·품질·설비 같은
  일반 명사 하나로 무관한 simulated case가 노출되는 false positive를 차단했다.
- [x] Diagnosis synthesis는 유효한 diagnostic issue가 없는 문서/사례를 후보 및 verified analogy에서
  제외하고 `excluded_evidence`로 추적해 구조 태그가 원인 근거로 승격되지 않게 했다.
- [x] Answer Supervisor는 diagnosis synthesis의 후보가 0개면 원인 판단 근거 부족을 답변에 요구해
  SQL 관측값만으로 원인 후보를 발명하는 false accept를 차단한다.
- [x] Impact는 결과 질문의 `감소`가 입력 변화의 `증가` 부호를 뒤집지 않도록 변화량 주변 방향어를
  사용하며, 모호·충돌 방향과 utilization/cycle time 물리 범위 초과를 계산하지 않는다.
- [x] Visualization은 ISO 시간축을 오름차순 정렬하고 중복 `(x, series)`와 존재하지 않거나 축과
  충돌하는 series를 거절해 프런트 렌더링의 행 유실과 잘못된 연결 순서를 차단한다.
- [x] Planner route invariant는 primary query type의 필수 agent를 보충하면서 명시적 복합 요청의
  Impact/Visualization을 삭제하지 않고 graph 실행 순서로 정규화한다.
- [x] Graph는 compound diagnosis에 Visualization이 있으면 trend SQL을, Impact만 있으면 status
  baseline SQL을 요청해 downstream agent가 필요한 결과 형태를 받도록 한다.
- [x] Compound route 통합 테스트는 agent 실행 순서뿐 아니라 최종 chart 시간 정렬과
  `capacity_delta_percent` 계산 evidence까지 검증한다.
- [x] Answer Supervisor는 compound 답변의 trend/chart와 impact task facet을 검사하고, 성공한
  Impact 계산이 있으면 실제 estimate 값과 계산 경계를 최종 답변에 요구한다.
- [x] Visualization은 모든 행의 encoding field를 검증하고 숫자 문자열을 유한 numeric 값으로
  정규화하며, 누락/NaN/Infinity를 일관된 `ValueError`로 거부한다.
- [x] Visualization의 복수 지표 line chart를 metric별 series로 분리하고 React가 각 series를
  독립 polyline/point로 렌더링한다. 실제 결과 column 기반 series와 미지원 type 거절도 추가했다.
- [x] deterministic Composer fallback이 verifier의 canonical 식별자·지표·기간을 `요청 범위`로
  보존해 실제 값이 있어도 질문 scope 누락으로 Answer Supervisor에서 실패하던 E2E를 수정했다.
- [x] Answer Supervisor의 식별자/날짜 검사도 spaced/hyphen identifier와 slash/dot date,
  `지난 일주일` 표현을 canonical 비교한다.
- [x] Answer Supervisor가 `YYYY년 M월`, 분기 범위, 이번/지난 월·분기를 필수 context로 검사하고
  한국어 분기와 `Qn` 동등 표기는 같은 기간으로 인정한다.
- [x] RAG는 score 0 강제 top-1 fallback을 제거하고, FAB 식별자 외 의미 토큰 또는 issue intent가
  일치하지 않으면 빈 결과를 반환한다.
- [x] RAG 빈 결과를 graph/API가 `succeeded`로 오인하지 않고 `data_unavailable`과 limitation으로
  노출하도록 상태 전파를 수정했다.
- [x] Impact 결과에 입력·공식·집계법·source table을 포함하고, SC-003 deterministic evaluator에서
  계산 2/2와 data-unavailable 거절 2/2를 고정했다.
- [x] Planner의 query type별 필수 agent route를 deterministic invariant로 고정하고,
  Supervisor 승인 단계가 agent를 조용히 누락하지 못하도록 했다.
- [x] CaseSearch simulated corpus 6문항에 `evaluate_case_retrieval.py`를 연결해
  local `Recall@3=1.0`, `MRR=1.0`을 고정했다.
- [x] `/api/chat`과 SSE 응답의 status/agent run 계약을 맞추고, React 답변에 feedback control을
  연결했다.
- [ ] verified incident case 적재를 release gate에 추가한다.
- [x] verified incident의 검증자·검증시각·원본 evidence 참조 계약을 추가하고, 자기 선언 verified
  레코드 강등과 최종 답변 case ID 공개를 release gate에 고정했다.
- [x] incident store의 case ID 중복, synthetic verified 위장, timezone 없는 검증시각을 fail-closed로
  거절하고 CaseSearch release 평가가 저장소 전체 무결성을 함께 검사하도록 했다.
- [x] CaseSearch top-k에 동등 관련도의 verified 사례를 최소 한 건 보존하되 75% relevance 임계값으로
  낮은 관련도 사례의 무조건 승격을 방지했다.
- [x] CaseSearch에 명시적 FAB 정합성 필터와 verified 동점 최신순을 추가하고, fab10 corpus의 FAB
  provenance 및 cross-FAB abstention fixture를 고정했다.
- [x] 동일 issue의 verified 사례가 서로 다른 원인을 기록하면 Diagnosis support를 충돌 상태로
  강등하고, 최종 답변에 두 case ID와 원인 불일치를 공개하도록 강제했다.
- [x] verified incident 발생시각과 검증시각 순서를 검증하고, 질문 기간 밖 사례를 historical analogy로
  강등해 최종 답변에 시간 정합 한계를 공개하도록 했다.
- [x] Impact parser가 결과 방향 질문을 입력 변화 방향으로 오인하지 않도록 변화 clause 경계를
  강화하고 모호한 utilization 변화는 계산 없이 거절하도록 했다.
- [x] Impact baseline에 서로 다른 대상·기간 차원이 섞이면 단일 평균 영향값 계산을 거절하고,
  혼합 차원을 provenance에 공개하도록 했다.
- [x] Answer Supervisor가 Impact 혼합 baseline의 실제 차원과 대상·기간별 분리 필요성을 최종
  답변에 요구해 일반적인 데이터 부족 문구로 원인이 유실되지 않도록 했다.
- [x] 혼합 station-group Impact를 adversarial fixture에 추가하고, 혼합 차원을 agent run과 stream
  trace에도 노출해 API 실행 경로의 거절 사유를 추적할 수 있게 했다.
- [x] RAG와 CaseSearch의 issue keyword에 국소 부정 범위를 적용해 “down은 아니고 Queue Time 증가”가
  equipment-down 근거로 오염되지 않게 하고 positive retrieval fixture에 고정했다.
- [x] RAG knowledge-base 자동 선택에서도 부정된 incident token을 제외해 process 설명 요청이
  incident playbook으로 잘못 라우팅되지 않도록 했다.
- [x] 서로 다른 두 명시 기간의 SC-004 비교를 조건부 집계로 지원하고, 겹침 방지와 기간별 최종
  답변 값 검증을 release gate에 추가했다.
- [x] local RAG retrieval 평가는 `rag_retrieval_eval.json`과
  `evaluate_rag_retrieval.py`로 release gate를 고정했다. Milvus embedding 경로 평가는 별도다.
- [x] 기본 corpus/state 경로를 assistant package root 기준으로 정규화해 저장소 루트와
  `apps/assistant` 어느 위치에서 실행해도 동일한 데이터를 사용한다.
- [x] Azure 연결/HTTP/JSON 오류를 공통 `RuntimeError`로 정규화해 SSE가 구조화된 실패 event를
  반환하도록 했다.

최근 성능 기준선:

| 평가 대상 | 현재 측정값 | 해석 |
| - | - | - |
| Text2SQL live 47문항(이전 fixture) | EX 29/47(61.7%), EM 17/47(36.2%), intent 47/47 | 현재 77문항 fixture의 full Azure graph는 외부 전송 승인 후 재측정 필요 |
| Text2SQL deterministic 79문항 | local PostgreSQL EX 79/79, EM 79/79, intent 79/79 | 61개 SQL + 18개 clarification/data-unavailable/unsupported guard, 두 명시 기간 비교 포함, Azure 호출 없음 |
| Local RAG | Recall@3 1.0, MRR 1.0 (15/15 Top-1), incident issue alignment 1.0 | 한국어 incident 표현 변형, 부정된 issue 제외, 복합 chunk issue 정합성 포함 |
| Local CaseSearch | Recall@3 1.0, MRR 1.0 (11/11 Top-1) | issue 부정/FAB 정합성과 verified 최신순 포함, simulated reference corpus 기준 |
| SC-003 deterministic Impact | 17/17 contract pass | 상대 percent, 복수 변화, 동일 metric 중복, 부분 모델 limitation, 입력/결과 방향 분리·물리 범위·혼합 baseline 거절 포함 |
| SC-002 diagnosis synthesis | 14/14 contract pass | candidate-only, verified metadata·cause/time conflict, mismatch eligibility, ranking, support calibration 모두 1.0 |
| Multi-turn context memory | 10/10, context accuracy 1.0 | normalization, entity/date-basis/metric switch, explicit override, selection inheritance 모두 1.0 |
| Answer Quality verifier | accuracy 1.0, positive accept 1.0, negative reject 1.0 (70/70) | SC-001 선택, SC-002 verified ID·원인/기간 충돌, SC-003 estimate·혼합 baseline 공개, SC-004 gap·zero-fill·기간 비교 포함 |
| Adversarial agent robustness | 57/57, agent별 pass rate 1.0 | Planner 경계, Impact 혼합 baseline, Visualization coverage·calendar gap·zero-fill 방어 포함 |
| Retrieval positive | RAG 15/15, CaseSearch 11/11 Top-1 | Recall@3/MRR 1.0 유지 |
| Retrieval abstention | 15/15, RAG 5/5, CaseSearch 10/10 | unrelated·근거리·지원하지 않는 PM issue·명시적 FAB/target mismatch negative 포함 |
| Composite local release gate | 47/47 metric, regression 0 | baseline v73, PostgreSQL preflight 후 Text2SQL 실행, Azure full graph는 `not_measured` |
| Python 회귀 | 341 passed | live Azure full graph 품질을 대체하지 않음 |

Local composite gate 실행:

```bash
.venv/bin/python apps/assistant/scripts/evaluate_release_quality.py --execute-text2sql
```

현재 LangGraph stream 경로:

- `planner(LLM) -> supervisor(LLM) -> selected agent -> agent reflection -> post-execution supervisor(LLM)`
- post-execution supervisor는 `continue -> 다음 agent`, `retry_same_agent -> 같은 agent`,
  `replan -> planner`, `alternate_agent -> 호환 agent` 조건부 edge를 선택한다.
- 모든 실행이 끝나면 `reflection(LLM) -> composer(LLM) -> answer_supervisor(LLM)`로 종료한다.
- 최종 Reflection은 `compose`, `replan`, `retry_target`, `human_review` 중 하나를 선택한다.
- Dispatcher는 Planner의 `execution_steps` cursor를 따라 선택된 agent만 직접 호출한다.
- `/api/chat/stream`은 Planner plan, Text2SQL query plan/SQL/result, chart spec,
  reflection, final response를 SSE로 순차 전송한다.
- 고빈도 status/master/release/trend 질의는 parsed slot과 allowlisted query contract에서 SQL을
  deterministic하게 렌더링하고, plan에 `source_tables`, `select_items`, `filters`, `group_by`,
  `order_by`, `aggregation`, `template_id`를 기록한다.
- `apps/assistant/scripts/load_autosched_postgres_reports.py`는 UTF-16 tab-delimited AutoSched `.rep`를
  `{fab}.autosched_*` staging table로 적재한다.
- Text2SQL은 deterministic contract가 없는 질문에만 Azure OpenAI 호환 `chat/completions`로
  SQL을 직접 생성한다.
- `sql_templates.py`는 legacy 검증 유틸로 남아 있고, 현재 deterministic renderer는 현행 slot과
  schema catalog를 단일 기준으로 유지하기 위해 `app/sub_agent/text2sql.py`에 구현돼 있다.

남은 고도화:

- Planner/Supervisor는 structured output LLM으로 intent와 실행 경로를 결정하고, Text2SQL의
  deterministic slot parser는 LLM SQL 호출 전 안전 전처리로 유지한다.
- `/api/chat`과 `/api/chat/stream`은 모두 같은 LangGraph를 실행한다. Orchestration LLM 장애 시
  query-type route invariant 기반 deterministic Planner/Supervisor/Reflection/Composer fallback으로
  강등하며 `execution_mode`과 `fallback_used` trace를 남긴다. Text2SQL SQL 생성 자체는 LLM 장애 시
  근거 없는 SQL을 만들지 않고 `failed`로 유지한다.
- `lotrelease` 외 AutoSched operational report, PM, breakdown 대표 질문 fixture를 추가했고,
  schema context에는 metric/date catalog와 parsed slot을 함께 제공한다.
- `lotrelease`의 `최근`, `일별`, `날짜 기준` 질문은 `start_date`와 `due_date` 중 기준이
  명확하지 않으면 SQL 생성 전 clarification으로 중단한다.
- `/api/chat/stream`은 elapsed time, retry budget, timeout, disconnect/cancellation telemetry를
  SSE event data에 포함한다.
- RAG, CaseSearch, Impact는 실제 저장소/모델 연결 전까지 limitation을 반환한다.

최근 추가된 검증 fixture:

- `apps/assistant/tests/fixtures/text2sql_fab10_eval.json`: fab10 Text2SQL 대표 질문 58개.
- `apps/assistant/tests/fixtures/scenario_acceptance_questions.json`: SC-001~SC-004 acceptance 질문 19개.

비고:

- 5번은 일정상 endpoint 구현을 바로 진행하지 않고, Text2SQL schema catalog와 LLM direct SQL 호출을
  먼저 구현하는 방향으로 대체 결정했다.
- SC-001 endpoint는 AutoSched `.rep` 적재와 LLM direct SQL validation 이후 연결한다.
- 현재 LLM API key 인증 실패 시 Text2SQL은 `failed`로 종료하고 SQL을 생성하지 않는다.
- Planner, Supervisor, Self-reflection, Composer는 모두 Azure Chat Completions를 호출한다.

## Text2SQL 일반화 추가 검증 (2026-09-08)

- [x] `fix/adv_t2s`에서 공통 FAB 메타 검색, 실제 area 도메인, SQL 이전 의미 계획 검증 보강.
- [x] CTE 집계/최신 MAX 계보, NULL 행 집계, 매칭 조인의 설정 중복, 계획 외 필터, 지표 별칭 역할 검사.
- [x] 날짜 범위의 기본 시간 열 및 명시 시작/종료 열 보존, 명확한 NULL 행 수 조건 바인딩.
- [x] 수정 전 고정한 tuning 24개와 validation 16개를 분리 실행. 최초 별도 검증 14/16을 원본 보존하고, 발견된 2건 수정 후 별도 개발 회귀 5/5 확인.
- [x] 전체 테스트 474개, 기존 실제 DB 회귀 79/79, 이번 라운드 저장 SQL/현재 계획/DB 재검증 43/43 확인.
- [x] 실제 화면에서 요청 문맥 FAB10보다 질문의 FAB13 우선, NULL 행 864개 최종 답변 확인.
- [x] 같은 UI 대화에서 NOT NULL 2592개 및 FAB12 기간 합계 443 확인. 상위 Planner의 불필요한 테이블명 재질문을 제거하고 동일 질문의 조회 복구 검증.
- [ ] 업무 담당자의 지표/조인/시간 기준 SSOT 검토와 실제 사용자 질문 기반 외부 평가 확대. 현재 자체 작성 소규모 평가만으로 일반 정확도 100%를 보장하지 않는다.

상세 변경·평가 계약·원본 실패·한계: `docs/text2sql_generalization_20260908.md`.
