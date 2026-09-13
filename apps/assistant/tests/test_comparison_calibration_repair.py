from types import SimpleNamespace

import pytest
from app.agents.planner import create_plan
from app.agents.supervisor import review_final_answer
from app.sub_agent.fab_comparison import comparison_facts, comparison_summary


def review(answer):
    rows = [
        {
            "fab": "FAB11",
            "area": "cmp",
            "interval_end": "2026-09-12 00:00:00+09:00",
            "wip_lots": 188,
        },
        {
            "fab": "FAB13",
            "area": "cmp",
            "interval_end": "2026-09-12 00:00:00+09:00",
            "wip_lots": 159,
        },
    ]
    evidence = [
        {
            "source_type": "text2sql_plan",
            "content": comparison_summary(rows),
            "metadata": {
                "status": "succeeded",
                "sql": "SELECT fab, area, wip_lots FROM comparison_result",
                "sample_rows": rows,
                "fab_comparison": comparison_facts(rows),
            },
        },
        {
            "source_type": "diagnosis_synthesis",
            "content": "현재 근거로는 원인 후보를 제시할 수 없습니다.",
            "metadata": {
                "conclusion_level": "candidate_only",
                "candidate_causes": [],
                "candidate_issue_types": [],
                "similar_cases": [],
            },
        },
    ]
    question = "FAB11과 FAB13 WIP 차이가 왜 발생했어?"
    client = SimpleNamespace(
        complete_json=lambda **kwargs: {
            "approved": True,
            "issues": [],
            "corrected_answer": None,
            "reason": "fixture model missed the contradiction",
        }
    )
    return review_final_answer(
        question=question,
        answer=answer,
        plan=create_plan(question),
        evidence=evidence,
        limitations=["동일 관측 시각 기준입니다."],
        llm_client=client,
    )


@pytest.mark.parametrize(
    "claim",
    [
        "정비를 원인 후보로 볼 수 있습니다.",
        "정비 시간이 증가한 것이 최근 WIP 증가에 기여했을 가능성이 있습니다.",
    ],
)
def test_unsupported_candidate_is_rejected_without_a_canned_comparison(claim):
    result = review(
        f"FAB11 WIP 188LOT, FAB13 159LOT 기준입니다. {claim} 현재 근거로는 원인 후보를 제시할 수 없습니다."
    )
    assert not result["approved"]
    assert not result["original_approved"]
    assert result["correction_source"] is None
    assert result["corrected_answer"] is None
    assert not result["correction_applied"]


def test_calibration_repair_does_not_silently_clear_an_invented_number():
    result = review(
        "FAB11 WIP 999LOT, FAB13 159LOT 기준입니다. 정비를 원인 후보로 볼 수 있습니다. 현재 근거로는 원인 후보를 제시할 수 없습니다."
    )
    assert not result["approved"]
    assert not result["correction_applied"]
