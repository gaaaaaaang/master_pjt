# FAB AI Assistant

SMT2020 기반 FAB 운영 질의를 처리하는 AI 채팅 어시스턴트의 초기 코드 골격입니다.

## 범위

- SC-001 현재 상태 조회: Text2SQL 인터페이스
- SC-002 원인 진단: Text2SQL + RAG 인터페이스
- SC-003 영향도 질의: 계산 인터페이스만 제공
- SC-004 추세/비교: 시계열 조회 및 차트 데이터 인터페이스
- Planner, Supervisor, self-reflection, 사용자 피드백 확장 지점

상세 Agent 구현과 실제 FAB DB 스키마는 아직 확정되지 않았으므로 기본 실행은 `MOCK_MODE=true`로 동작합니다.

## 실행

```bash
cp .env.example .env
uv sync --extra dev
uv run uvicorn app.main:app --reload
```

API 문서: `http://localhost:8000/docs`

## 테스트

```bash
uv run pytest
```

## 데이터

원본 SMT2020 파일은 `SMT_2020 - Final/`에 보관합니다. 적재 파이프라인은 `app/data/`에 두고, 원본 파일을 직접 수정하지 않습니다.

