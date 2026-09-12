import copy
import json
from decimal import Decimal
from random import Random

import pytest
from app.agents.context_encoding import decode_context, encode_context
from app.agents.evidence_contract import answer_evidence_contract


@pytest.mark.parametrize("seed", range(12))
def test_lossless_context_across_changed_scopes_values_orders_and_missing_cells(seed):
    rng = Random(seed)
    rows = [{"fab": f"fab{rng.randrange(10, 14)}", "area": rng.choice(["cmp", "etch", "photo"]),
             "at": f"2026-09-{i % 12 + 1:02d}T00:00:00+09:00", "wip_lots": rng.randrange(0, 10000),
             "yield_percent": Decimal(str(rng.random() * 100)), "nullable": None,
             "zero": 0, "valid": False, "quoted": "자료: {}\\n\n한국어"} for i in range(70)]
    evidence = {"status": "succeeded", "sample_rows": rows, "unit": "LOT", "sample_is_complete": False}
    payload = {"question": "같은 기간의 다른 공정을 비교해줘", "evidence": [evidence],
               "upstream": {"evidence": [copy.deepcopy(evidence)]},
               "history": [{"role": "user", "content": "이전 요청"}]}
    before = copy.deepcopy(payload)
    encoded, stats = encode_context(payload)
    assert decode_context(encoded) == json.loads(json.dumps(payload, default=str))
    assert payload == before
    assert stats["context_sent_bytes"] < stats["context_original_bytes"] * 0.7


def test_equal_values_from_different_scopes_do_not_erase_evidence_identity():
    records = [{"value": 159, "area": "cmp", "timestamp": "2026-09-12"} for _ in range(20)]
    payload = {"evidence": [{"fab": fab, "provenance": source, "rows": records}
                            for fab, source in [("fab11", "verified"), ("fab13", "generated")]]}
    encoded, _ = encode_context(payload)
    assert decode_context(encoded) == payload


@pytest.mark.parametrize("reserved", ["$ref", "$table", "context_encoding"])
def test_encoding_marker_in_source_data_is_not_interpreted_as_an_alias(reserved):
    payload = {"document": {reserved: "untrusted " * 400}, "metadata": {"missing": None}}
    encoded, stats = encode_context(payload)
    assert stats["context_encoding"] == "plain"
    assert json.loads(encoded) == payload


def test_heterogeneous_rows_preserve_absent_keys_separately_from_null():
    rows = [{"a": 0}, {"a": None}, {"b": 0}, {"a": False}] * 40
    encoded, _ = encode_context({"rows": rows})
    assert decode_context(encoded) == {"rows": rows}


def test_contract_keeps_available_observations_when_documents_or_candidates_are_missing():
    evidence = [{"source_type": "text2sql_plan", "metadata": {
        "status": "succeeded", "row_count": 100, "sample_rows": [{"metric": 0}],
        "sample_is_complete": False, "limit_reached": True}},
        {"source_type": "diagnosis_synthesis", "metadata": {
            "candidate_causes": [], "required_verification": ["aligned event history"]}}]
    contract = answer_evidence_contract(evidence)
    assert contract["queries"][0]["observations_available"]
    assert contract["queries"][0]["row_limit_reached"]
    assert not contract["queries"][0]["sample_is_complete"]
    assert contract["diagnosis"][0]["status"] == "none_supported"
    assert not contract["confirmed_root_cause_supported"]
    assert contract["documents"] == []


def test_document_and_case_strength_do_not_promote_hypothesis_to_root_cause():
    evidence = [{"source_type": "rag_chunk", "metadata": {"reliability": "verified"}},
                {"source_type": "diagnosis_synthesis", "metadata": {
                    "candidate_rankings": [{"issue_type": "maintenance", "support_level": "strong_candidate"}],
                    "missing_evidence": ["current event log"]}}]
    contract = answer_evidence_contract(evidence)
    assert contract["diagnosis"][0]["status"] == "candidates_only"
    assert not contract["confirmed_root_cause_supported"]
    assert not contract["queries"]


def test_failed_query_and_unexecuted_diagnosis_do_not_become_successful_evidence():
    evidence = [{"source_type": "text2sql_plan", "metadata": {
        "status": "failed", "sample_rows": [{"wip": 188}], "metric_summaries": [{"movement": "increasing"}]}}]
    contract = answer_evidence_contract(evidence)
    assert not contract["queries"][0]["observations_available"]
    assert not contract["queries"][0]["temporal_summaries"]
    assert contract["diagnosis_evidence_status"] == "not_evaluated"


def test_conditional_estimates_retain_assumptions_formulas_and_limitations():
    metadata = {"status": "succeeded", "assumptions": ["constant arrivals"],
                "formulae": ["capacity * utilization_ratio"], "limitations": ["no causal model"]}
    contract = answer_evidence_contract([{"source_type": "impact_calculation", "metadata": metadata}])
    calculation = contract["conditional_calculations"][0]
    for key in ("assumptions", "formulae", "limitations"):
        assert calculation[key] == metadata[key]


def test_actual_model_transport_uses_shared_contract_and_records_sizes_only(monkeypatch):
    import httpx
    from app.agents.context_encoding import ENCODING_INSTRUCTION
    from app.agents.evidence_contract import with_evidence_contract
    from app.agents.llm import AzureAgentClient
    from app.agents.prompt_context import compact_prompt_data
    from app.agents.usage import UsageLedger, model_call, usage_scope

    captured = []
    original_client = httpx.Client

    def respond(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok":true}'}}]})

    monkeypatch.setattr(httpx, "Client", lambda **kw: original_client(transport=httpx.MockTransport(respond), **kw))
    model = AzureAgentClient()
    model.endpoint, model.api_key = "https://fixture.invalid", "fixture-key"
    rows = [{"fab": "fab12", "wip_lots": i, "area": "etch"} for i in range(70)]
    evidence = [{"source_type": "text2sql_plan", "metadata": {"status": "succeeded", "sample_rows": rows}}]
    data = {"evidence": evidence, "execution_context": {"evidence": copy.deepcopy(evidence)}}
    ledger = UsageLedger()
    with usage_scope(ledger), model_call("fixture", "fixture"):
        model._complete_json(system_prompt="base instruction", input_data=data,
                             output_schema={"required": ["ok"]}, schema_name="fixture")
    ledger.cancel()
    messages = captured[0]["messages"]
    assert ENCODING_INSTRUCTION in messages[0]["content"]
    expected, _ = with_evidence_contract(data)
    assert decode_context(messages[1]["content"]) == compact_prompt_data(expected)
    record = ledger.snapshot()["calls"][0]
    assert record["context_sent_bytes"] < record["context_original_bytes"]
    assert "fixture-key" not in json.dumps(record)
    assert "sample_rows" not in json.dumps(record)


def test_recovery_and_composer_share_the_same_active_evidence_boundaries():
    from app.agents.evidence_contract import with_evidence_contract
    evidence = [{"source_type": "diagnosis_synthesis", "metadata": {"candidate_causes": []}}]
    composer, _ = with_evidence_contract({"evidence": evidence})
    recovery, _ = with_evidence_contract({"execution_context": {"active_results": {"rag": {"evidence": evidence}}}})
    assert recovery["answer_evidence_contract"] == composer["answer_evidence_contract"]
