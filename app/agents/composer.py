from app.schemas.chat import Evidence


def compose_mock_answer(query_type: str, message: str) -> tuple[str, list[Evidence], list[str]]:
    limitations = ["현재는 MOCK_MODE로 실행 중이며 실제 FAB DB와 문서 검색 결과가 연결되지 않았습니다."]
    if query_type == "status":
        answer = "요청하신 FAB/라인/공정의 현재 상태를 조회하는 단계입니다. 실제 DB 연결 후 WIP와 기준 시각을 제공합니다."
    elif query_type == "diagnosis":
        answer = "원인 진단은 추이 데이터와 공정 지식을 함께 확인해야 합니다. 현재는 분석 파이프라인 골격만 연결되어 있습니다."
    elif query_type == "trend":
        answer = "비교 기간의 지표를 조회하고 차트로 반환하는 단계입니다. 실제 데이터 연결 후 변화폭과 추세를 제공합니다."
    elif query_type == "impact":
        answer = "영향도 계산 인터페이스가 준비되어 있습니다. 기준 지표와 비교 기간 확정 후 계산 로직을 연결합니다."
    else:
        answer = f"질의 유형({query_type})에 대한 상세 Agent 동작은 설계 확정 후 구현합니다."
    evidence = [Evidence(source_type="system", title="초기 골격", content=f"입력 질의: {message}")]
    return answer, evidence, limitations

