# FAB AI Assistant

Streamlit 프런트와 FastAPI 백엔드를 분리한 초기 골격입니다.

## 현재 상태

- 프런트: Streamlit
- 백엔드: FastAPI
- agent: 비활성

지금은 화면, API 계약, 연결 경로만 확인할 수 있는 shell 단계입니다.

## 실행

```bash
cp .env.example .env
uv sync --extra dev
uv run uvicorn app.main:app --reload
uv run streamlit run frontend/app.py
```

- API 문서: `http://localhost:8000/docs`
- Streamlit: `http://localhost:8501`

## 테스트

```bash
uv run pytest
```
