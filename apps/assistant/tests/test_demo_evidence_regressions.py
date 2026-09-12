"""Regressions from live demo answers, with adversarial counterexamples."""
import pytest
from app.sub_agent.reflection import _sounds_like_live_state


@pytest.mark.parametrize("answer", [
    "정적 모델 입력입니다. 최신 또는 실시간 정보가 필요한 경우 별도의 데이터 소스 확인이 필요합니다.",
    "실시간 정보를 확인하려면 운영 데이터 연동이 필요합니다.",
    "추가 공정 영역 또는 실시간 데이터가 필요하시면 별도 요청 바랍니다.",
    "실시간 현황이 아닌 정적 마스터 데이터입니다.",
    "실시간 생산 현황이나 가동률 판단에는 사용하지 말아야 합니다.",
    "For real-time information, a separate operational source is required.",
])
def test_requesting_a_live_source_is_not_claiming_master_data_is_live(answer):
    assert not _sounds_like_live_state(answer)


@pytest.mark.parametrize("answer", [
    "현재 WIP는 142입니다. 실시간 현황입니다. 참고로 모델 입력 데이터입니다.",
    "실시간 설비 수는 90대입니다. 별도 확인이 필요합니다.",
    "The real-time equipment count is 90. A separate source is required for confirmation.",
])
def test_disclaimer_does_not_hide_an_affirmative_live_claim(answer):
    assert _sounds_like_live_state(answer)


def test_readiness_does_not_mistake_tool_row_counts_for_measurements():
    from app.agents.llm_nodes import evidence_readiness
    evidence = [{"source_type":"text2sql_plan", "content":"1개 행 조회", "metadata":{"sample_rows":[{"wip_lots":261}]}}]
    result = evidence_readiness(evidence, {"coverage":{"requirements":[{"status":"satisfied"}]}})
    assert result["is_supported"]
    assert result["warnings"] == []
    missing = evidence_readiness(evidence, {"coverage":{"requirements":[{"status":"unavailable", "description":"제품별 수율"}]}})
    assert not missing["is_supported"]
    assert "제품별 수율" in missing["warnings"][0]


def test_prompt_compaction_preserves_current_evidence_and_original_audit():
    from app.agents.prompt_context import compact_prompt_data
    payload = {"evidence":[{"source_type":"text2sql_plan", "metadata":{"sample_rows":[{"wip":261}]}}],
               "agent_reflections":[{"agent_output":{"metadata":{"handoff":{"upstream_results":["old"]}}}}]}
    compact = compact_prompt_data(payload)
    assert compact["evidence"] == payload["evidence"]
    assert "handoff" not in compact["agent_reflections"][0]["agent_output"]["metadata"]
    assert "handoff" in payload["agent_reflections"][0]["agent_output"]["metadata"]


def test_impact_historical_context_is_compacted_without_removing_current_recovery_context():
    from copy import deepcopy

    from app.agents.prompt_context import compact_prompt_data
    payload = {"execution_context":{"coverage":{"pending_agents":["visualization"]}},
               "evidence":[{"metadata":{"scenario":{"question":"가동률 5%p 감소", "fab":"fab12",
                                                       "execution_context":{"upstream_results":["historical copy"]}},
                                          "inputs":{"baseline_util_percent":79.58},
                                          "formulae":["projected = baseline - 5"],
                                          "estimates":{"projected_util_percent":74.58}}}]}
    original = deepcopy(payload)
    compact = compact_prompt_data(payload)
    assert compact["execution_context"] == payload["execution_context"]
    metadata = compact["evidence"][0]["metadata"]
    assert metadata["scenario"] == {"question":"가동률 5%p 감소", "fab":"fab12"}
    for key in ["inputs", "formulae", "estimates"]:
        assert metadata[key] == payload["evidence"][0]["metadata"][key]
    assert payload == original


def test_full_bounded_rows_are_delivered_separately_from_evidence_sampling():
    from app.sub_agent.result_delivery import query_result_payload, sample_evidence_rows
    from app.sub_agent.text2sql import Text2SQLResult
    rows = [{"wip_lots": i} for i in range(200)]
    result = Text2SQLResult(status="succeeded", query_type="status", answer="200행 조회", rows=rows, row_count=200)
    assert query_result_payload(result)["rows"] == rows
    sample = sample_evidence_rows(rows)
    assert len(sample) == 60 and sample[0] == rows[0] and sample[-1] == rows[-1]


