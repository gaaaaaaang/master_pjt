# FAB AI Assistant

SMT2020 기반 FAB 운영 질의를 처리하는 FastAPI + LangGraph + React Vite 애플리케이션입니다.

## 현재 상태

- 프런트: React Vite 채팅 UI
- 백엔드: FastAPI
- orchestration: LangGraph
- agent 경로: Planner -> Supervisor -> Text2SQL -> RAG/CaseSearch/Impact -> Visualization -> Reflection -> Composer
- Text2SQL: Azure OpenAI Chat Completions structured output으로 read-only PostgreSQL SELECT/WITH SQL을 생성하고 allowlist 검증 후 실행
- SSE: `/api/chat/stream`에서 node별 trace, final/error event와 elapsed/timeout/cancellation telemetry 제공

현재 RAG, CaseSearch, Impact는 실제 저장소/계산 모델 연결 전 placeholder limitation을 반환합니다.
AutoSched report table이 없는 환경에서는 live/current status 질문이 `data_unavailable`로 종료됩니다.

## 실행

```bash
cp .env.example .env
uv sync --extra dev
uv run uvicorn app.main:app --reload
cd frontend
npm install
npm run dev
```

- API 문서: `http://localhost:8000/docs`
- React Vite: `http://localhost:5173`

## 테스트

```bash
uv run pytest
```

주요 fixture:

- `tests/fixtures/text2sql_fab10_eval.json`: fab10 Text2SQL 대표 질문 세트
- `tests/fixtures/scenario_acceptance_questions.json`: SC-001~SC-004 acceptance 질문 세트
