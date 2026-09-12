from copy import deepcopy

from app.services.answer_presentation import METRIC_LABELS, present_response
from app.sub_agent.snapshot_queries import METRICS


def response(**updates):
    payload = {
        "status": "succeeded", "query_type": "status",
        "answer": "조회값은 시뮬레이션 데이터이며 실제 FAB 실측값이나 실시간 데이터가 아닙니다.",
        "limitations": ["조회값은 생성된 시뮬레이션 데이터 기준입니다.", "LOT 단위이며 관측 시각은 구간 종료 기준입니다."],
        "query_result": {"rows": [{"interval_end": "2026-09-12 00:00:00+09:00", "wip_lots": 159}]},
        "evidence": [{"source_type": "text2sql_plan", "metadata": {"query_plan": {"data_source_type": "simulation_snapshot", "fab_id": "fab13"}}}],
    }
    return payload | updates


def test_model_answer_is_preserved_with_provenance_retained():
    payload = response()
    evidence = deepcopy(payload["evidence"])
    answer = payload["answer"]
    limitations = list(payload["limitations"])
    result = present_response(payload)
    assert result["answer"] == answer
    assert result["evidence"] == evidence
    assert result["data_sources"][0]["description"] == "PoC용 생성 데이터"
    assert "시뮬레이션" in str(result["data_sources"][0]["details"])
    assert result["limitations"] == limitations


def test_all_supported_metrics_survive_concise_status_presentation():
    assert set(METRIC_LABELS) == set(METRICS)
    payload = response(answer="모델이 작성하고 검증한 답변입니다.", query_result={"rows": [{"interval_end": "2026-09-12", "temperature_c": 23.4, "humidity_percent": 44, "down_minutes": 20}]})
    assert present_response(payload)["answer"] == "모델이 작성하고 검증한 답변입니다."


def test_source_words_do_not_remove_material_scope_or_causal_limits():
    text = "시뮬레이션 데이터만으로 원인을 확정할 수 없습니다. 집계 기준은 LOT 단위입니다."
    result = present_response(response(query_type="diagnosis", answer=text, limitations=[text]))
    assert "원인을 확정할 수 없습니다" in result["answer"]
    assert "원인을 확정할 수 없습니다" in result["limitations"][0]


def test_failure_and_non_generated_answers_are_not_replaced_by_single_row():
    failed = response(status="failed", answer="관측 시각이 서로 달라 비교할 수 없습니다.")
    assert present_response(failed)["answer"] == failed["answer"]
    ordinary = response(evidence=[], limitations=[], answer="관측 이력을 확인했습니다.")
    assert present_response(ordinary)["answer"] == "관측 이력을 확인했습니다."


def test_live_model_provenance_variants_preserve_observation_basis():
    text = "실제 FAB 실적이 아니며, 시뮬레이션 기반 공정 데이터 기준 값입니다. 집계 시각은 2026-09-12입니다."
    answer = present_response(response(answer=text))["answer"]
    assert answer == text
    assert "2026-09-12" in answer
    assert "공정 데이터 기준" in answer


def test_double_escaped_model_paragraphs_are_rendered_without_changing_code():
    from app.services.answer_presentation import normalize_answer_lines
    assert normalize_answer_lines(r"결과입니다.\n\n근거입니다.") == "결과입니다.\n\n근거입니다."
    assert normalize_answer_lines(r"결과\n\n`print('\n')`") == "결과\n\n`print('\\n')`"
    assert normalize_answer_lines(r"`C:\new` 파일") == r"`C:\new` 파일"


def test_slash_snapshot_source_wording_is_neutralized_without_dropping_assumptions():
    text = "시뮬레이션/스냅샷 데이터이며 비례 가정으로 추정했습니다."
    assert present_response(response(answer=text))["answer"] == text


def test_latest_snapshot_source_preserves_causal_limit_and_source_details():
    text = "시뮬레이션 기반 최신 스냅샷 값이며 원인 확정은 불가합니다."
    result = present_response(response(query_type="diagnosis", answer=text))
    assert result["answer"] == text
    assert any(text in source["details"] for source in result["data_sources"])
