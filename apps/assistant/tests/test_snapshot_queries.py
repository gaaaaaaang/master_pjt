from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from app.db.metadata_catalog import source_type
from app.sub_agent.impact import estimate_output_delta
from app.sub_agent.snapshot_queries import METRICS, build_snapshot_query, comparison_ranges
from app.sub_agent.text2sql import extract_query_slots, plan_text2sql
from sqlglot import exp, parse_one


def catalog(fab):
    columns = ["interval_start", "interval_end", "fab_id", "area", *METRICS]
    return {f"{fab}.live_process_snapshots_{fab}": {
        "logical_table": "live_process_snapshots", "data_source_type": "simulation_snapshot",
        "columns": [{"name": c} for c in columns],
    }}


def test_explicit_top_three_survives_superlative_wording():
    result = plan_text2sql("FAB13에서 지금 WIP이 가장 많은 공정 3개를 보여줘.", database_catalog=catalog("fab13"))
    assert result.sql.endswith("LIMIT 3")
    assert "ORDER BY wip_lots DESC" in result.sql


def test_yesterday_area_averages_use_one_kst_day_and_categorical_chart():
    result = plan_text2sql("FAB11 어제 공정별 평균 대기 시간을 차트로 보여줘", database_catalog=catalog("fab11"))
    today = datetime.now(ZoneInfo("Asia/Seoul")).date()
    assert result.plan.slots["date_start"].value == str(today-timedelta(days=1))
    assert result.plan.slots["date_end"].value == str(today)
    assert result.plan.chart_intent["x"] == "area"
    assert result.plan.chart_intent["type"] == "grouped_bar"
    assert "INTERVAL '168 hours'" not in result.sql


def test_short_hour_window_does_not_collapse_into_two_daily_points():
    result = plan_text2sql("FAB11 etch 최근 24시간 수율 추세", database_catalog=catalog("fab11"))
    assert "date_trunc('hour'" in result.sql
    assert "INTERVAL '24 hours'" in result.sql


def test_automatic_grain_retains_all_areas_within_the_result_budget():
    result = plan_text2sql("FAB11 최근 48시간 공정별 수율 추세", database_catalog=catalog("fab11"))
    assert "INTERVAL '48 hours'" in result.sql
    assert "date_trunc('day'" in result.sql
    assert any("모두 포함" in note for note in result.limitations)
    explicit = plan_text2sql("FAB11 최근 48시간 공정별 수율 시간별 추세", database_catalog=catalog("fab11"))
    assert "date_trunc('hour'" in explicit.sql


def test_hour_window_survives_a_followup_without_repeating_the_number():
    history = [{"role":"user", "content":"FAB11 최근 24시간 공정별 수율 추세"}]
    slots = extract_query_slots("그 기간 평균 수율이 가장 낮은 공정", fab="fab11", conversation_history=history)
    assert slots["relative_period"].value == "last_24_hours"
    result = plan_text2sql("그 기간 평균 수율이 가장 낮은 공정", fab="fab11", conversation_history=history,
                          database_catalog=catalog("fab11"))
    assert "INTERVAL '24 hours'" in result.sql
    assert "AVG(yield_percent)" in result.sql


def test_diagnosis_of_the_same_process_keeps_the_observed_trend_window():
    history = [{"role":"user", "content":"FAB12 etch 최근 24시간 Queue Time 추세"}]
    question = "왜 같은 공정의 Queue Time이 늘었어? 원인 후보를 알려줘"
    slots = extract_query_slots(question, fab="fab12", conversation_history=history)
    assert slots["relative_period"].value == "last_24_hours"
    current = extract_query_slots("왜 같은 공정의 현재 Queue Time이 높아?", fab="fab12", conversation_history=history)
    assert "relative_period" not in current


