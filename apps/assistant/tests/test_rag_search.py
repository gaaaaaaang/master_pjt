from dataclasses import asdict
from pathlib import Path

import pytest
from app.rag.ingest import build_chunks_for_paths
from app.rag.query import analyze_query
from app.rag.search import rrf, search


def chunk(cid, content, *, base="incident_playbook", title="", metadata=None):
    return {
        "chunk_id": cid,
        "content": content,
        "title": title,
        "knowledge_base": base,
        "source": cid + ".txt",
        "collection": "test",
        "metadata": metadata or {},
    }


def test_unrelated_question_abstains():
    result = search("내일 서울 날씨", [chunk("a", "장비 고장 초기 대응과 복구")])
    assert result.chunks == []
    assert result.limitations


def test_no_fallback_for_zero_overlap():
    assert not search("CMP 평탄화", [chunk("a", "dispatch 순서", base="process_basics")]).chunks


def test_korean_query_retrieves_english_terminology():
    result = search(
        "예방정비 지연 대응",
        [
            chunk("pm", "Preventive maintenance overdue procedure"),
            chunk("other", "SPC alarm control"),
        ],
    )
    assert result.chunks[0]["chunk_id"] == "pm"


def test_explicit_playbook_id_routes_and_restricts():
    result = search(
        "PB-QT-001",
        [
            chunk("right", "PB-QT-001 Queue Time limit"),
            chunk("other", "PB-EQ-001 Queue Time equipment"),
        ],
    )
    assert [c["chunk_id"] for c in result.chunks] == ["right"]


def test_fab_scope_cannot_leak():
    result = search(
        "fab11 설비 고장",
        [
            chunk("wrong", "설비 고장", metadata={"fab_id": "fab10"}),
            chunk("right", "설비 고장", metadata={"fab_id": "fab11"}),
        ],
    )
    assert [c["chunk_id"] for c in result.chunks] == ["right"]


@pytest.mark.parametrize(
    "query,expected",
    [("팹 12 고장", ("fab12",)), ("M10 설비", ("fab10",)), ("FAB10과 fab11", ("fab10", "fab11"))],
)
def test_query_fabs(query, expected):
    assert analyze_query(query).fab_ids == expected


def test_kb_filter_is_hard():
    result = search(
        "Queue Time",
        [chunk("wrong", "Queue Time", base="process_basics")],
        knowledge_base="incident_playbook",
    )
    assert not result.chunks


def test_withdrawn_document_is_excluded():
    assert not search("고장", [chunk("a", "고장", metadata={"status": "withdrawn"})]).chunks


def test_duplicate_content_is_not_repeated():
    result = search("설비 고장", [chunk("a", "설비 고장"), chunk("b", "설비  고장")])
    assert len(result.chunks) == 1


def test_context_budget_is_hard_and_no_partial_procedure():
    result = search(
        "설비 고장",
        [chunk("long", "설비 고장 " * 100), chunk("short", "설비 고장")],
        context_chars=50,
    )
    assert [c["chunk_id"] for c in result.chunks] == ["short"]
    assert result.trace["context_chars"] <= 50


def test_dense_and_lexical_candidates_are_fused():
    lexical = chunk("lexical", "설비 고장 대응")
    dense = chunk("dense", "장비 고장 점검")

    def dense_search(query, base, limit):
        assert base == "incident_playbook"
        assert limit == 30
        return [dense, lexical]

    result = search("설비 고장", [lexical], dense_search=dense_search)
    assert {c["chunk_id"] for c in result.chunks} == {"lexical", "dense"}
    assert result.trace["retrieval_mode"] == "hybrid"
    assert result.trace["dense_candidates"] == 2
    assert len(result.chunks[0]["metadata"]["retrieval_ranks"]) == 2


def test_dense_failure_degrades_visibly():
    def fail(*args):
        raise RuntimeError("service down")

    result = search("고장", [chunk("a", "고장")], dense_search=fail)
    assert result.chunks
    assert result.trace["retrieval_mode"] == "hybrid_degraded"
    assert result.limitations


def test_rrf_does_not_count_duplicate_hits_twice():
    a = chunk("a", "a")
    result = rrf([[a, a], [a]])
    assert result[0][0] == pytest.approx(2 / 61)


def test_rrf_does_not_compare_raw_backend_score_scales():
    a, b = chunk("a", "a"), chunk("b", "b")
    a["metadata"]["score"] = 100000
    b["metadata"]["score"] = 0.1
    assert rrf([[b, a], [b]])[0][1]["chunk_id"] == "b"


def test_compound_query_collects_both_procedures():
    records = [
        chunk("eq", "설비 고장 초기 대응"),
        chunk("hold", "Lot Hold 격리 절차"),
        chunk("eq2", "설비 고장 초기 대응 확인"),
    ]
    result = search("설비 고장 초기 대응과 Lot Hold 격리 절차", records, top_k=2)
    assert {c["chunk_id"] for c in result.chunks} == {"eq", "hold"}


def test_explicit_negation_not_global():
    plan = analyze_query("장비 고장은 아니고 Queue Time 증가")
    assert "equipment_down" in plan.excluded_concepts
    assert "queue_time" in plan.concepts
    assert "equipment_down" not in plan.concepts


def test_positive_remention_restores_concept():
    plan = analyze_query("장비 고장은 아니고 다른 장비 고장을 검토해")
    assert "equipment_down" in plan.concepts
    assert "equipment_down" not in plan.excluded_concepts


def test_chunk_ids_are_portable(tmp_path: Path):
    paths = []
    for folder in ("one", "two"):
        path = tmp_path / folder / "manual.md"
        path.parent.mkdir()
        path.write_text("# 장비 고장\n\n초기 대응 절차")
        paths.append(path)
    a, b = [
        build_chunks_for_paths([p], collection="test", knowledge_base="incident_playbook")
        for p in paths
    ]
    assert a[0].chunk_id == b[0].chunk_id
    assert a[0].metadata["document_version"] == b[0].metadata["document_version"]


def test_markdown_sections_never_mix(tmp_path: Path):
    path = tmp_path / "manual.md"
    path.write_text("# 설비 고장\n\nPB-EQ-001 고장 대응\n\n# 수율 저하\n\nPB-YD-001 수율 대응")
    records = build_chunks_for_paths([path], collection="test", knowledge_base="incident_playbook")
    assert len(records) == 2
    assert records[0].metadata["playbook_ids"] == ["PB-EQ-001"]
    assert records[1].metadata["playbook_ids"] == ["PB-YD-001"]
    assert "PB-YD-001" not in records[0].content


def test_simulation_provenance_is_propagated(tmp_path: Path):
    path = tmp_path / "manual.md"
    path.write_text("# 범위\n\n시뮬레이션용 자료\n\n# 대응\n\nPB-EQ-001 장비 고장")
    records = build_chunks_for_paths([path], collection="test", knowledge_base="incident_playbook")
    assert all(r.metadata["reliability"] == "simulation_reference" for r in records)
    assert asdict(records[0])["metadata"]["ingestion_version"] == "structure.v2"


@pytest.mark.parametrize("candidate_k", [0, -1, 201])
def test_candidate_budget_validation(candidate_k):
    with pytest.raises(ValueError):
        search("고장", [], candidate_k=candidate_k)