def test_area_count_is_computed_before_long_result_sampling():
    from app.sub_agent.reflection import _unsupported_trend_numeric_claims
    from app.sub_agent.result_delivery import result_cardinality, sample_evidence_rows
    rows = [{"area":f"area_{area}", "wip_lots":100} for area in range(6) for _ in range(25)]
    evidence = [{"source_type":"text2sql_plan", "metadata":{
        "status":"succeeded", "row_count":150, "sample_is_complete":False,
        "sample_rows":sample_evidence_rows(rows), "result_cardinality":result_cardinality(rows),
    }}]
    assert _unsupported_trend_numeric_claims("WIP 추세", "총 6개 공정의 150개 행입니다.", evidence) == []
    assert _unsupported_trend_numeric_claims("WIP 추세", "WIP은 6 LOT입니다.", evidence) == ["6"]


def test_generic_simulation_disclaimer_cannot_hide_a_reached_row_limit():
    from app.sub_agent.reflection import verify_response
    evidence = [{"source_type":"text2sql_plan", "metadata":{
        "status":"succeeded", "row_count":200, "row_limit":200, "limit_reached":True,
        "sample_rows":[{"wip_lots":10}],
    }}]
    omitted = verify_response("WIP은 10입니다. 시뮬레이션 데이터입니다.", evidence=evidence, query_type="status")
    assert omitted["undisclosed_row_limit"]
    disclosed = verify_response("WIP은 10입니다. 반환 한도 200행에 도달해 전체 결과가 아닐 수 있습니다.",
                                evidence=evidence, query_type="status")
    assert not disclosed["undisclosed_row_limit"]


def test_observation_facts_handle_equivalent_aware_and_kst_naive_times():
    from app.sub_agent.result_facts import observation_trends
    rows = [{"observed_at":"2026-09-11T00:00:00", "wip_lots":10},
            {"observed_at":"2026-09-11T01:00:00+09:00", "wip_lots":12}]
    assert observation_trends(rows)[0]["absolute_delta"] == "2"
    rows[1]["observed_at"] = "2026-09-10T15:00:00Z"
    assert observation_trends(rows) == []


def test_observation_facts_distinguish_endpoint_change_from_in_between_variation():
    from app.sub_agent.result_facts import observation_trends
    rows = [{"area":"etch", "observed_at":f"2026-09-{day}T00:00:00", "avg_queue_minutes":value}
            for day, value in [(10,"53.64"),(11,"12.99"),(12,"19.72")]]
    summary = observation_trends(rows)[0]
    assert summary["movement"] == "fluctuating"
    assert summary["display_2dp"]["minimum"] == "12.99"
    assert summary["display_2dp"]["absolute_delta"] == "-33.92"
    assert observation_trends([{**rows[0], "observed_at":"bad"}, rows[1]]) == []


def test_short_korean_calendar_ranges_are_not_extra_metric_claims():
    from app.sub_agent.reflection import _numeric_claims
    assert set(_numeric_claims("지난주는 9월 5~6, 이번주는 9월 7~12 관측. WIP은 184.9 감소.")) == {"184.9"}
    assert set(_numeric_claims("하루 전체의 24시간을 관측한 것은 아닙니다. 소수점 2자리로 표시한 수율은 99.47%입니다.")) == {"99.47"}
    assert set(_numeric_claims("24시간을 실제로 관측했습니다.")) == {"24"}


def test_percent_band_is_an_interval_but_plain_integer_is_still_exact():
    from decimal import Decimal

    from app.sub_agent.reflection import _grounded_numeric_claim
    assert _grounded_numeric_claim("99", Decimal(99), {Decimal("99.298")}, context="수율은 99%대입니다")
    assert not _grounded_numeric_claim("99", Decimal(99), {Decimal("98.9")}, context="수율은 99%대입니다")
    assert not _grounded_numeric_claim("99", Decimal(99), {Decimal("99.298")}, context="수율은 99%입니다")


def test_decimal_next_to_unit_is_not_backtracked_into_a_false_integer_claim():
    from app.sub_agent.reflection import _numeric_claims
    assert set(_numeric_claims("+3.35p, 15.27min, -2.53%p, 1,234.56 lots")) == {"+3.35", "15.27", "-2.53", "1,234.56"}
    assert set(_numeric_claims("FAB12 tool_015 DE_BE_11 값 13.15분")) == {"13.15"}
    assert set(_numeric_claims("1, 2, 3")) == {"1", "2", "3"}