def test_today_current_uses_latest_observation_within_the_day_not_daily_average():
    result = plan_text2sql("FAB12 오늘 현재 WIP", database_catalog=catalog("fab12"))
    assert "MAX(interval_end)" in result.sql
    assert "WHERE interval_end >= TIMESTAMPTZ" in result.sql
    assert "AVG(wip_lots)" not in result.sql
    average = plan_text2sql("FAB12 오늘 평균 WIP", database_catalog=catalog("fab12"))
    assert "AVG(wip_lots)" in average.sql


@pytest.mark.parametrize("scope,dimension", [("공정별", "area"), ("전체", "period_label")])
def test_period_average_is_one_value_per_target_not_an_unsolicited_daily_series(scope, dimension):
    result = plan_text2sql(f"FAB12 최근 3일 {scope} WIP 평균", database_catalog=catalog("fab12"))
    assert result.plan.expected_result_shape == "period_summary"
    assert "date_trunc" not in result.sql
    assert "AVG(wip_lots)" in result.sql
    assert result.plan.chart_intent["x"] == dimension


def test_period_flow_total_sums_each_returned_interval_once():
    result = plan_text2sql("FAB12 최근 3일 공정별 완료 LOT 합계", database_catalog=catalog("fab12"))
    assert result.plan.expected_result_shape == "period_summary"
    assert "SUM(lot_completions)" in result.sql


def test_completed_lot_mean_and_mixed_period_aggregations_preserve_the_requested_operator():
    mean = plan_text2sql("FAB12 최근 3일 공정별 완료 LOT 평균", database_catalog=catalog("fab12"))
    assert mean.plan.expected_result_shape == "period_summary"
    assert "AVG(lot_completions)" in mean.sql
    mixed = plan_text2sql("FAB12 최근 3일 공정별 WIP 평균과 완료 LOT 합계", database_catalog=catalog("fab12"))
    assert mixed.plan.expected_result_shape == "period_summary"
    assert "AVG(wip_lots)" in mixed.sql and "SUM(lot_completions)" in mixed.sql


def test_period_flow_threshold_uses_the_same_aggregate_as_the_displayed_metric():
    mean = plan_text2sql("FAB12 최근 3일 공정별 완료 LOT 평균 10 이상", database_catalog=catalog("fab12"))
    assert mean.status == "succeeded"
    assert "AVG(lot_completions) AS lot_completions" in mean.sql
    assert "HAVING AVG(lot_completions) >= 10" in mean.sql
    total = plan_text2sql("FAB12 최근 3일 공정별 완료 LOT 합계 10 이상", database_catalog=catalog("fab12"))
    assert total.status == "succeeded"
    assert "HAVING SUM(lot_completions) >= 10" in total.sql


@pytest.mark.parametrize("metric,aggregate,column", [
    ("WIP", "SUM", "wip_lots"),
    ("완료 LOT", "SUM", "lot_completions"),
    ("평균 수율", "AVG", "yield_percent"),
    ("평균 가동률", "AVG", "utilization_percent"),
])
def test_current_whole_fab_threshold_filters_the_result_not_individual_areas(metric, aggregate, column):
    result = plan_text2sql(f"FAB11 현재 전체 {metric} 50 이상인 경우 보여줘", database_catalog=catalog("fab11"))
    tree = parse_one(result.sql, read="postgres")
    assert tree.args["having"].sql() == f"HAVING {aggregate}({column}) >= 50"
    assert column not in {c.name for c in tree.args["where"].find_all(exp.Column)}
    assert any("FAB 전체" in note and "임계값" in note for note in result.limitations)


def test_current_area_threshold_still_selects_individual_areas():
    result = plan_text2sql("FAB11 현재 공정별 WIP 30 이상을 보여줘", database_catalog=catalog("fab11"))
    tree = parse_one(result.sql, read="postgres")
    assert tree.args.get("having") is None
    assert "wip_lots" in {c.name for c in tree.args["where"].find_all(exp.Column)}
    assert "area" in tree.named_selects


