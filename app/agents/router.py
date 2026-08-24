QUERY_TYPES = {
    "status": "현재 상태 조회",
    "knowledge": "공정지식 질의",
    "diagnosis": "원인 진단",
    "impact": "영향도 질의",
    "recommendation": "대응 추천",
    "trend": "추세/비교",
    "follow_up": "후속/맥락 질의",
}


def classify_query(message: str) -> str:
    """초기 키워드 라우터. 추후 LLM structured output으로 교체합니다."""
    text = message.lower()
    if any(word in text for word in ("왜", "원인", "증가한 이유", "악화")):
        return "diagnosis"
    if any(word in text for word in ("영향", "얼마나", "기여")):
        return "impact"
    if any(word in text for word in ("지난주", "전주", "추세", "비교", "변화")):
        return "trend"
    if any(word in text for word in ("뭐야", "무엇", "정의", "설명")):
        return "knowledge"
    if any(word in text for word in ("대응", "어떻게", "조치")):
        return "recommendation"
    return "status"

