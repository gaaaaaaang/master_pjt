from copy import deepcopy

import pytest
from app.agents.llm_nodes import compact_evidence, compact_summaries
from app.rag.grounding import (
    apply_review,
    compose_grounded,
    normalized,
    render_grounded,
    resolve_quotes,
    source_spans,
)

SOURCES = {
    "chunk-a": {
        "content": "품질 영향 가능성이 있고 원인 범위가 확정되지 않은 경우 hold한다.\n납기 압박은 품질 hold 해제의 단독 사유가 될 수 없다.",
        "source_document": "원본_매뉴얼.pdf",
        "page_number": 13,
        "reliability": "simulation_reference",
    }
}
QUOTE = "품질 영향 가능성이 있고 원인 범위가 확정되지 않은 경우 hold한다."


def answer():
    return {
        "status": "supported",
        "claims": [
            {
                "text": "품질 영향 가능성과 미확정 원인 범위가 Hold 조건입니다.",
                "sources": [{"chunk_id": "chunk-a", "quote": QUOTE}],
            }
        ],
    }


def test_citations_are_rendered_from_metadata_and_quotes_are_exposed():
    result = render_grounded(answer(), SOURCES)
    assert result.citations[0]["quote"] == QUOTE
    assert "[1] 원본_매뉴얼.pdf, p.13" in result.answer
    assert "실제 사내 승인 SOP가 아닙니다" in result.answer


@pytest.mark.parametrize("mutation", ["id", "quote", "number", "filename", "empty", "shape"])
def test_unsupported_citation_or_claim_cannot_pass(mutation):
    output = answer()
    claim = output["claims"][0]
    if mutation == "id":
        claim["sources"][0]["chunk_id"] = "invented"
    elif mutation == "quote":
        claim["sources"][0]["quote"] = "품질 영향이 확정된 경우에만 hold한다."
    elif mutation == "number":
        claim["text"] = "고장 후 15분 내 반드시 재가동해야 합니다."
    elif mutation == "filename":
        claim["text"] = "승인_SOP.pdf에서 허용합니다."
    elif mutation == "empty":
        claim["sources"] = []
    else:
        claim["unchecked"] = "fact"
    with pytest.raises(ValueError):
        render_grounded(output, SOURCES)


def test_quote_whitespace_tolerates_pdf_line_breaks_without_changing_words():
    sources = deepcopy(SOURCES)
    sources["chunk-a"]["content"] = QUOTE.replace(" ", "\n")
    assert render_grounded(answer(), sources).status == "supported"


def test_pdf_layout_bullets_and_quote_styles_are_not_treated_as_changed_facts():
    assert normalized("조건 A.\n-\n조건 B.") == normalized("조건 A. 조건 B.")
    assert normalized("file “attach.txt”") == normalized("file 'attach.txt'")
    assert normalized("temperature\n-\n5 degrees") != normalized("temperature 5 degrees")
    assert normalized("re-measure") != normalized("remeasure")


def test_no_sources_does_not_call_model_and_returns_insufficient():
    class Client:
        def complete_json(self, **kwargs):
            raise AssertionError("No evidence must not trigger another model call")

    result = compose_grounded("정확한 노광량?", [], client=Client())
    assert result.status == "insufficient"
    assert result.validation == "no_sources"


def test_model_outage_is_distinguished_from_missing_documents_or_invalid_quotes():
    class Client:
        def complete_json(self, **kwargs):
            raise RuntimeError("injected outage")

    evidence = [{"source_type":"rag_chunk", "title":"Hold", "content":QUOTE,
                 "metadata":{"chunk_id":"chunk-a", "source_document":"원본.pdf"}}]
    result = compose_grounded("hold 조건?", evidence, client=Client())
    assert result.status == "insufficient" and result.validation == "rejected"
    assert result.review["stage"] == "generation_api"
    assert "문서 검색은 완료" in result.answer and "모델을 사용할 수 없습니다" in result.answer
    assert result.citations == []


def test_invalid_model_quote_is_rejected_without_exposing_claim():
    class Client:
        def complete_json(self, **kwargs):
            output = answer()
            output["claims"][0]["sources"][0]["quote"] = "This text is not in the source."
            return output

    evidence = [
        {
            "source_type": "rag_chunk",
            "title": "Hold",
            "content": QUOTE,
            "metadata": {"chunk_id": "chunk-a", "source_document": "원본.pdf"},
        }
    ]
    result = compose_grounded("hold 조건?", evidence, client=Client())
    assert result.validation == "rejected"
    assert result.citations == []
    assert "품질 영향 가능성과" not in result.answer


def test_insufficient_cannot_smuggle_claims():
    output = answer()
    output["status"] = "insufficient"
    with pytest.raises(ValueError):
        render_grounded(output, SOURCES)


