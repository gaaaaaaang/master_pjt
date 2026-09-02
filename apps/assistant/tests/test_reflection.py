from app.sub_agent.reflection import verify_response


def test_reflection_warns_when_rag_only_diagnosis_confirms_root_cause() -> None:
    result = verify_response(
        "실제 원인은 병목 설비입니다.",
        evidence=[
            {
                "source_type": "rag_chunk",
                "metadata": {"knowledge_base": "incident_playbook"},
            }
        ],
        limitations=[],
        query_type="diagnosis",
    )

    assert "RAG evidence alone cannot prove the actual root cause." in result["warnings"]


def test_reflection_warns_when_incident_playbook_sounds_executable() -> None:
    result = verify_response(
        "장비를 정지하고 바로 hold를 실행하세요.",
        evidence=[
            {
                "source_type": "rag_chunk",
                "metadata": {"knowledge_base": "incident_playbook"},
            }
        ],
        limitations=["운영자 검토 필요"],
        query_type="knowledge_lookup",
    )

    assert any("Incident playbook" in warning for warning in result["warnings"])


def test_reflection_warns_when_numeric_claim_lacks_sql() -> None:
    result = verify_response(
        "현재 WIP는 128개입니다.",
        evidence=[
            {
                "source_type": "rag_chunk",
                "metadata": {"knowledge_base": "process_basics"},
            }
        ],
        limitations=[],
        query_type="status",
    )

    assert "Numeric operational claims require SQL evidence." in result["warnings"]
