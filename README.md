# FAB AI Assistant

SMT2020 기반 FAB 운영 질의를 처리하는 FastAPI + LangGraph + React Vite 애플리케이션입니다.

## 현재 상태

- 프런트: React Vite 채팅 UI
- 백엔드: FastAPI
- orchestration: LangGraph
- agent 경로: Planner -> Supervisor -> Text2SQL -> RAG/CaseSearch/Impact -> Visualization -> Reflection -> Composer
- Text2SQL: Azure OpenAI Chat Completions structured output으로 read-only PostgreSQL SELECT/WITH SQL을 생성하고 allowlist 검증 후 실행
- SSE: `/api/chat/stream`에서 node별 trace, final/error event와 elapsed/timeout/cancellation telemetry 제공

RAG는 페이지·절차 단위 문서 검색, BM25와 Milvus 의미 검색 결합, 리랭킹, 원문 인용 검증을
지원합니다. 문서 전용 답변은 생성 후 주장과 질문 항목별 근거를 다시 검토합니다.
현재 corpus는 SMT2020 시뮬레이터 설명서와 시뮬레이션 돌발상황 매뉴얼이며 실제 승인 SOP가 아닙니다.
CaseSearch와 Impact의 실제 저장소/계산 모델 연결은 별도 과제입니다.
AutoSched report table이 없는 환경에서는 live/current status 질문이 `data_unavailable`로 종료됩니다.

## 실행

```bash
cp .env.example .env
uv sync --extra dev --extra rag
PYTHONPATH=apps/assistant/src uv run uvicorn app.main:app --reload
cd apps/web
npm ci
npm run dev
```

`.env`에 본인의 API 키를 설정한 뒤 실행합니다. 기본 BM25 **검색**은 로컬에서 수행하지만,
전체 chat의 Planner·Supervisor·답변 생성은 모델 API를 호출합니다. `MOCK_MODE`는 현재
chat 경로의 API 호출 차단 스위치가 아닙니다. 웹 페이지를 열기만 해서는 평가 배치를 실행하지 않습니다.

Milvus 인덱스 구축·하이브리드 활성화·실제 API 검증은 [RAG 실행 안내](docs/rag_search_runbook.md)를
참고하세요. [추가 고도화 검증 기록](docs/rag_extension_20260908.md)에서 검색 적중률,
인용 검증, 답변 완전성의 결과와 한계를 구분해 확인할 수 있습니다.

- API 문서: `http://localhost:8000/docs`
- React Vite: `http://localhost:5173`

## 테스트

```bash
uv run pytest
```

주요 fixture:

- `apps/assistant/tests/fixtures/text2sql_fab10_eval.json`: fab10 Text2SQL 대표 질문 세트
- `apps/assistant/tests/fixtures/scenario_acceptance_questions.json`: SC-001~SC-004 acceptance 질문 세트