def test_post_generation_review_removes_unsupported_claims():
    output = answer()
    output["claims"].append(
        {
            "text": "품질 영향이 확정된 경우에만 Hold합니다.",
            "sources": [{"chunk_id": "chunk-a", "quote": QUOTE}],
        }
    )
    review = {
        "complete": False,
        "coverage": [{"requirement": "Hold condition", "covered": True, "reason": "quote"}],
        "checks": [
            {"claim_index": 0, "supported": True, "reason": "원문 조건 일치"},
            {"claim_index": 1, "supported": False, "reason": "가능성을 확정 조건으로 바꿈"},
        ],
    }
    result = apply_review(output, review, SOURCES)
    assert result.status == "partial"
    assert "확정된 경우에만" not in result.answer
    assert result.validation == "verified_quotes_and_model_review"


def test_incomplete_review_cannot_claim_validation_success():
    with pytest.raises(ValueError):
        apply_review(answer(), {"complete": True, "checks": []}, SOURCES)


def test_selectable_spans_resolve_to_original_text_and_reject_invented_ids():
    sources = deepcopy(SOURCES)
    sources["chunk-a"]["title"] = "Hold"
    documents, quotes = source_spans(sources)
    selected = documents[0]["spans"][0]
    output = {
        "status": "supported",
        "claims": [
            {
                "text": "품질 영향 가능성을 확인해야 합니다.",
                "sources": [{"quote_id": selected["quote_id"]}],
            }
        ],
    }
    resolved = resolve_quotes(output, quotes)
    assert resolved["claims"][0]["sources"][0]["quote"] == selected["quote"]
    assert render_grounded(resolved, sources).citations[0]["chunk_id"] == "chunk-a"
    output["claims"][0]["sources"][0]["quote_id"] = "invented:0"
    with pytest.raises(ValueError):
        resolve_quotes(output, quotes)


def test_long_unbroken_source_does_not_expand_to_one_span_per_character():
    sources = deepcopy(SOURCES)
    sources["chunk-a"].update(title="malformed extraction", content="x" * 3600)
    _, quotes = source_spans(sources)
    assert len(quotes) == 3
    assert all(len(quote["quote"]) == 1200 for quote in quotes.values())


def test_numeric_validation_distinguishes_identifiers_formatting_and_signs():
    from app.rag.grounding import numeric_literals
    assert numeric_literals("SMT2020 CALTYPE P95") == set()
    assert numeric_literals("1,000 1000 1e3") == {1000}
    assert numeric_literals("-5도 +5도") == {-5, 5}
    assert numeric_literals("15분 10mJ") == {15, 10}
    assert numeric_literals(".5도 −.5도") == {0.5, -0.5}
    with pytest.raises(ValueError):
        numeric_literals("1e999999999999999999999999999")


def test_compaction_keeps_document_evidence_once_without_debug_or_planner_prompt():
    evidence = [
        {"source_type": "planner_plan", "content": "pretend evidence"},
        {
            "source_type": "rag_chunk",
            "content": QUOTE,
            "metadata": {
                "chunk_id": "x",
                "retrieval_trace": {"debug": "large"},
                "source_document": "original.pdf",
            },
        },
    ]
    result = compact_evidence(evidence)
    assert len(result) == 1
    assert result[0]["content"] == QUOTE
    assert result[0]["metadata"] == {"chunk_id": "x", "source_document": "original.pdf"}
    assert compact_summaries(
        ["RAG(incident) full repeated text", "SQL summary", "SQL summary"]
    ) == ["SQL summary"]


def test_missing_decision_prerequisite_prevents_complete_answer_status():
    review = {
        "complete": True,
        "checks": [{"claim_index": 0, "supported": True, "reason": "quote is supported"}],
        "coverage": [{"requirement": "Required approval condition", "covered": False,
                      "reason": "The answer omits the decision table prerequisite"}],
    }
    result = apply_review(answer(), review, SOURCES)
    assert result.status == "partial"
    assert result.citations
    assert "일부 항목" in result.answer
    review["coverage"] = []
    with pytest.raises(ValueError, match="coverage"):
        apply_review(answer(), review, SOURCES)


def test_decision_table_rows_have_selectable_literal_evidence_and_conditions():
    from app.rag.grounding import decision_rows
    content = "Decision\nAllowed When\nEvidence\nDelay\nlow risk and approval\nrisk memo\nRAG note:"
    rows = decision_rows(content)
    assert rows[0]["allowed_when"] == "low risk and approval"
    sources = deepcopy(SOURCES)
    sources["chunk-a"].update(title="test", content=content)
    documents, quotes = source_spans(sources)
    row = documents[0]["decision_rows"][0]
    assert row["required_evidence"] == "risk memo"
    assert quotes[row["quote_id"]]["quote"] in content
    assert decision_rows(content.replace("\nrisk memo", "")) == []
    assert decision_rows(content.replace("Allowed When", "Unknown heading")) == []