@pytest.mark.parametrize("question,averaged,summed", [
    ("완료 LOT 평균과 정비 시간 합계", "lot_completions", "pm_minutes"),
    ("완료 LOT 합계와 투입량 평균", "lot_starts", "lot_completions"),
    ("평균 정비 시간과 downtime 합계", "pm_minutes", "down_minutes"),
    ("투입 LOT 평균과 비가동 시간 합계", "lot_starts", "down_minutes"),
])
@pytest.mark.parametrize("period", ["최근 3일", "지난주와 이번주 비교"])
def test_flow_metrics_keep_individual_average_and_total_operators(question, averaged, summed, period):
    result = plan_text2sql(f"FAB12 {period} 공정별 {question}", database_catalog=catalog("fab12"))
    assert result.status == "succeeded"
    # Both appear in the outer aggregation, after the per-timestamp observation CTE.
    outer = result.sql.split("FROM observations")[0].rsplit(") SELECT", 1)[1]
    assert f"AVG({averaged}) AS {averaged}" in outer
    assert f"SUM({summed}) AS {summed}" in outer
    notes = " ".join(result.limitations)
    assert "관측 구간당" in notes and "선택한 구간의 합계" in notes


def test_conflicting_operators_for_one_flow_require_general_query_planning():
    assert build_snapshot_query("FAB12 최근 3일 완료 LOT 평균과 완료 LOT 합계", "trend", "fab12",
                                {"metrics":"lotcomps"}, catalog("fab12")) is None


def test_implicit_followup_preserves_per_metric_operators_until_explicitly_changed():
    from app.schemas.chat import ChatRequest
    from app.services.conversation_memory import ConversationMemory
    memory = ConversationMemory()
    first, _ = memory.prepare_request(ChatRequest(message="FAB12 최근 3일 공정별 완료 LOT 평균과 정비 시간 합계"))
    memory.append_exchange(conversation_id=first.conversation_id, request=first, answer="test")
    history = memory.get_history(first.conversation_id)
    follow = plan_text2sql("그 두 지표 이번주도 보여줘", fab="fab12", conversation_history=history, database_catalog=catalog("fab12"))
    assert "AVG(lot_completions) AS lot_completions" in follow.sql
    assert "SUM(pm_minutes) AS pm_minutes" in follow.sql
    changed = plan_text2sql("그 두 지표 이번주 합계로 보여줘", fab="fab12", conversation_history=history, database_catalog=catalog("fab12"))
    assert "SUM(lot_completions) AS lot_completions" in changed.sql
    assert "AVG(lot_completions) AS lot_completions" not in changed.sql


def test_stock_period_total_is_clarified_without_silently_averaging():
    result = plan_text2sql("FAB12 최근 3일 WIP 누적 합계", database_catalog=catalog("fab12"))
    assert result.status == "needs_clarification"
    assert result.sql is None


def test_threshold_for_process_list_preserves_area_and_percentage_units():
    result = plan_text2sql("FAB12 지금 수율이 99.4% 미만인 공정", database_catalog=catalog("fab12"))
    assert result.status == "succeeded"
    assert "area" in result.plan.select_items
    assert "yield_percent < 99.4" in result.sql


def test_downtime_ratio_does_not_become_utilization_or_snapshot_minutes():
    slots = extract_query_slots("FAB12 etch 현재 비가동률")
    assert slots["metrics"].value == "down_percent"
    assert build_snapshot_query("FAB12 etch 비가동률과 WIP", "status", "fab12",
                                {"area":"etch"}, catalog("fab12")) is None


def test_period_maintenance_duration_is_observed_time_not_master_policy():
    result = plan_text2sql("FAB12 최근 3일 공정별 정비 시간 합계", database_catalog=catalog("fab12"))
    assert result.status == "succeeded"
    assert result.query_type == "status"
    assert result.plan.data_source_type == "simulation_snapshot"
    assert "SUM(pm_minutes) AS pm_minutes" in result.sql


