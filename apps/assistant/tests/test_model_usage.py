import asyncio

import pytest
from app.agents.usage import UsageLedger, model_call, reported_usage, usage_scope


def test_usage_records_provider_tokens_without_payloads_or_secrets():
    ledger = UsageLedger()
    with usage_scope(ledger), model_call("rerank", "configured-model"):
        reported_usage(
            {
                "prompt_tokens": 120,
                "completion_tokens": 40,
                "total_tokens": 160,
                "prompt": "should never be recorded",
                "api_key": "secret",
            }
        )
    snapshot = ledger.snapshot()
    assert snapshot["reported_total_tokens"] == 160
    assert snapshot["unreported_usage_calls"] == 0
    assert "secret" not in str(snapshot)
    assert "should never be recorded" not in str(snapshot)


def test_missing_usage_is_unknown_and_failed_calls_count():
    ledger = UsageLedger()
    with usage_scope(ledger), pytest.raises(RuntimeError), model_call("compose", "model"):
        raise RuntimeError("do not store this text")
    assert ledger.snapshot()["failed_calls"] == 1
    assert ledger.snapshot()["unreported_usage_calls"] == 1
    assert "do not store this text" not in str(ledger.snapshot())


@pytest.mark.asyncio
async def test_concurrent_requests_do_not_share_usage_and_worker_threads_inherit_scope():
    ledgers = [UsageLedger(), UsageLedger()]

    async def request(ledger, amount):
        def call():
            with model_call("embedding", "model"):
                reported_usage({"total_tokens": amount})

        with usage_scope(ledger):
            await asyncio.to_thread(call)

    await asyncio.gather(request(ledgers[0], 7), request(ledgers[1], 19))
    assert [ledger.snapshot()["reported_total_tokens"] for ledger in ledgers] == [7, 19]