def test_reliability_disclaimer_describes_cited_sources_not_unused_candidates():
    sources = deepcopy(SOURCES)
    sources["chunk-b"] = deepcopy(sources["chunk-a"])
    sources["chunk-a"]["reliability"] = "verified_sop"
    result = render_grounded(answer(), sources)
    assert "실제 사내 승인 SOP가 아닙니다" not in result.answer
    sources["chunk-a"]["reliability"] = "simulation_reference"
    assert "실제 사내 승인 SOP가 아닙니다" in render_grounded(answer(), sources).answer


def test_public_summary_qualification_is_attached_only_when_cited():
    sources = deepcopy(SOURCES)
    sources["chunk-a"]["reliability"] = "verified_sop"
    sources["unused"] = {**sources["chunk-a"], "reliability": "reference_summary"}
    assert "프로젝트 참고 문서" not in render_grounded(answer(), sources).answer
    sources["chunk-a"]["reliability"] = "reference_summary"
    result = render_grounded(answer(), sources)
    assert "프로젝트 참고 문서" in result.answer
    assert "사내 공식 KPI" in result.answer
    assert result.status == "supported"
    assert len(result.citations) == 1


def test_grounding_uses_actual_evidence_not_only_planned_agent_names(monkeypatch):
    from types import SimpleNamespace

    from app.agents.llm_nodes import compose_with_llm, uses_document_grounding
    from app.rag.grounding import GroundedAnswer

    plan = SimpleNamespace(query_type="diagnosis", selected_sub_agents=["rag", "text2sql"])
    docs = [{"source_type": "rag_chunk"}]
    failed_sql = {"source_type": "text2sql_plan", "metadata": {"status": "data_unavailable"}}
    assert uses_document_grounding(plan, [*docs, failed_sql])
    monkeypatch.setattr("app.agents.llm_nodes.compose_grounded",
                        lambda *_: GroundedAnswer("verified document answer", "partial"))
    grounding = {}
    answer_text = compose_with_llm(
        question="실제 상태와 매뉴얼", plan=plan, answer_parts=[],
        evidence=[*docs, failed_sql], limitations=[], reflection={}, grounding=grounding,
    )
    assert answer_text == "verified document answer"
    assert grounding["status"] == "partial"
    actual_sql = {"source_type": "text2sql_plan", "metadata": {"status": "succeeded", "row_count": 2}}
    assert not uses_document_grounding(plan, [*docs, actual_sql])
    plan.query_type = "knowledge_lookup"
    assert not uses_document_grounding(plan, [*docs, actual_sql])
    assert not uses_document_grounding(plan, [*docs, {"source_type": "case", "content": "case result"}])


def test_explicit_approval_requirements_survive_summary_omission():
    from app.rag.grounding import GroundedAnswer, retain_procedural_requirements

    source = deepcopy(SOURCES)
    source["chunk-a"]["content"] = (
        "PM 연기는 Engineer 승인 없이는 허용하지 않는다.\nPM 이후 dummy run 결과를 기록한다.\n"
        "Decision\nAllowed When\nEvidence\nPM short delay\n"
        "low risk and manager approval\nrisk memo\nRAG note:"
    )
    result = GroundedAnswer("Manager 승인이 필요합니다.", "supported", citations=[{
        "number": 1, "chunk_id": "chunk-a", "quote": "low risk and manager approval",
        "source_document": "원본_매뉴얼.pdf", "page_number": 13,
    }])
    checked = retain_procedural_requirements(result, "누가 PM 연기를 승인해?", source)
    assert "Engineer 승인 없이는 허용하지 않는다" in checked.answer
    assert "low risk and manager approval risk memo" in checked.answer
    assert checked.review["extractive_requirement_count"] == 2
    assert len(checked.citations) == 3
    for citation in checked.citations:
        assert normalized(citation["quote"]) in normalized(source["chunk-a"]["content"])
    refused = GroundedAnswer("근거 없음", "insufficient")
    assert retain_procedural_requirements(refused, "승인?", source).answer == "근거 없음"
    unrelated = GroundedAnswer("qualification 결과", "supported", citations=result.citations)
    assert retain_procedural_requirements(unrelated, "PM 뜻?", source).answer == "qualification 결과"
    records = retain_procedural_requirements(unrelated, "복구 기록?", source)
    assert "PM 이후 dummy run 결과를 기록한다" in records.answer
    assert "Engineer 승인" not in records.answer