def test_downtime_duration_does_not_request_an_extra_percentage_metric():
    assert extract_query_slots("FAB12 etch downtime")["metrics"].value == "down_minutes"
    assert extract_query_slots("FAB10 etch down_percent")["metrics"].value == "down_percent"
    assert extract_query_slots("FAB10 etch down")["metrics"].value == "down_percent"
    assert extract_query_slots("FAB12 etch 비가동 시간")["metrics"].value == "down_minutes"
    assert extract_query_slots("FAB12 etch PM 시간")["metrics"].value == "pm_minutes"
    assert extract_query_slots("FAB12 etch PM 비율과 PM 시간")["metrics"].value == "pm_percent,pm_minutes"


@pytest.mark.parametrize("fab", ["fab10", "fab11", "fab12", "fab13"])
def test_area_equipment_counts_use_discovered_model_columns(fab):
    tables = {f"{fab}.toolgroups_{fab}": {"logical_table":"toolgroups", "data_source_type":"model_master",
              "columns":[{"name":"area"}, {"name":"number_of_tools"}]}}
    result = plan_text2sql(f"{fab} 공정 영역별 설비 대수 합계", database_catalog=tables)
    assert result.status == "succeeded"
    assert result.plan.template_id == "deterministic_master_area_equipment_counts"
    assert f"FROM {fab}.toolgroups_{fab}" in result.sql
    assert "GROUP BY area" in result.sql
    assert "SUM(number_of_tools)" in result.sql
    assert "현재 가동 중인 실제 설비 대수" in " ".join(result.limitations)


def test_equipment_count_contract_does_not_answer_live_or_filtered_scope():
    from app.sub_agent.text2sql import QuerySlot, _grounded_equipment_counts
    tables = {"fab12.toolgroups_fab12": {"columns":[{"name":"area"}, {"name":"number_of_tools"}]}}
    for question, slots in [("현재 공정별 가동 중인 설비 대수", {}),
                            ("공정별 설비 대수 합계", {"product":QuerySlot("Product_a", "parser", 1, "제품 A")})]:
        assert _grounded_equipment_counts(question, slots, "fab12", tables) is None


def test_multi_metric_native_query_keeps_shorthand_aliases_as_well_as_wip():
    result = plan_text2sql("FAB12 etch 현재 WIP과 util, cycleavg를 알려줘", database_catalog=catalog("fab12"))
    assert result.status == "succeeded"
    assert {"wip_lots", "utilization_percent", "avg_cycle_hours"} <= set(result.plan.select_items)
    queue = plan_text2sql("FAB12 etch 현재 WIP과 queue_time", database_catalog=catalog("fab12"))
    assert {"wip_lots", "avg_queue_minutes"} <= set(queue.plan.select_items)


def test_native_contract_does_not_drop_an_unsupported_metric_or_relabel_capacity():
    assert build_snapshot_query("FAB12 WIP과 PM", "status", "fab12",
                                {"metrics":"wiplotavg,pm_percent"}, catalog("fab12")) is None
    assert build_snapshot_query("FAB12 현재 capacity", "status", "fab12", {}, catalog("fab12")) is None


def test_old_simulation_shortcut_cannot_erase_an_unsupported_metric(monkeypatch):
    calls = []
    def unavailable(self, **kwargs):
        calls.append(kwargs)
        raise RuntimeError("model disabled in test")
    monkeypatch.setattr("app.sub_agent.text2sql.OpenAIText2SQLClient.create_sql", unavailable)
    tables = catalog("fab12")
    tables["fab12.live_process_snapshots_fab12"]["table_pattern"] = "live_process_snapshots_{fab}"
    result = plan_text2sql("FAB12 시뮬레이션 Queue Time과 PM 비율", database_catalog=tables)
    assert calls
    assert result.sql is None and result.status == "failed"


