from types import SimpleNamespace

import pytest
from app.agents.planner import PlannerDecision
from app.agents.supervisor import review_final_answer


def inputs():
    return {"question": "fab10 현재 WIP", "answer": "fab10 현재 WIP=999입니다.",
            "plan": PlannerDecision("ready", "status", "WIP 조회", ["text2sql"], []),
            "evidence": [{"source_type": "text2sql_plan", "metadata": {
                "status": "succeeded", "sample_rows": [{"wiplotavg": 123}], "row_count": 1,
                "sql": "SELECT wiplotavg FROM fab10.autosched_perf_fab10 LIMIT 1",
            }}], "limitations": []}


def test_review_outage_cannot_approve_even_when_numbers_are_valid():
    def unavailable(**kwargs):
        raise RuntimeError("review unavailable")
    request = inputs() | {"answer": "fab10 현재 WIP=123입니다."}
    with pytest.raises(RuntimeError, match="review unavailable"):
        review_final_answer(**request, llm_client=SimpleNamespace(complete_json=unavailable))


def test_revision_is_model_authored_and_revalidated():
    calls = []
    def complete(**kwargs):
        calls.append(kwargs)
        value = 456 if len(calls) == 1 else 123
        return {"approved": False, "issues": ["wrong WIP"], "reason": "revise value",
                "corrected_answer": f"fab10 현재 WIP={value}입니다."}
    result = review_final_answer(**inputs(), llm_client=SimpleNamespace(complete_json=complete))
    assert result["approved"]
    assert result["corrected_answer"] == "fab10 현재 WIP=123입니다."
    assert result["correction_source"] == "model"
    assert len(calls) == len(result["review_attempts"]) == 2
    assert calls[1]["input_data"]["final_answer"] == "fab10 현재 WIP=456입니다."


def test_revision_budget_does_not_end_in_a_canned_answer():
    calls = []
    def complete(**kwargs):
        calls.append(kwargs)
        return {"approved": False, "issues": ["wrong WIP"], "reason": "still wrong",
                "corrected_answer": f"fab10 현재 WIP={500 + len(calls)}입니다."}
    result = review_final_answer(**inputs(), llm_client=SimpleNamespace(complete_json=complete))
    assert not result["approved"]
    assert not result["correction_applied"]
    assert len(calls) == 3
    assert result["correction_source"] is None

@pytest.mark.parametrize("verdict", [None, True, False])
def test_semantic_flags_need_explicit_model_judgement(verdict):
    calls = []
    def complete(**kwargs):
        calls.append(kwargs)
        flags = kwargs["input_data"]["semantic_review_items"]
        return {"approved": True, "issues": [], "reason": "reviewed",
                "corrected_answer": None,
                "semantic_checks": [] if verdict is None else [
                    {"warning_index": i, "violated": verdict, "reason": "evidence reviewed"}
                    for i in range(len(flags))]}
    request = inputs() | {"answer": "fab10 현재 WIP=123입니다.",
                          "limitations": ["조회 시각은 결과 표에 표시됩니다."]}
    result = review_final_answer(**request, llm_client=SimpleNamespace(complete_json=complete))
    assert calls[0]["input_data"]["semantic_review_items"]
    assert result["approved"] is (verdict is False)
    assert not result["correction_applied"]


def test_semantic_approval_cannot_override_an_unsupported_number():
    def complete(**kwargs):
        flags = kwargs["input_data"]["semantic_review_items"]
        return {"approved": True, "issues": [], "reason": "accepted semantics",
                "corrected_answer": None,
                "semantic_checks": [{"warning_index": i, "violated": False, "reason": "reviewed"}
                                    for i in range(len(flags))]}
    result = review_final_answer(**inputs(), llm_client=SimpleNamespace(complete_json=complete))
    assert not result["approved"]
    assert result["deterministic_check"]["blocking_warnings"]
