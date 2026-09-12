from app.agents.planner import create_plan
from app.db.fab_catalog import comparison_fabs
from app.sub_agent.fab_comparison import comparison_facts
from app.sub_agent.snapshot_queries import METRICS
from app.sub_agent.text2sql import plan_text2sql

QUESTION = "방금 답한 fab11이랑 fab13의 wip 차이에 대해서 분석해줄래?? 왜 fab11에는 더 쌓였는지 궁금해. 시각화 자료도 그려줄 수 있음 그려줘"


def catalog():
    return {f"{fab}.live_process_snapshots_{fab}": {"columns": [{"name": column} for column in ["interval_start", "interval_end", "fab_id", "area", *METRICS]]} for fab in ["fab11", "fab13"]}


def test_comparison_plans_every_requested_capability():
    plan = create_plan(QUESTION)
    assert plan.status == "ready"
    assert plan.slots["fab_ids"].value == "fab11,fab13"
    assert set(plan.selected_sub_agents) == {"text2sql", "rag", "case_search", "visualization"}


def test_comparison_uses_both_tables_at_a_shared_current_time():
    result = plan_text2sql(QUESTION, database_catalog=catalog())
    assert result.status == "succeeded"
    assert "UNION ALL" in result.sql
    assert "interval_end IN (SELECT interval_end FROM fab13.live_process_snapshots_fab13)" in result.sql
    assert "INTERVAL '168 hours'" not in result.sql
    assert result.plan.chart_intent["series"] == "fab"
    assert result.plan.chart_intent["x"] == "area"


def test_deictic_comparison_uses_successful_user_scopes_only():
    history = [{"role": "user", "content": "FAB11 WIP?", "metadata": {"fab": "fab11"}},
               {"role": "assistant", "content": "예를 들어 FAB10", "metadata": {"status": "succeeded"}},
               {"role": "user", "content": "FAB13 WIP?", "metadata": {"fab": "fab13"}},
               {"role": "assistant", "content": "159", "metadata": {"status": "succeeded"}}]
    assert comparison_fabs("방금 조회한 두 FAB의 WIP 차이를 비교해줘", history) == ["fab11", "fab13"]
    assert comparison_fabs("FAB12 WIP은?", history) == []
    history[-1]["metadata"]["status"] = "failed"
    assert comparison_fabs("둘의 차이를 비교해줘", history) == []


def test_comparison_facts_use_sums_for_stock_and_means_for_rates():
    rows = [{"fab": "FAB11", "area": "etch", "wip_lots": 105, "utilization_percent": 80},
            {"fab": "FAB11", "area": "cmp", "wip_lots": 83, "utilization_percent": 90},
            {"fab": "FAB13", "area": "etch", "wip_lots": 155, "utilization_percent": 70},
            {"fab": "FAB13", "area": "cmp", "wip_lots": 4, "utilization_percent": 80}]
    facts = comparison_facts(rows)
    assert facts["totals"]["FAB11"] == {"wip_lots": "188", "utilization_percent": "85"}
    assert facts["comparisons"][0]["left_minus_right"] == "29"


def test_multi_fab_temporal_chart_keeps_separate_process_series():
    result = plan_text2sql("FAB11과 FAB13의 최근 24시간 WIP 추세 비교", database_catalog=catalog())
    assert result.status == "succeeded"
    assert result.plan.chart_intent["series"] == "comparison_series"
    assert "comparison_series" in result.plan.select_items


def test_period_comparison_does_not_silently_reuse_two_fabs():
    history = [{"role": "user", "content": "FAB11 WIP"},
               {"role": "assistant", "metadata": {"status": "succeeded"}},
               {"role": "user", "content": "FAB13 WIP"},
               {"role": "assistant", "metadata": {"status": "succeeded"}}]
    assert comparison_fabs("어제와 오늘 WIP 차이를 비교해줘", history) == []


def test_comparison_facts_refuse_misaligned_areas_and_observation_times():
    left = {"fab": "FAB11", "area": "etch", "interval_end": "2026-09-12", "wip_lots": 20}
    right = {"fab": "FAB13", "area": "etch", "interval_end": "2026-09-12", "wip_lots": 15}
    assert comparison_facts([left, right])
    assert not comparison_facts([left, right | {"interval_end": "2026-09-11"}])
    assert not comparison_facts([left, right | {"area": "cmp"}])
    assert not comparison_facts([left, left, right])


