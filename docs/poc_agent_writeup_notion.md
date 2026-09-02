# PoC 모듈 구현 보고서 보강안

> 작성 기준: 현재 PoC 코드 기준. 실제 구현은 `app/agents/graph.py`, `app/agents/prompts.py`, `app/sub_agent/text2sql.py`, `app/sub_agent/rag.py`, `app/agents/llm_nodes.py` 기준으로 정리했다.

## 2. 주요 Agent 구현 내용

| Agent | 구현 구조 | LangGraph 연결 | 검증 포인트 |
| --- | --- | --- | --- |
| Planner | 사용자 질문을 `status`, `master_data_lookup`, `release_plan_lookup`, `diagnosis`, `impact`, `trend`, `knowledge_lookup`, `unsupported` 중 하나로 분류한다. 필요한 sub-agent 목록과 실행 순서를 structured output으로 반환한다. | `START -> planner -> supervisor` | SQL 직접 생성 금지, 최소 sub-agent 선택, RAG knowledge base 선택, 누락 slot 식별 |
| Supervisor | Planner 결과를 실행 가능 상태로 검토한다. `ready`, `needs_clarification`, `data_unavailable`, `unsupported` 상태를 확정하고, 필요한 경우 실행을 중단한다. | `planner -> supervisor -> text2sql` | live/current data가 없을 때 General Data로 추정하지 않음, 필수 Text2SQL 실패 시 halt |
| Text2SQL | FAB/제품/route/toolgroup/date/metric slot을 추출하고, 허용 schema catalog만 LLM에 전달한다. Azure Chat Completions structured output으로 PostgreSQL `SELECT/WITH` SQL을 생성한 뒤 read-only validator와 table allowlist로 검증한다. | `supervisor -> text2sql -> rag` | DDL/DML 차단, schema-qualified table만 허용, `LIMIT` 정책, AutoSched 미적재 시 `data_unavailable` |
| RAG | 질문 성격에 따라 `incident_playbook` 또는 `process_basics` knowledge base를 선택한다. Milvus 연결이 있으면 vector search, 없으면 local JSONL chunk scoring을 사용한다. | `text2sql -> rag -> case_search` | 진단 답변은 가능 원인/검토 가이드로 제한, RAG-only로 실제 root cause 단정 금지 |
| CaseSearch | 유사 사례 검색 sub-agent 자리. 현재는 구조 연결 중심이며, 진단 흐름에서 RAG/SQL 근거와 결합될 수 있도록 evidence 형태를 맞춘다. | `rag -> case_search -> impact` | 유사 사례가 없거나 stub이면 한계로 노출 |
| Impact | 영향도 계산 질문에서 baseline/scenario 입력을 받아 output delta 계산 구조로 연결한다. 현재 operational metric과 완전 연결되지는 않아 limitation을 반환한다. | `case_search -> impact -> visualization` | 수치 근거 없이 영향도를 단정하지 않음 |
| Visualization | Text2SQL 결과 row와 `chart_intent`를 받아 chart spec을 생성한다. trend/비교 질문에서 x/y encoding이 SQL 결과 column과 맞는지 확인한다. | `impact -> visualization -> reflection` | rows 없으면 skipped, chart field가 결과 column에 없으면 오류 |
| Reflection | tool 결과와 limitation을 기준으로 답변 근거성을 검토한다. deterministic safety check와 LLM reflection을 함께 사용한다. | `visualization -> reflection -> composer` | General Data를 live state로 표현하지 않음, RAG-only diagnosis 단정 방지 |
| Composer | Planner, tool summaries, evidence, limitations, reflection 지시를 받아 최종 사용자 답변을 생성한다. | `reflection -> composer -> END` | 내부 evidence object명을 노출하지 않고 값/근거/한계를 사용자 언어로 정리 |

## 3. LangGraph 실행 구조

```text
START
  -> planner
  -> supervisor
  -> text2sql
  -> rag
  -> case_search
  -> impact
  -> visualization
  -> reflection
  -> composer
  -> END
```

| State key | 역할 |
| --- | --- |
| `request` | 사용자 질문, fab context, conversation id |
| `plan` | PlannerDecision: query_type, selected_sub_agents, execution_steps |
| `halted` | 필수 단계 실패 또는 clarification 필요 시 이후 agent를 skip |
| `text2sql_result` | SQL, rows, columns, row_count, QueryPlan |
| `evidence` | planner/text2sql/rag 등 각 agent 근거 |
| `limitations` | 데이터 미적재, live state 아님, 근거 부족 등 한계 |
| `agent_runs` | agent별 실행 상태와 metadata |
| `chart` | Visualization chart spec |
| `reflection` | self-check 결과와 composer 지시 |
| `answer` | 최종 사용자 답변 |

