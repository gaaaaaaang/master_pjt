import importlib.util
import json
from pathlib import Path

import pytest
from app.schemas.chat import Evidence

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/evaluate_rag_search.py"
spec = importlib.util.spec_from_file_location("rag_search_evaluator", SCRIPT)
evaluator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluator)


def test_reference_mention_does_not_count_as_procedure():
    text = "playbook_id\nPB-EX-001\nExample references PB-HL-001"
    assert evaluator.covered_units(text, {"PB-HL-001"}) == set()


def test_primary_playbook_is_relevant():
    assert evaluator.covered_units("playbook_id\nPB-HL-001\nHold procedure", {"PB-HL-001"}) == {
        "PB-HL-001"
    }


def test_empty_slices_do_not_divide_by_zero():
    result = evaluator.summarize([])
    assert result["mrr"] is None
    assert result["abstention_accuracy"] is None


def test_missing_half_of_compound_evidence_is_not_complete(tmp_path):
    content = "playbook_id\nPB-HL-001\nHold procedure"
    store = tmp_path / "corpus.jsonl"
    store.write_text(
        json.dumps({"chunk_id": "a", "content": content, "knowledge_base": "incident_playbook"})
        + "\n"
    )

    def retrieve(*args, **kwargs):
        return [
            Evidence(
                source_type="rag_chunk", title="hold", content=content, metadata={"chunk_id": "a"}
            )
        ]

    report = evaluator.evaluate(
        [
            {
                "id": "compound",
                "query": "hold and equipment",
                "knowledge_base": "incident_playbook",
                "expected_markers": ["PB-HL-001", "PB-EQ-001"],
            }
        ],
        store_path=store,
        retrieve_fn=retrieve,
    )
    assert report["metrics"]["hit_rate"] == 1
    assert report["metrics"]["recall_at_k"] == 0.5
    assert report["metrics"]["complete_evidence_rate"] == 0


def test_baseline_requires_immutable_hex_ref():
    with pytest.raises(ValueError):
        evaluator.baseline_retriever("main; echo bad")
