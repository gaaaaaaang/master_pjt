import json
from types import SimpleNamespace

import httpx
import pytest
from app.agents.llm import AzureAgentClient
from app.rag.grounding import compose_grounded


@pytest.mark.parametrize("temperature", [None, 0.0])
def test_sampling_is_explicit_only_for_opted_in_clients(monkeypatch, temperature):
    monkeypatch.setattr("app.agents.llm.get_settings", lambda: SimpleNamespace(
        openai_api_key="test-only", openai_model="gpt-4.1", openai_endpoint="https://test.invalid",
        openai_api_version="test",
    ))
    payloads = []

    def handler(request):
        payloads.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok": true}'}}]})

    original = httpx.Client
    monkeypatch.setattr("app.agents.llm.httpx.Client", lambda **kwargs: original(
        **kwargs, transport=httpx.MockTransport(handler),
    ))
    result = AzureAgentClient(temperature=temperature)._complete_json(
        system_prompt="test", input_data={}, output_schema={"required": ["ok"]}, schema_name="test",
    )
    assert result == {"ok": True}
    if temperature is None:
        assert "temperature" not in payloads[0]
    else:
        assert payloads[0]["temperature"] == 0.0


def test_document_generation_opts_into_zero_temperature(monkeypatch):
    seen = []

    class Client:
        def __init__(self, *, temperature):
            seen.append(temperature)

        def complete_json(self, **kwargs):
            return {"status": "insufficient", "claims": []}

    monkeypatch.setattr("app.rag.grounding.AzureAgentClient", Client)
    result = compose_grounded("없는 수치?", [{
        "source_type": "rag_chunk", "title": "manual", "content": "장비 정지 기록 절차.",
        "metadata": {"chunk_id": "a"},
    }])
    assert seen == [0.0]
    assert result.status == "insufficient"
