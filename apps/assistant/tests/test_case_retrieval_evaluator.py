import json
from pathlib import Path

from scripts.evaluate_case_retrieval import evaluate_retrieval


def test_case_evaluator_reports_recall_and_mrr(tmp_path: Path) -> None:
    store = tmp_path / "cases.jsonl"
    records = [
        {
            "case_id": "wrong",
            "case_type": "simulated_reference",
            "summary": "generic queue review",
            "cause": "unknown",
            "actions": ["review"],
            "outcome": "pending",
            "source": "test",
        },
        {
            "case_id": "right",
            "case_type": "verified",
            "summary": "Dry_Etch Queue Time and WIP increase",
            "cause": "station down",
            "actions": ["check utilization"],
            "outcome": "recovered",
            "source": "test",
            "verification": {
                "incident_at": "2026-08-31T14:00:00+09:00",
                "verified_at": "2026-09-01T09:00:00+09:00",
                "verified_by": "test-reviewer",
                "evidence_refs": ["TEST-EVENT-1"],
            },
        },
    ]
    store.write_text(
        "\n".join(json.dumps(record, ensure_ascii=False) for record in records),
        encoding="utf-8",
    )
    cases = [
        {
            "id": "queue",
            "query": "Dry_Etch Queue Time WIP station down",
            "relevant_case_ids": ["right"],
        }
    ]

    report = evaluate_retrieval(cases, store_path=store, top_k=2)

    assert report["summary"]["recall_at_2"] == 1.0
    assert report["summary"]["mrr"] == 1.0
    assert report["results"][0]["first_relevant_rank"] == 1
