from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_root_describes_local_services() -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["docs"] == "/docs"
    assert response.json()["frontend"] == "http://localhost:8501"


def test_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_status_chat_works_in_mock_mode() -> None:
    response = client.post("/api/chat", json={"message": "지금 fab10 WIP 몇 개야?"})
    assert response.status_code == 200
    body = response.json()
    assert body["query_type"] == "status"
    assert "AutoSched" in " ".join(body["limitations"])


def test_meta_reflects_shell_stack() -> None:
    response = client.get("/api/meta")
    assert response.status_code == 200
    assert response.json() == {
        "frontend": "streamlit",
        "backend": "fastapi",
        "agent": "text2sql-initial",
    }