def test_followup_comparison_retains_both_areas_in_sql():
    result = plan_text2sql("그 두 공정 수율은?", fab="fab12", database_catalog=catalog("fab12"),
                          conversation_history=[{"role":"user", "content":"FAB12 etch와 photo WIP 비교"}])
    assert "area IN ('etch', 'photo')" in result.sql


def test_referential_trend_retains_both_requested_metrics():
    history = [{"role":"user", "content":"FAB12 etch 수율과 가동률을 알려줘"},
               {"role":"assistant", "content":"WIP도 예로 조회할 수 있습니다."}]
    result = plan_text2sql("그 두 지표 최근 24시간 추세도 보여줘", fab="fab12", process="etch",
                          metric="yield_percent", conversation_history=history, database_catalog=catalog("fab12"))
    assert result.status == "succeeded"
    assert {"yield_percent", "utilization_percent"} <= set(result.plan.select_items)
    assert "wip_lots" not in result.plan.select_items
    rank = plan_text2sql("그중 가장 높은 공정", fab="fab12", metric="yield_percent",
                        conversation_history=history, database_catalog=catalog("fab12"))
    assert rank.status == "needs_clarification" and rank.sql is None
    assert "지표를 하나" in rank.answer


def test_uncovered_contract_uses_actual_catalog_instead_of_old_no_report_assumption(monkeypatch):
    calls = []
    def stop_before_model(self, **kwargs):
        calls.append(kwargs["schema_context"])
        raise RuntimeError("model unavailable in this regression test")
    monkeypatch.setattr("app.sub_agent.text2sql.OpenAIText2SQLClient.create_sql", stop_before_model)
    actual = catalog("fab13")
    actual["fab13.live_process_snapshots_fab13"]["table_pattern"] = "live_process_snapshots_{fab}"
    result = plan_text2sql("FAB13 현재 장비별 습도", database_catalog=actual)
    assert len(calls) == 1
    assert "fab13.live_process_snapshots_fab13" in calls[0]["tables"]
    assert result.status == "failed"  # The unavailable model is not evidence that data is absent.


def test_product_metric_cannot_drop_the_product_from_area_only_snapshots():
    result = plan_text2sql("FAB12 product A 수율", database_catalog=catalog("fab12"))
    assert result.status == "data_unavailable" and result.sql is None
    assert "제품 구분" in result.answer and "공정 영역별" in result.answer


@pytest.mark.parametrize("metric", ["WIP 가중평균", "수율 최솟값", "수율 중앙값", "시뮬레이션 Queue Time 표준편차"])
def test_special_aggregations_are_not_silently_replaced_by_plain_averages(metric, monkeypatch):
    calls = []
    def unavailable(self, **kwargs):
        calls.append(kwargs["question"])
        raise RuntimeError("model disabled in test")
    monkeypatch.setattr("app.sub_agent.text2sql.OpenAIText2SQLClient.create_sql", unavailable)
    actual = catalog("fab12")
    actual["fab12.live_process_snapshots_fab12"]["table_pattern"] = "live_process_snapshots_{fab}"
    result = plan_text2sql(f"FAB12 최근 3일 공정별 {metric}", database_catalog=actual)
    assert calls and result.status == "failed"
    assert result.sql is None