def test_failed_answer_review_keeps_raw_data_but_does_not_publish_rejected_claims(monkeypatch):
    from app.agents.graph import _answer_supervisor_node, initial_graph_state
    from app.schemas.chat import ChatRequest
    from app.sub_agent.text2sql import QueryPlan, Text2SQLResult
    request = ChatRequest(message="FAB12 현재 WIP")
    state = initial_graph_state(request)
    state["plan"] = type("Plan", (), {"query_type":"status"})()
    state.update(status="succeeded", answer="FAB12 WIP은 999개입니다.", text2sql_result=Text2SQLResult(
        status="succeeded", query_type="status", answer="1행 조회", row_count=1, rows=[{"wip_lots":261}],
        plan=QueryPlan(query_type="status", template_id="test", fab_id="fab12", data_source_type="simulation_snapshot")))
    monkeypatch.setattr("app.agents.graph.review_final_answer", lambda **kwargs: {"approved":False, "issues":["wrong WIP"], "correction_applied":False})
    result = _answer_supervisor_node(state)
    assert result["status"] == "failed"
    assert "999" not in result["answer"]
    assert "999" in result["answer_review"]["rejected_answer"]
    assert result["answer_review"]["presentation_fallback"] == "verified_query_rows_only"
    assert "wrong WIP" not in result["limitations"]


def test_fluctuating_chart_cannot_be_described_as_steadily_increasing():
    from app.sub_agent.reflection import _trend_direction_conflicts
    evidence = [{"source_type":"visualization_spec", "metadata":{"status":"succeeded", "trend_summary":[
        {"series":"etch", "movement":"fluctuating", "percent_delta":3.4},
        {"series":"photo", "movement":"increasing", "percent_delta":3.1},
    ]}}]
    assert _trend_direction_conflicts("모든 공정 수율이 꾸준히 증가했습니다.", evidence)
    assert not _trend_direction_conflicts("etch 수율은 꾸준히 증가한 것이 아니며 중간 변동이 있습니다.", evidence)
    assert not _trend_direction_conflicts("photo 수율이 꾸준히 증가했습니다.", evidence)
    assert _trend_direction_conflicts("etch 수율은 96.11%에서 99.47%로 꾸준히 증가했습니다.", evidence)
    assert not _trend_direction_conflicts("etch 수율은 96.11%에서 99.47%가 됐습니다. photo는 꾸준히 증가했습니다.", evidence)
    assert not _trend_direction_conflicts("etch 수율은 96.11%에서 99.47%가 됐지만 꾸준히 증가한 것은 아닙니다.", evidence)
    assert not _trend_direction_conflicts("etch 수율은 초반 96.11%에서 99.47%까지 꾸준히 증가했습니다.", evidence)


def test_downtime_answer_does_not_satisfy_a_utilization_question():
    from app.sub_agent.reflection import _missing_question_metrics, _requested_metric_groups
    assert _requested_metric_groups("비가동률은?") == {"Down": ("down_percent", "down_minutes")}
    assert _missing_question_metrics("가동률은?", "비가동률은 5%입니다.") == ["Utilization"]
    assert _missing_question_metrics("비가동률은?", "Down은 5%입니다.") == []


def test_composer_outage_retains_inherited_fab_and_selection_conditions(monkeypatch):
    from app.agents.llm_nodes import compose_with_llm
    from app.agents.planner import create_plan
    from app.sub_agent.reflection import _missing_selection_constraints

    class Offline:
        def complete_json(self, **kwargs):
            raise RuntimeError("injected outage")

    question = "그럼 지금 공정별 WIP 상위 3개를 보여줘"
    plan = create_plan(question, fab="fab12", llm_client=Offline())
    monkeypatch.setattr("app.agents.llm_nodes.AzureAgentClient", Offline)
    answer = compose_with_llm(question=question, plan=plan, answer_parts=["3개 행 조회"],
                             evidence=[], limitations=["시뮬레이션 관측값"], reflection={})
    assert "FAB12" in answer and "상위 3" in answer
    assert _missing_selection_constraints(question, answer) == []
