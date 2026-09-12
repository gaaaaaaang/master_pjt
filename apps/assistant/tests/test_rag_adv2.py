"""Serving regressions: preserve eligibility, recall and source conditions together."""

import json
from pathlib import Path

import pytest
from app.rag.grounding import (
    GroundedAnswer,
    apply_review,
    compose_grounded,
    retain_procedural_requirements,
)
from app.rag.query import analyze_query
from app.rag.search import search
from app.sub_agent.rag import retrieve_evidence


def record(cid, issue, content):
    return {
        "chunk_id": cid, "collection": "test", "knowledge_base": "incident_playbook",
        "source": "manual.txt", "title": content, "content": content,
        "metadata": {"issue_type": issue},
    }


@pytest.mark.parametrize("phrase", ["지식 문서에 반영", "지식문서 개정", "플레이북 업데이트"])
def test_knowledge_maintenance_paraphrases_route_to_playbooks(phrase):
    plan = analyze_query(f"재발 방지 결과를 {phrase}할 때 검토 기준은?")
    assert "kb_update" in plan.concepts
    assert plan.knowledge_bases == ("incident_playbook",)


def test_issue_mismatch_cannot_exhaust_top_k(tmp_path):
    wrong = record("wrong", "pm_delay", "Queue Time 대기 lot 원인 대응")
    right = record("right", "queue_time_risk", "Queue Time 관리")
    path = tmp_path / "store.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in [wrong, right]))
    result = retrieve_evidence("Queue Time 대기 lot 원인 대응", top_k=1, store_path=path)
    assert [e.metadata["chunk_id"] for e in result.evidence] == ["right"]
    assert result.trace["selected_count"] == result.trace["evidence_count"] == 1
    assert result.trace["issue_filtered_count"] == 1
    assert result.trace["post_filter_dropped_count"] == 0


def test_candidate_filter_also_applies_to_dense_results():
    wrong = record("wrong", "pm_delay", "Queue Time")
    right = record("right", "queue_time_risk", "Queue Time")
    result = search(
        "Queue Time", [wrong, right], top_k=1,
        dense_search=lambda *_: [wrong, right],
        candidate_filter=lambda c: c["metadata"]["issue_type"] == "queue_time_risk",
    )
    assert [c["chunk_id"] for c in result.chunks] == ["right"]
    assert result.trace["dense_candidates"] == 1


@pytest.mark.parametrize("corpus", ["master_pjt.jsonl", "master_pjt_v2.jsonl"])
def test_kb_update_and_both_hold_decisions_survive_serving(corpus):
    path = Path(__file__).resolve().parents[1] / "output/rag" / corpus
    for question, markers in [
        ("재발 방지 내용을 지식 문서에 반영할 때 필요한 검토와 버전 관리 기준은?", ["PB-KB-001"]),
        ("품질 의심 lot의 보류 조건과 해제 조건을 비교해줘", ["PB-HL-001", "PB-HL-002"]),
    ]:
        result = retrieve_evidence(question, top_k=3, store_path=path)
        contents = " ".join(e.content for e in result.evidence)
        assert all(marker in contents for marker in markers)
        assert result.trace["evidence_count"] == len(result.evidence)


def test_extract_preserves_missing_condition_without_repeating_exact_answer():
    approval = "PM 연기는 Engineer 승인 없이는 허용하지 않는다."
    recovery = "PM 이후 dummy run 결과를 기록한다."
    sources = {"a": {
        "content": approval + "\n" + recovery,
        "source_document": "manual.txt", "page_number": 1,
    }}
    result = GroundedAnswer(approval + " [1]", "supported", citations=[{
        "number": 1, "chunk_id": "a", "quote": approval,
        "source_document": "manual.txt", "page_number": 1,
    }])
    result = retain_procedural_requirements(result, "승인과 복구 기록?", sources)
    assert result.answer.startswith(approval)
    assert result.answer.count(approval) == 1
    assert recovery in result.answer
    assert len(result.citations) == 2
    assert result.review["extractive_requirement_count"] == 1


@pytest.mark.parametrize("question", ["검토와 버전 관리 기준은?", "What is the revision policy?"])
def test_lifecycle_status_cannot_be_presented_as_revision_policy(question):
    quote = "validity draft/reviewed/approved"
    sources = {"a": {"content": quote, "source_document": "kb.txt", "page_number": 1,
                     "reliability": "simulation_reference"}}
    output = {"status": "supported", "claims": [
        {"text": "문서 상태는 draft/reviewed/approved입니다.",
         "sources": [{"chunk_id": "a", "quote": quote}]},
        {"text": "버전은 draft/reviewed/approved로 관리합니다.",
         "sources": [{"chunk_id": "a", "quote": quote}]},
    ]}
    review = {"complete": True,
              "coverage": [{"requirement": question, "covered": True, "reason": "accepted"}],
              "checks": [{"claim_index": i, "supported": True, "reason": "accepted"}
                         for i in range(2)]}
    result = apply_review(output, review, sources, question=question)
    assert result.status == "partial"
    assert "문서 상태는" in result.answer
    assert "버전은 draft" not in result.answer
    assert "확인되지 않습니다" in result.answer
    assert result.review["checks"][1]["supported"] is False
    assert review["complete"] is True  # Preserve the original model verdict for audit.
    assert apply_review(output, review, sources, question="문서 상태는?").status == "supported"
    version_quote = "Revision policy: record the revision identifier and change history."
    sources["a"]["content"] += "\n" + version_quote
    output["claims"][1] = {
        "text": "개정 식별자와 변경 이력을 기록합니다.",
        "sources": [{"chunk_id": "a", "quote": version_quote}],
    }
    assert apply_review(output, review, sources, question=question).status == "supported"


@pytest.mark.parametrize("repair_succeeds", [True, False])
def test_numeric_validation_repairs_once_without_relaxing_grounding(repair_succeeds):
    calls = []

    class Client:
        def complete_json(self, **kwargs):
            calls.append(kwargs)
            if kwargs["schema_name"] == "fab_grounded_review":
                return {"complete": True,
                        "coverage": [{"requirement": "hold 조건", "covered": True, "reason": "원문"}],
                        "checks": [{"claim_index": 0, "supported": True, "reason": "원문"}]}
            first = len(calls) == 1
            return {"status": "supported", "claims": [{
                "text": "15분 후 hold 해제" if first or not repair_succeeds else "원인 분석 후 해제한다.",
                "sources": [{"quote_id": "a:0"}],
            }]}

    evidence = [{"source_type": "rag_chunk", "title": "hold",
                 "content": "원인 분석 후 해제한다.", "metadata": {"chunk_id": "a"}}]
    result = compose_grounded("hold 해제 조건?", evidence, client=Client())
    assert result.review["generation_attempts"] == 2
    assert "15분" not in result.answer
    assert calls[1]["input_data"]["validation_error"] == "Claim contains a number absent from its quotes."
    assert result.status == ("supported" if repair_succeeds else "insufficient")
    assert len(calls) == (3 if repair_succeeds else 2)