@pytest.mark.parametrize("failure", ["empty_aggregate", "connection"])
def test_empty_period_and_query_failure_are_distinct(failure, monkeypatch):
    from app.db.read_only import ReadOnlyQueryExecutor, ReadOnlyQueryResult
    from app.sub_agent.text2sql import answer_question
    monkeypatch.setattr("app.sub_agent.text2sql.load_fab_catalog", lambda fab: catalog(fab))
    monkeypatch.setattr("app.sub_agent.text2sql._simulation_availability_suggestions", lambda *args: ([], []))
    class Executor(ReadOnlyQueryExecutor):
        def execute(self, sql):
            if failure == "connection":
                raise RuntimeError("database connection unavailable")
            return ReadOnlyQueryResult(["wip_lots", "observation_count"],
                                      [{"wip_lots":None, "observation_count":0}], 1, sql, 200)
    monkeypatch.setattr("app.sub_agent.text2sql.ReadOnlyQueryExecutor", Executor)
    result = answer_question("FAB12 최근 3일 전체 WIP 평균", execute=True)
    assert result.status == ("data_unavailable" if failure == "empty_aggregate" else "failed")
    assert not result.rows
    if failure == "connection":
        assert "행이 없습니다" not in result.answer


@pytest.mark.parametrize("day", ["2026-09-11", "2026/09/11"])
def test_single_explicit_date_is_a_one_day_scope(day):
    slots = extract_query_slots(f"FAB12 {day} 공정별 WIP")
    assert slots["date_start"].value == "2026-09-11"
    assert slots["date_end"].value == "2026-09-12"


@pytest.mark.parametrize("period", ["최근 0일", "최근 400일", "최근 -2시간", "2026-02-30", "past 0 days", "last 9000 HOURS"])
def test_invalid_period_never_falls_back_to_a_default_window(period):
    result = plan_text2sql(f"FAB11 {period} WIP 추세", database_catalog=catalog("fab11"))
    assert result.status == "needs_clarification"
    assert result.sql is None


def test_single_week_area_comparison_does_not_invent_a_second_week():
    assert comparison_ranges("지난주 etch와 photo의 수율 비교", {}, today=date(2026,9,12)) == []


def test_two_areas_in_one_week_are_compared_using_period_means():
    result = plan_text2sql("FAB13 지난주 etch와 photo의 평균 수율 비교", database_catalog=catalog("fab13"))
    assert result.status == "succeeded"
    assert result.plan.template_id == "deterministic_simulation_observations"
    assert "comparison_period" not in result.sql
    assert "date_trunc" not in result.sql
    assert "GROUP BY area" in result.sql
    assert result.plan.chart_intent["x"] == "area"
    assert result.plan.chart_intent["type"] == "grouped_bar"


def test_explicit_daily_comparison_preserves_time_axis():
    result = plan_text2sql("FAB13 지난주 etch와 photo의 일별 수율 추세 비교", database_catalog=catalog("fab13"))
    assert result.status == "succeeded"
    assert "date_trunc('day'" in result.sql
    assert result.plan.chart_intent["x"] == "observed_at"


def test_unknown_named_line_does_not_return_whole_fab_values():
    result = plan_text2sql("FAB11 C라인의 현재 WIP", database_catalog=catalog("fab11"))
    assert result.status == "data_unavailable"
    assert result.sql is None
    assert result.plan.slots["line"].value == "C"
    assert "공정 영역" in result.answer


def test_context_line_is_not_discarded_by_snapshot_contract():
    result = plan_text2sql("FAB11 현재 WIP", line="M2", database_catalog=catalog("fab11"))
    assert result.status == "data_unavailable"
    assert result.sql is None


def test_original_explicit_fab_wins_over_conflicting_planner_scope():
    result = plan_text2sql("FAB12 현재 WIP", database_catalog=catalog("fab12"),
                          execution_context={"scope":{"fab_id":{"value":"fab11"}}})
    assert result.plan.fab_id == "fab12"
    assert "fab12.live_process" in result.sql


@pytest.mark.parametrize("fab", ["fab10", "fab11", "fab12", "fab13"])
def test_current_wip_sums_only_same_timestamp_areas_and_exposes_basis(fab):
    result = plan_text2sql(f"{fab} 지금 WIP 몇 개야?", database_catalog=catalog(fab))
    assert result.status == "succeeded"
    tree = parse_one(result.sql, read="postgres")
    assert {f"{t.db}.{t.name}" for t in tree.find_all(exp.Table)} == {
        f"{fab}.live_process_snapshots_{fab}"
    }
    assert any(isinstance(n.this, exp.Column) and n.this.name == "wip_lots" for n in tree.find_all(exp.Sum))
    assert "interval_end = (SELECT MAX(interval_end)" in result.sql
    assert {"interval_start", "interval_end", "wip_lots"} <= set(result.plan.select_items)
    assert result.plan.data_source_type == "simulation_snapshot"


