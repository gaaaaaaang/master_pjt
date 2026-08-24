from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_status_chat_works_in_mock_mode() -> None:
    response = client.post("/api/chat", json={"message": "지금 M2라인 WIP 몇 개야?"})
    assert response.status_code == 200
    assert response.json()["query_type"] == "status"

