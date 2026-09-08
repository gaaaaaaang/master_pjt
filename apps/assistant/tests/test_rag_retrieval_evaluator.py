import json
from pathlib import Path

from scripts.evaluate_rag_retrieval import evaluate_retrieval


def test_evaluator_reports_recall_and_mrr(tmp_path: Path) -> None:
    store = tmp_path / "store.jsonl"
    records = [
        {
            "chunk_id": "wrong",
            "collection": "test",
            "knowledge_base": "incident_playbook",
            "source": "wrong.txt",
            "title": "Generic queue",
            "content": "queue guidance",
            "metadata": {},
        },
        {
            "chunk_id": "right",
            "collection": "test",
            "knowledge_base": "incident_playbook",
            "source": "right.txt",
            "title": "Queue Time 대응",
            "content": "Queue Time 증가와 WIP 병목 대응",
            "metadata": {"issue_type": "queue_time"},
        },
    ]
    store.write_text(
        "\n".join(json.dumps(record, ensure_ascii=False) for record in records),
        encoding="utf-8",
    )
    cases = [
        {
            "id": "queue",
            "knowledge_base": "incident_playbook",
            "query": "Queue Time WIP 병목",
            "relevant_chunk_ids": ["right"],
        }
    ]

    report = evaluate_retrieval(cases, store_path=store, top_k=2)

    assert report["summary"]["recall_at_2"] == 1.0
    assert report["summary"]["mrr"] == 1.0
    assert report["summary"]["incident_issue_alignment_rate"] == 1.0
    assert report["results"][0]["first_relevant_rank"] == 1
