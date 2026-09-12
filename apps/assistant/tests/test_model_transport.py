import asyncio
from threading import Event

import httpx
import pytest
from app.agents.llm import AzureAgentClient
from app.agents.usage import UsageLedger, model_call, model_http_client, usage_scope


def mock_clients(monkeypatch):
    clients, requests = [], []
    original = httpx.Client

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"choices":[{"message":{"content":'{"ok":true}'}}], "usage":{"total_tokens":1}})

    def create(**kwargs):
        client = original(transport=httpx.MockTransport(respond), **kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr(httpx, "Client", create)
    return clients, requests


def fixture_model():
    model = AzureAgentClient()
    model.api_key, model.endpoint, model.model = "fixture-key", "https://model.invalid", "fixture-model"
    return model


def invoke_fixture(model):
    with model_call("fixture", model.model):
        return model._complete_json(system_prompt="fixture", input_data={},
                                    output_schema={"required":["ok"]}, schema_name="fixture")


def install_handler(monkeypatch, handler):
    original = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))


def good_response():
    return httpx.Response(200, json={"choices":[{"message":{"content":'{"ok":true}'}}], "usage":{"total_tokens":1}})


def test_network_timeout_is_not_repeated_by_later_stages_and_a_new_request_can_recover(monkeypatch):
    requests = []

    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            raise httpx.ReadTimeout("fixture timeout", request=request)
        return good_response()

    install_handler(monkeypatch, handler)
    model, first, next_request = fixture_model(), UsageLedger(), UsageLedger()
    with usage_scope(first):
        with pytest.raises(RuntimeError, match="fixture timeout"):
            invoke_fixture(model)
        with pytest.raises(RuntimeError, match="earlier in this request"):
            invoke_fixture(model)
    assert len(requests) == 1
    assert first.snapshot()["network_call_count"] == 1
    assert first.snapshot()["skipped_calls"] == 1
    assert first.snapshot()["unreported_usage_calls"] == 1
    with usage_scope(next_request):
        assert invoke_fixture(model) == {"ok":True}
    assert len(requests) == 2
    first.cancel()
    next_request.cancel()


@pytest.mark.parametrize("mode", ["invalid_content", "http_400", "http_503"])
def test_model_content_and_http_responses_do_not_disable_review_or_repair_calls(monkeypatch, mode):
    requests = []

    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            if mode.startswith("http_"):
                return httpx.Response(int(mode.removeprefix("http_")), text="fixture response")
            return httpx.Response(200, json={"choices":[{"message":{"content":"invalid json"}}]})
        return good_response()

    install_handler(monkeypatch, handler)
    model, ledger = fixture_model(), UsageLedger()
    with usage_scope(ledger):
        with pytest.raises(RuntimeError):
            invoke_fixture(model)
        assert invoke_fixture(model) == {"ok":True}
    assert len(requests) == 2
    assert ledger.snapshot()["skipped_calls"] == 0
    ledger.cancel()


def test_network_failure_does_not_suppress_a_different_model_endpoint(monkeypatch):
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.host == "model.invalid":
            raise httpx.ConnectError("fixture connection failure", request=request)
        return good_response()

    install_handler(monkeypatch, handler)
    model, ledger = fixture_model(), UsageLedger()
    with usage_scope(ledger):
        with pytest.raises(RuntimeError):
            invoke_fixture(model)
        model.endpoint = "https://other-model.invalid"
        assert invoke_fixture(model) == {"ok":True}
    assert len(requests) == 2
    ledger.cancel()


def test_planner_timeout_also_prevents_repeat_wait_in_the_text2sql_client(monkeypatch):
    from app.sub_agent.text2sql import OpenAIText2SQLClient

    requests = []

    def handler(request):
        requests.append(request)
        raise httpx.ConnectTimeout("fixture timeout", request=request)

    install_handler(monkeypatch, handler)
    model, sql_model, ledger = fixture_model(), OpenAIText2SQLClient(), UsageLedger()
    for attribute in ("api_key", "endpoint", "model", "api_version"):
        setattr(sql_model, attribute, getattr(model, attribute))
    with usage_scope(ledger):
        with pytest.raises(RuntimeError):
            invoke_fixture(model)
        with pytest.raises(RuntimeError, match="earlier in this request"):
            sql_model._complete_payload({"response_format":{"json_schema":{"name":"fixture_sql"}}})
    assert len(requests) == 1
    assert ledger.snapshot()["skipped_calls"] == 1
    ledger.cancel()


def test_graph_request_reuses_model_client_without_caching_answers_or_credentials(monkeypatch):
    clients, requests = mock_clients(monkeypatch)
    ledger = UsageLedger()
    model = AzureAgentClient()
    model.api_key, model.endpoint, model.model = "fixture-key", "https://model.invalid", "fixture-model"
    with usage_scope(ledger):
        for index in range(3):
            with model_call("fixture", "fixture-model"):
                assert model._complete_json(system_prompt="fixture", input_data={"index":index},
                                            output_schema={"required":["ok"]}, schema_name="fixture") == {"ok":True}
    assert len(clients) == 1 and len(requests) == 3
    assert all(request.headers["api-key"] == "fixture-key" for request in requests)
    assert "api-key" not in clients[0].headers
    assert len({request.content for request in requests}) == 3
    assert ledger.snapshot()["reported_total_tokens"] == 3
    ledger.cancel()
    assert clients[0].is_closed