def test_yield_does_not_turn_into_wip_and_daily_chart_covers_all_areas():
    result = plan_text2sql("fab12 최근 7일 공정별 수율 추세 그래프로 보여줘", database_catalog=catalog("fab12"))
    assert result.status == "succeeded"
    assert "yield_percent" in result.sql
    assert "wip" not in result.sql
    assert "AT TIME ZONE 'Asia/Seoul'" in result.sql
    assert "+09:00" in result.sql
    assert result.plan.chart_intent["series"] == "area"
    assert result.plan.chart_intent["y"] == "yield_percent"


def test_temporal_wip_is_mean_of_same_timestamp_totals_not_sum_of_snapshots():
    result = plan_text2sql("fab11 WIP 일별 추세", database_catalog=catalog("fab11"))
    tree = parse_one(result.sql, read="postgres")
    assert tree.selects[1].this.sql() == "AVG(wip_lots)"
    cte = next(tree.find_all(exp.CTE)).this
    assert any(n.sql() == "SUM(wip_lots)" for n in cte.find_all(exp.Sum))
    assert "interval_end" in cte.args["group"].sql()


@pytest.mark.parametrize("question", [
    "fab11 Product_3 수율 추세", "fab11 Dry_Etch WIP", "fab11 Wet_Etch WIP",
    "fab11 autosched 보고서 WIP", "fab11 live_process_events 대기시간",
    "fab11 DE_BE_11 WIP", "fab11 Init_Lot_3_24 WIP",
    "fab11 제품별 수율", "fab11 납기 준수율 추세",
])
def test_snapshot_contract_cannot_drop_scopes_or_substitute_sources(question):
    slots = {k: v.value for k, v in extract_query_slots(question).items()}
    assert build_snapshot_query(question, "status", "fab11", slots, catalog("fab11")) is None


def test_schema_availability_and_metric_presence_are_required():
    assert build_snapshot_query("fab11 WIP", "status", "fab11", {}, {}) is None
    schema = catalog("fab11")
    schema["fab11.live_process_snapshots_fab11"]["columns"] = [{"name": "interval_end"}]
    assert build_snapshot_query("fab11 WIP", "status", "fab11", {}, schema) is None


def test_top_n_orders_requested_metric_descending():
    result = plan_text2sql("fab13 공정별 WIP 상위 2개", database_catalog=catalog("fab13"))
    assert result.status == "succeeded"
    assert "ORDER BY wip_lots DESC, area ASC LIMIT 2" in result.sql


def test_simulation_impact_uses_same_period_units_without_merging_different_areas():
    row = {"area": "etch", "interval_end": "2026-09-12T00:00:00+09:00",
           "utilization_percent": 80, "lot_completions": 16, "avg_cycle_hours": 2}
    scenario = {"question": "fab11 etch 가동률이 5%p 떨어지면 처리량 영향은?"}
    result = estimate_output_delta({"rows": [row]}, scenario)
    assert result["status"] == "succeeded"
    assert result["estimates"]["capacity_delta_percent"] == -6.25
    assert result["estimates"]["estimated_lotcomps_delta"] == -1
    assert result["baseline"]["metric_units"]["utilization_percent"] == "percent"
    mixed = estimate_output_delta({"rows": [row, {**row, "area": "cmp"}]}, scenario)
    assert mixed["status"] == "data_unavailable"
    assert mixed["baseline"]["mixed_dimensions"] == ["area"]


