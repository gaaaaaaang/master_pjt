import json

import pytest
from app.agents.graph import _answer_supervisor_node
from app.main import app
from app.schemas.chat import Evidence
from app.sub_agent.rag import EvidenceResult
from fastapi.testclient import TestClient


@pytest.mark.parametrize("validation", ["verified_quotes_and_model_review", "rejected", "no_sources"])
def test_final_supervisor_preserves_grounded_answer_bindings(monkeypatch, validation):
    def unexpected(**kwargs):
        raise AssertionError("Generic review must not rewrite verified source claims")

    monkeypatch.setattr("app.agents.graph.review_final_answer", unexpected)
    state = {
        "answer": "원문의 조건을 보존한 답변 [1]",
        "grounding": {"validation": validation},
        "citations": [{"number": 1, "quote": "원문의 조건"}],
    }
    before = dict(state)
    state.update(_answer_supervisor_node(state))
    assert state["answer"] == before["answer"]
    assert state["citations"] == before["citations"]
    assert state["answer_review"]["approved"] is (validation != "rejected")
    assert state["answer_review"]["correction_applied"] is False


def test_stream_retains_grounded_citations_usage_and_conversation_memory(monkeypatch):
    evidence = Evidence(
        source_type="rag_chunk", title="CMP 기초", content="CMP는 웨이퍼 표면을 평탄화한다.",
        metadata={"chunk_id": "integration-cmp", "source_document": "fixture.txt",
                  "page_number": 1, "knowledge_base": "process_basics"},
    )
    monkeypatch.setattr("app.agents.graph.retrieve_evidence",
                        lambda *a, **k: EvidenceResult([evidence], {}, []))
    with TestClient(app) as client:
        response = client.post("/api/chat/stream", json={"message": "CMP 공정이 뭐야?"})
    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
    final = next(event["data"] for event in events if event["type"] == "run_completed")
    assert final["status"] == "succeeded"
    assert final["grounding"]["validation"] == "verified_quotes_and_model_review"
    assert final["citations"][0]["quote"] == evidence.content
    assert final["citations"][0]["source_document"] == "fixture.txt"
    assert final["answer_review"]["correction_applied"] is False
    assert final["conversation_history"][-1]["content"] == final["answer"]
    assert "call_count" in final["model_usage"]
