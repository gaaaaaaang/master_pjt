# 초기 아키텍처

```text
사용자
  -> frontend 채팅 UI
  -> FastAPI /api/chat 또는 /api/chat/stream(SSE)
  -> LangGraph
  -> Planner -> Supervisor
  -> Text2SQL -> Agent Reflection -> RAG -> Agent Reflection (선택 agent마다 반복)
  -> Case Search -> Impact -> Visualization (조건부, 각 실행 뒤 Agent Reflection)
  -> Verifier/Self-reflection -> Answer Composer
```

`/api/chat`과 `/api/chat/stream`은 동일한 LLM LangGraph를 실행한다. Planner와 Supervisor는
각각 독립된 Azure Chat Completions structured-output 호출로 계획과 실행 승인을 결정한다.
`/api/chat/stream`은 각 LangGraph node 완료 시 trace event를 보내고, Text2SQL event에는
semantic query plan, 생성 SQL, 조회 column/row count/sample row를 포함한다. Visualization
event는 chart type과 x/y encoding 및 조회 row를 포함한다. Reflection은 SQL/tool 근거와 한계를
LLM으로 검토하고, Composer는 그 검토 지시를 반영해 최종 답변을 생성한다. 최종 event는 answer, SQL, chart,
evidence, limitations, reflection을 함께 반환한다.

각 선택 agent 실행 직후 공통 `sub_agent/reflection.py` 계약으로 Planner의 intent/action,
agent output, success criteria, 신규 evidence와 limitation을 검증한다. 결과는 실행 순서대로
`agent_reflections`에 누적한다. `pass`가 아닌 결과는 `supervisor_reviews`에도 기록해 최종
Reflection/Composer와 API 응답에 전달한다. 현재 단계에서는 이 결과로 재시도나 replan을 자동 실행하지 않는다.

## 단계별 구현 순서

1. SMT2020 Excel을 staging table에 적재하고 조회 가능한 스키마를 고정합니다.
2. SC-001 Text2SQL과 read-only SQL 검증을 구현합니다.
3. 공정 문서와 대응 매뉴얼을 별도 collection으로 임베딩합니다.
4. SC-002 원인 진단에서 SQL 근거와 RAG 근거를 결합합니다.
5. SC-004 시계열 조회와 차트 응답을 연결합니다.
6. Planner/Supervisor와 SC-003 영향도, feedback, self-reflection sub-agent를 확장합니다.

## 안전 경계

- DB 계정은 read-only로 제한합니다.
- 설비 제어와 생산 조치 자동 실행은 제공하지 않습니다.
- 모든 답변은 SQL, 문서, 계산 결과 중 사용한 근거와 한계를 반환합니다.

## 상세 설계

- Text2SQL sub-agent의 LangGraph state, node, prompt, validation, fallback 설계는
  `docs/text2sql_agent_design.md`를 기준으로 합니다.
