from app.agents.router import QUERY_TYPES


def make_plan(query_type: str) -> list[str]:
    plans = {
        "status": ["질의 메타데이터 추출", "Text2SQL 실행", "결과 검증", "답변 작성"],
        "diagnosis": ["추이 SQL 실행", "공정 지식 검색", "근거 결합", "self-reflection", "답변 작성"],
        "impact": ["영향 대상 확인", "영향도 계산", "계산 결과 검증", "답변 작성"],
        "trend": ["비교 기간 확인", "시계열 SQL 실행", "차트 데이터 생성", "답변 작성"],
    }
    return plans.get(query_type, [QUERY_TYPES.get(query_type, "질의 분류"), "답변 작성"])