@pytest.mark.parametrize("logical", ["fab_process_raw_events", "fab_incidents", "fab_simulation_state"])
def test_simulator_records_are_not_mislabeled_as_model_master(logical):
    assert source_type(logical) == "simulation_snapshot"


def test_weekly_comparison_preserves_both_windows_and_factory_scope():
    assert comparison_ranges("지난주 대비 이번주", {}, today=date(2026, 9, 12)) == [
        (date(2026, 8, 31), date(2026, 9, 7)), (date(2026, 9, 7), date(2026, 9, 13))]
    result = plan_text2sql("fab11 공정별 지난주 대비 이번주 수율 비교", database_catalog=catalog("fab11"))
    assert result.status == "succeeded"
    assert "comparison_period" in result.sql
    assert "area" in result.plan.select_items
    assert result.plan.slots["comparison_date_ranges"].value.count("|") == 1
    assert result.plan.chart_intent["type"] == "grouped_bar"
    assert result.plan.chart_intent["series"] == "area"


def test_area_comparison_uses_current_snapshot_instead_of_inventing_time_trend():
    result = plan_text2sql("fab13 etch와 cmp 현재 수율 비교", database_catalog=catalog("fab13"))
    assert result.status == "succeeded"
    assert "interval_end = (SELECT MAX(interval_end)" in result.sql
    assert "date_trunc" not in result.sql
    assert "'etch'" in result.sql and "'cmp'" in result.sql
    assert result.plan.chart_intent["x"] == "area"


def test_conversation_retains_simulation_area_and_yield_metric():
    from app.schemas.chat import ChatRequest
    from app.services.conversation_memory import ConversationMemory
    memory = ConversationMemory()
    first, _ = memory.prepare_request(ChatRequest(message="fab12 etch 수율 알려줘"))
    memory.append_exchange(conversation_id=first.conversation_id, request=first, answer="test")
    follow, _ = memory.prepare_request(ChatRequest(message="그 공정 WIP도 보여줘", conversation_id=first.conversation_id))
    result = plan_text2sql(follow.message, fab=follow.fab, process=follow.process,
                          database_catalog=catalog("fab12"), query_type="status")
    assert follow.process == "etch"
    assert result.plan.slots["area"].value == "etch"
    assert "area IN ('etch')" in result.sql


def test_followup_period_is_frozen_and_ranking_uses_period_average():
    history = [{"role":"user", "content":"FAB12 최근 7일 공정별 수율 추세", "metadata":{
        "query_period":{"date_start":"2026-09-06", "date_end":"2026-09-13", "relative_period":"last_7_days"},
    }}, {"role":"assistant", "content":"임의로 2027-01-01부터 보세요"}]
    result = plan_text2sql("그 기간 평균 수율이 가장 낮은 공정", fab="fab12", query_type="status",
                          conversation_history=history, database_catalog=catalog("fab12"))
    assert result.plan.slots["date_start"].value == "2026-09-06"
    assert result.plan.slots["date_start"].source == "conversation_context"
    assert "2027" not in result.sql
    assert "AVG(yield_percent) AS yield_percent" in result.sql
    assert "GROUP BY area ORDER BY yield_percent ASC, area ASC LIMIT 1" in result.sql


def test_temporal_threshold_applies_after_aggregation():
    result = plan_text2sql("FAB11 지난주 공정별 평균 WIP 100 이상", database_catalog=catalog("fab11"))
    assert "HAVING AVG(wip_lots) >= 100" in result.sql
    assert "WHERE wip_lots" not in result.sql


def test_worst_followup_uses_inherited_yield_and_lowest_area():
    result = plan_text2sql("그중 제일 나쁜 공정 하나만", fab="fab13", metric="yield_percent",
                          query_type="status", database_catalog=catalog("fab13"))
    assert result.status == "succeeded"
    assert "ORDER BY yield_percent ASC, area ASC LIMIT 1" in result.sql
