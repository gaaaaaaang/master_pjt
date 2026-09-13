# FAB AI Assistant

SMT2020 기반 FAB 운영 질의를 처리하는 FastAPI + LangGraph + React Vite 애플리케이션입니다.

## 현재 상태

- 프런트: React Vite 채팅 UI
- 백엔드: FastAPI
- orchestration: LangGraph
- agent 경로: Planner -> Supervisor -> Text2SQL -> RAG/CaseSearch/Impact -> Visualization -> Reflection -> Composer
- Text2SQL: 공통 시뮬레이션 현황·추세는 추출된 지표·기간·공정으로 조회 계약을 구성하며, 그 밖의 질문은 Azure OpenAI structured output의 의미 계획을 SQL로 변환하고 read-only 검증 후 실행
- SSE: `/api/chat/stream`에서 node별 trace, final/error event와 elapsed/timeout/cancellation telemetry 제공

RAG는 페이지·절차 단위 문서 검색, BM25와 Milvus 의미 검색 결합, 리랭킹, 원문 인용 검증을
지원합니다. 문서 전용 답변은 생성 후 주장과 질문 항목별 근거를 다시 검토합니다.
현재 corpus는 SMT2020 시뮬레이터 설명서, 시뮬레이션 돌발상황 매뉴얼 및 공개 공정 흐름 자료 요약이며 실제 승인 SOP가 아닙니다.
CaseSearch는 사례의 검증 수준을 구분하고, Impact는 조회 기준값과 명시된 가정으로 1차 민감도를 계산합니다. 실제 공장에 보정된 인과·예측 모델은 아닙니다.
FAB10~13의 `live_process_snapshots`가 적재된 환경에서는 네 FAB의 최신 공정별 WIP·수율·가동률·대기 시간과 기간 추세를 조회합니다.
“현재”는 각 FAB의 최신 적재 구간을 뜻하며 답변의 기준 시각을 확인해야 합니다. 라인·제품별 데이터가 없는 경우 해당 범위를 공정 전체 값으로 대체하지 않습니다.

`feat/final_adv`의 시연 순서, 데이터 기준, 인덱스 재현과 검산 방법은 [FAB 데모 실행 안내](docs/final_adv_demo_runbook.md)를 참고하세요.
화면 상단 **질문 가이드**에서 FAB10~13별 현황·진단·영향·추세 대화와 문서 지식·대응 절차 질문을 입력할 수 있습니다.

분석 답변은 모델이 작성하고 근거 검증을 통과해야 합니다. 특정 질문의 답변을 하드코딩하거나,
모델·검토 실패를 정해진 분석 문장으로 대체하는 fallback을 추가하지 않습니다. 수정이 필요하면
공통 프롬프트·근거 계약·실행 코드를 개선하고 실모델로 검증합니다.
[기본 질문 복구와 fallback 제거 기록](docs/default_question_recovery_20260913.md)을 참고하세요.

## 실행

고도화된 사용자 화면은 **React (`http://localhost:5173/`)** 입니다.
`uv run streamlit run apps/web/app.py`는 별도로 유지하는 기존 Streamlit 화면(`8501`)을
실행하므로 React 디자인 개선이 표시되지 않습니다. `/trace`는 기존 React 평가 화면입니다.
브랜치 병합 후에는 백엔드도 현재 checkout에서 재시작해야 변경된 에이전트 코드가 로드됩니다.

저장소 루트에서 의존성을 설치하고 백엔드를 실행합니다.

```bash
cp .env.example .env
uv sync --extra dev --extra rag
PYTHONPATH=apps/assistant/src uv run uvicorn app.main:app --reload
```

기존 `.env`가 있으면 복사 단계는 생략해 현재 API·DB 설정을 보존합니다.
로컬 DB는 Docker 실행 후 `docker compose up -d postgres`로 기동합니다.
`/health/ready`는 FAB10~13 데이터의 연결·적재 여부를 확인합니다.

별도 터미널의 저장소 루트에서 React를 실행합니다.

```bash
npm --prefix apps/web ci
npm --prefix apps/web run dev
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

## 사용자 화면 개선 실험 (`fix/frontend_adv`)

React 기본 경로 `/`는 결과 중심 FAB 채팅 화면입니다. 기존 에이전트 평가 화면은 `/trace`에 있습니다.
기존 Streamlit 화면은 별도로 유지되며 React에 자동 이관되지 않습니다.

```sh
cd apps/web
npm install
npm run dev -- --port 5175
npm test
npm run build
```

- 채팅: `http://127.0.0.1:5175/`
- 디자인 예시: `http://127.0.0.1:5175/?preview=trend` (실제 운영 수치 아님)
- 예시 선택기에서 조건 확인, 데이터 없음, 연결 오류 화면을 비교할 수 있습니다.
- 기본 개발 proxy는 `http://127.0.0.1:8000`을 사용합니다. `FAB_API_TARGET`으로 바꿀 수 있습니다.
- 운영 배포에는 `/api`·`/health` reverse proxy 또는 `VITE_API_BASE` 설정이 필요합니다.
- 대화 기록은 현재 탭의 세션 동안 저장됩니다. 분석 범위는 대화마다 설정할 수 있습니다.

디자인 판단, 검증 내용, 남은 제한은 [프런트엔드 검토 기록](docs/frontend_adv_review.md)을 참고하세요.
