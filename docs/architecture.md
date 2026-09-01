# 초기 아키텍처

```text
사용자
  -> frontend 채팅 UI
  -> FastAPI /api/chat 또는 /api/chat/stream(SSE)
  -> LangGraph
  -> Planner -> Supervisor
  -> Text2SQL -> RAG -> Case Search -> Impact -> Visualization (조건부)
  -> Answer Composer -> Verifier/Self-reflection
```

`/api/chat/stream`은 각 LangGraph node 완료 시 trace event를 보내고, Text2SQL event에는
semantic query plan, 생성 SQL, 조회 column/row count/sample row를 포함한다. Visualization
event는 chart type과 x/y encoding 및 조회 row를 포함한다. 최종 event는 answer, SQL, chart,
evidence, limitations, reflection을 함께 반환한다.

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