def test_separate_requests_and_timeout_contracts_have_separate_clients(monkeypatch):
    clients, _ = mock_clients(monkeypatch)
    first, second = UsageLedger(), UsageLedger()
    with usage_scope(first):
        with model_http_client(45) as a:
            pass
        with model_http_client(30) as b:
            pass
        with model_http_client(45) as repeated:
            assert repeated is a
    with usage_scope(second), model_http_client(45) as c:
        assert c is not a and c is not b
    assert len(clients) == 3
    first.cancel()
    assert a.is_closed and b.is_closed and not c.is_closed
    second.cancel()
    assert c.is_closed


@pytest.mark.asyncio
async def test_cancellation_waits_for_inflight_worker_then_releases_pool(monkeypatch):
    clients, _ = mock_clients(monkeypatch)
    ledger = UsageLedger()
    entered, finish = Event(), Event()

    def worker():
        with model_http_client(45) as client:
            entered.set()
            assert finish.wait(2)
            assert not client.is_closed
        with pytest.raises(RuntimeError, match="cancelled"), model_http_client(45):
            pass

    with usage_scope(ledger):
        task = asyncio.create_task(asyncio.to_thread(worker))
        assert await asyncio.to_thread(entered.wait, 2)
        ledger.cancel()
        assert not clients[0].is_closed
        finish.set()
        await task
    assert clients[0].is_closed


def test_standalone_model_client_keeps_context_manager_cleanup(monkeypatch):
    clients, _ = mock_clients(monkeypatch)
    with model_http_client(12) as client:
        assert not client.is_closed
    assert clients == [client] and client.is_closed


def test_shared_connection_is_closed_only_after_the_last_active_lease(monkeypatch):
    clients, _ = mock_clients(monkeypatch)
    ledger = UsageLedger()
    with usage_scope(ledger):
        with model_http_client(45) as first:
            with model_http_client(45) as second:
                assert first is second
                ledger.cancel()
                ledger.cancel()
                assert not first.is_closed
            assert not first.is_closed
        assert first.is_closed
    assert len(clients) == 1


def test_supervisor_closes_request_connections_when_graph_raises(monkeypatch):
    from app.agents.supervisor import Supervisor
    from app.schemas.chat import ChatRequest
    clients, _ = mock_clients(monkeypatch)

    class BrokenGraph:
        def stream(self, state, **kwargs):
            with usage_scope(state["usage_ledger"]), model_http_client(45):
                raise RuntimeError("fixture graph failure")

    monkeypatch.setattr("app.agents.graph.build_agent_graph", BrokenGraph)
    with pytest.raises(RuntimeError, match="fixture graph failure"):
        Supervisor().run(ChatRequest(message="fixture"))
    assert len(clients) == 1 and clients[0].is_closed


@pytest.mark.asyncio
@pytest.mark.parametrize("disconnected", [False, True])
async def test_sse_interruption_closes_idle_model_connections_without_saving_an_exchange(monkeypatch, disconnected):
    from app.api import routes
    from app.schemas.chat import ChatRequest
    from app.services.conversation_memory import ConversationMemory

    clients, _ = mock_clients(monkeypatch)
    memory = ConversationMemory()
    ledgers = []

    class PartialGraph:
        async def astream(self, state, **kwargs):
            ledgers.append(state["usage_ledger"])
            with usage_scope(state["usage_ledger"]), model_http_client(45):
                pass
            yield {"fixture":{"stream_event":{"type":"node_completed", "node":"fixture", "message":"fixture", "data":{}}}}
            await asyncio.Event().wait()

    class RequestConnection:
        async def is_disconnected(self):
            return disconnected

    monkeypatch.setattr(routes, "build_agent_graph", PartialGraph)
    monkeypatch.setattr(routes, "conversation_memory", memory)
    response = routes.chat_stream(ChatRequest(message="fixture", conversation_id="fixture-stream"), RequestConnection())
    stream = response.body_iterator
    assert "run_started" in await anext(stream)
    next_event = await anext(stream)
    if disconnected:
        assert "run_cancelled" in next_event
        with pytest.raises(StopAsyncIteration):
            await anext(stream)
    else:
        assert "node_completed" in next_event
        assert not clients[0].is_closed
        await stream.aclose()
    assert clients[0].is_closed
    with pytest.raises(RuntimeError, match="cancelled"):
        ledgers[0].ensure_active()
    assert memory.get_history("fixture-stream") == []


def test_rate_limit_is_not_repeated_by_later_stages_but_next_question_can_retry(monkeypatch):
    requests = []

    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(429, json={"error": {"code": "rate_limit_tpm"}})
        return good_response()

    install_handler(monkeypatch, handler)
    model, first, second = fixture_model(), UsageLedger(), UsageLedger()
    with usage_scope(first):
        with pytest.raises(RuntimeError, match="429"):
            invoke_fixture(model)
        with pytest.raises(RuntimeError, match="rate limit"):
            invoke_fixture(model)
    assert len(requests) == 1
    assert first.snapshot()["skipped_calls"] == 1
    with usage_scope(second):
        assert invoke_fixture(model) == {"ok": True}
    first.cancel()
    second.cancel()