def test_non_wip_comparison_reports_rate_percentage_point_difference():
    from app.sub_agent.fab_comparison import comparison_summary
    rows = [{"fab": "FAB11", "area": "etch", "yield_percent": 95},
            {"fab": "FAB13", "area": "etch", "yield_percent": 90}]
    text = comparison_summary(rows)
    assert "95%" in text and "90%" in text and "5%p" in text
    assert "비가중 평균" in text


def test_comparison_rejects_unsupported_fab_and_missing_catalog():
    assert plan_text2sql("FAB11과 FAB99 WIP 비교", database_catalog=catalog()).status != "succeeded"
    incomplete = {key: value for key, value in catalog().items() if key.startswith("fab11.")}
    assert plan_text2sql("FAB11과 FAB13 WIP 비교", database_catalog=incomplete).status != "succeeded"


def test_average_rounding_and_investigation_details_pass_evidence_checks():
    from app.sub_agent.fab_comparison import comparison_summary
    from app.sub_agent.reflection import _unsupported_diagnosis_numeric_claims
    rows = [{"fab": "FAB11", "area": "cmp", "wip_lots": 83, "avg_queue_minutes": 17.70, "pm_minutes": 17},
            {"fab": "FAB11", "area": "etch", "wip_lots": 20, "avg_queue_minutes": 17.71, "pm_minutes": 0},
            {"fab": "FAB13", "area": "cmp", "wip_lots": 21, "avg_queue_minutes": 12.35, "pm_minutes": 0},
            {"fab": "FAB13", "area": "etch", "wip_lots": 20, "avg_queue_minutes": 14.20, "pm_minutes": 0}]
    answer = comparison_summary(rows)
    evidence = [{"source_type": "text2sql_plan", "metadata": {"fab_comparison": comparison_facts(rows), "sample_rows": rows}}]
    assert "17.71분" in answer
    assert "cmp 공정을 먼저 확인" in answer
    assert "이력 확인이 필요" in answer
    assert _unsupported_diagnosis_numeric_claims("FAB11과 FAB13 WIP 차이 원인", answer, evidence) == []


def test_all_four_fabs_share_one_observation_boundary():
    from app.db.fab_catalog import ALLOWED_FABS
    tables = {f"{fab}.live_process_snapshots_{fab}": {"columns": [{"name": column} for column in ["interval_start", "interval_end", "fab_id", "area", *METRICS]]} for fab in ALLOWED_FABS}
    result = plan_text2sql("FAB10 FAB11 FAB12 FAB13의 현재 수율 비교", database_catalog=tables)
    assert result.status == "succeeded"
    assert result.sql.count("UNION ALL") == 3
    assert set(result.plan.slots["fab_ids"].value.split(",")) == ALLOWED_FABS
    for fab in ALLOWED_FABS:
        assert f"{fab}.live_process_snapshots_{fab}" in result.sql
    assert result.plan.chart_intent["y"] == "yield_percent"


def test_old_single_fab_refusal_does_not_block_explicit_multi_fab_request():
    history = [{"role": "user", "content": QUESTION},
               {"role": "assistant", "content": "현재 한 번에 하나의 FAB을 조회합니다.", "metadata": {"status": "needs_clarification"}}]
    plan = create_plan(QUESTION, conversation_history=history)
    assert plan.status == "ready"
    assert plan.slots["fab_ids"].value == "fab11,fab13"


def test_trend_defaults_to_fab_totals_and_preserves_explicit_area_breakdown():
    whole = plan_text2sql("FAB11 FAB13 최근 24시간 WIP 추세 비교", database_catalog=catalog())
    areas = plan_text2sql("FAB11 FAB13 최근 24시간 공정별 WIP 추세 비교", database_catalog=catalog())
    assert whole.status == areas.status == "succeeded"
    assert "fab AS comparison_series" in whole.sql
    assert "fab || ' / ' || area AS comparison_series" in areas.sql
    assert " 전체 " in whole.plan.chart_intent["title"]
    assert " 공정별 " in areas.plan.chart_intent["title"]