## 4. Text2SQL 논문 참고 및 차용 부분

### 참고 논문

| 논문 | 핵심 아이디어 | PoC 적용 방식 |
| --- | --- | --- |
| MARS-SQL: A Multi-Agent Reinforcement Learning Framework For Text-To-SQL | Grounding Agent, Generation Agent, Validation Agent로 역할을 분리하고, DB 실행 결과를 observation으로 사용해 SQL을 개선한다. | 전체 구조를 `schema grounding -> SQL generation -> validation/execution`으로 나눴다. 실행 전 read-only validation, 실행 후 row/column/limitation을 evidence로 넘긴다. |
| A robust natural language text-to-SQL generation framework with dynamic strategies based on LLMs (TriSQL) | Question-Guided Schema Selector, Structure-Aware SQL Generator, Complexity-Aware SQL Refiner를 사용한다. | 질문과 slot 기반으로 schema catalog를 축소하고, query_type/data_source_type을 먼저 정한 뒤 SQL 생성에 필요한 최소 schema만 LLM에 제공한다. |

### 실제로 차용한 부분

| 단계 | 차용 개념 | 현재 구현 |
| --- | --- | --- |
| Intent/Slot | 질문을 바로 SQL로 번역하지 않고 FAB, product, route, toolgroup, lot, metric, date_basis 등을 먼저 추출 | `_extract_slots()`, regex/alias parser, request context fab |
| Schema Grounding | 전체 DB schema 대신 질문과 관련된 table/column만 제공 | `SCHEMA_CATALOG`, `_schema_context_for_question()` |
| Structure First | 질문 유형과 data source를 먼저 결정 | `_classify_query_type()`, `_data_source_type()` |
| Generation | LLM은 허용 schema와 rule 안에서 SQL JSON만 생성 | `OpenAIText2SQLClient.create_sql()`, `TEXT2SQL_OUTPUT_SCHEMA` |
| Validation | 실행 가능한 SQL이어도 정책 위반이면 실패 처리 | `ReadOnlyQueryExecutor.validate()`, `_validate_sql_tables()` |
| Observation | 실행 결과를 최종 답변이 아니라 evidence로 상위 graph에 전달 | `Text2SQLResult.rows`, `columns`, `row_count`, `QueryPlan` |
| Fallback | live/current status에 필요한 AutoSched table이 없으면 General Data로 추정하지 않음 | `_operational_data_unavailable()` |

### 아직 차용하지 않은 부분

| 논문 기능 | 현재 상태 | 이유 |
| --- | --- | --- |
| MARS-SQL GRPO 강화학습 | 미구현 | PoC 데이터 규모와 일정 대비 비용이 큼 |
| MARS-SQL 다중 trajectory rollout | 미구현 | 현재는 single SQL candidate 중심 |
| 별도 7B Validation Agent | 미구현 | LLM reflection + deterministic validator로 대체 |
| TriSQL 학습형 schema selector | 미구현 | FAB schema가 제한적이라 catalog/slot rule로 대체 |
| TriSQL skeleton decoder | 부분 적용 | 별도 decoder는 없고 query_type/schema_context를 먼저 구성 |

## 5. Text2SQL 상세 구조

```text
사용자 질문
  -> normalize question
  -> slot extraction
  -> query_type classification
  -> clarification gate
  -> schema context build
  -> Azure Chat Completions structured output
  -> read-only SQL validation
  -> table allowlist validation
  -> PostgreSQL execution
  -> Text2SQLResult packaging
```

| Query type | Data source | 허용 table family | 정책 |
| --- | --- | --- | --- |
| `status` | `operational_report` | `autosched_perf`, `autosched_stngrp`, `autosched_stn`, `autosched_part`, `autosched_lot` | AutoSched table 없으면 `data_unavailable` |
| `master_data_lookup` | `model_master` | `toolgroups`, `route_product_*`, `pm`, `breakdown`, `setups`, `transport` | SMT2020 General Data 기준으로만 답변 |
| `release_plan_lookup` | `release_plan` | `lotrelease`, `lotrelease_variable_due_dates`, `lotrelease_engineering` | product/route/scenario/date 조건 없이 broad scan 금지 |
| `trend` | `release_plan` 또는 `operational_report` | 질문 slot에 따라 선택 | 날짜 기준이 모호하면 clarification |

## 6. 프롬프트

### Planner system prompt

