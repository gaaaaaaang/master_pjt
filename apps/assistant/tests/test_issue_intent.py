from app.sub_agent.issue_intent import negated_issue_types

ISSUE_TERMS = {
    "equipment_down": {"down", "고장"},
    "pm": {"pm", "예방정비"},
    "queue_time": {"queue time", "대기시간"},
}


def test_issue_negation_is_local_and_preserves_later_positive_mentions() -> None:
    assert negated_issue_types(
        "장비 down은 아니고 Queue Time만 증가했습니다.", ISSUE_TERMS
    ) == {"equipment_down"}
    assert negated_issue_types(
        "PM 문제는 아니고 장비 down 원인을 봐줘.", ISSUE_TERMS
    ) == {"pm"}
    assert negated_issue_types(
        "초기 down은 아니었지만 이후 down이 발생했습니다.", ISSUE_TERMS
    ) == set()
