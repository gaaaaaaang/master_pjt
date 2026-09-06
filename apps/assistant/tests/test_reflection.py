from app.sub_agent.reflection import reflect_agent_output, verify_response


def test_agent_reflection_passes_evidence_backed_text2sql_result() -> None:
    result = reflect_agent_output(
        agent_name="text2sql",
        agent_intent="Query fab10 WIP.",
        planner_plan={"intent": "Check WIP", "query_type": "status"},
        agent_output={
            "status": "succeeded",
            "summary": "Returned one WIP row.",
            "sql": "SELECT wip FROM fab10.autosched_wip LIMIT 1",
        },
        success_criteria=["Return read-only SQL and result evidence."],
        evidence=[{"source_type": "text2sql_plan"}],
        limitations=[],
        required=True,
    )

    assert result["decision"] == "pass"
    assert result["recommended_action"] == "continue"


def test_agent_reflection_sends_failed_required_agent_to_supervisor() -> None:
    result = reflect_agent_output(
        agent_name="text2sql",
        agent_intent="Query fab10 WIP.",
        planner_plan={"intent": "Check WIP", "query_type": "status"},
        agent_output={"status": "data_unavailable", "summary": "Report is not loaded."},
        success_criteria=["Return read-only SQL and result evidence."],
        evidence=[],
        limitations=["AutoSched report is unavailable."],
        required=True,
    )

    assert result["decision"] == "needs_supervisor_review"
    assert result["recommended_action"] == "supervisor_review"
    assert "Required agent" in result["reason"]


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