```text
You are the Planner agent for a semiconductor FAB assistant.

Your job is to turn a user question into an execution plan. Do not execute tools
or write SQL directly. Classify the query, identify missing slots, choose the
minimum required sub-agents, and return a structured plan.

Required output fields:
- query_type: status | master_data_lookup | release_plan_lookup | diagnosis | impact | trend | knowledge_lookup | unsupported
- intent: concise task intent
- rag_knowledge_base: incident_playbook for incident response/manual guidance, process_basics
  for semiconductor basics/general reference, null when RAG is not selected
- missing_slots: required information that must be clarified before execution
- selected_sub_agents: ordered list from text2sql, rag, impact, case_search, visualization
- execution_steps: ordered actions for the Supervisor
- clarification_question: present only when required slots are missing
- limitations: known data or scope limitations

Policy:
- Use Text2SQL for database-backed status, master-data, route, release-plan, trend, and
  numeric evidence gathering.
- Use RAG for process knowledge and diagnosis support.
- Use RAG only for knowledge_lookup questions that ask concepts or basic explanations.
- For RAG, choose incident_playbook for response/manual/incident guidance and
  process_basics for basic semiconductor concepts or SMT2020/AutoSched documentation.
- Use Impact only for impact calculation questions.
- Use Visualization for trend/comparison or chartable tabular results.
- If live/current operational data requires AutoSched autosched_* tables that are not
  available, plan a data_unavailable path. Do not fall back to General Data.
- Never plan direct equipment control or automatic production actions.
```

### Supervisor system prompt

```text
You are the Supervisor agent for a semiconductor FAB assistant.

Your job is to execute the Planner's structured plan by selecting and sequencing
sub-agents. After each sub-agent result, decide whether to continue, stop for
clarification, stop for data_unavailable, retry a sub-agent, or request replanning.

Required behavior:
- Follow selected_sub_agents in order unless a result requires early stop.
- If Text2SQL returns needs_clarification, stop and ask the clarification question.
- If Text2SQL returns data_unavailable for live/current status, do not fabricate a
  status answer from General Data.
- For diagnosis, try to combine SQL evidence with RAG knowledge. If one side is missing,
  continue only with an explicit limitation.
- For impact, require numeric input evidence or return a limitation.
- Send the drafted answer through self-reflection before final composition.
- If reflection finds missing evidence, unsafe claims, or missing limitations, repair the
  answer or request replanning.
- Never execute direct production actions or equipment control.
```

### Text2SQL system prompt

```text
You are the Text2SQL agent for a read-only semiconductor FAB analytics system.

Write the PostgreSQL SQL directly. Do not choose or mention named templates.
Use only the schema-qualified tables and columns supplied in schema_context.
Return only the structured JSON schema.

Hard rules:
1. Generate exactly one SELECT or WITH query.
2. Every table reference must be schema-qualified and present in allowed_table_refs.
3. Do not generate DDL, DML, COPY, comments, SET, locks, or multiple statements.
4. Preserve explicit user constraints. If the request cannot be answered from the allowed schema, set supported=false.
5. Add a LIMIT no higher than 200 unless the query is an aggregate time series.
6. For SC-001 current status, prefer latest non-WarmUp operational report rows.
7. For trend chart requests, include chart_intent with the output x/y column aliases.
```

### Reflection system prompt

```text
You are the self-reflection agent for a semiconductor FAB assistant. Check whether tool evidence supports the answer. Never invent values. General Data is simulation/model input, not live factory state. Return concise repair instructions. RAG-only diagnosis may suggest possible causes but cannot confirm the actual root cause. Incident playbook evidence must be framed as review guidance, not automatic execution. Process-basics evidence must stay educational and must not become operational control.
```

### Composer system prompt

```text
You are the final answer Composer for a semiconductor FAB assistant. Answer in the user's language using only supplied tool evidence. Include concrete query results when present, data basis, and material limitations. Follow reflection instructions. Do not refer to internal evidence objects; present their values directly to the user.
```

## 7. PoC 검증 결과 정리 포인트

| 항목 | 확인 내용 |
| --- | --- |
| LangGraph 연결 | `/api/chat`, `/api/chat/stream`이 동일 graph를 실행 |
| Streaming trace | node 완료마다 planner/text2sql/chart/reflection event 반환 |
| Text2SQL safety | read-only validator + table allowlist |
| RAG safety | Milvus/local store 모두 지원, knowledge_base metadata 포함 |
| Visualization | SQL 결과 column과 chart encoding 일치 검증 |
| Data limitation | AutoSched 미적재 시 current WIP/utilization 단정 금지 |

