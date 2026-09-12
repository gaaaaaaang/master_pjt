import pytest
from app.agents.intent import analyze_request, enrich_analysis, resolved_request_context
from app.sub_agent.snapshot_queries import METRICS
from app.sub_agent.text2sql import plan_text2sql
from sqlglot import exp, parse_one


@pytest.mark.parametrize("fab", ["fab10", "fab11", "fab12", "fab13"])
@pytest.mark.parametrize("value", ["임의 지연 현상", "unbound_business_label", "yield_percent,unknown_column"])
def test_unbound_model_labels_cannot_become_executable_metric_context(fab, value):
    question = f"{fab} etch의 현상을 분석해줘"
    analysis = analyze_request(question)
    result = enrich_analysis(analysis, [{"name": "metric", "value": value, "raw_text": "현상"}])
    assert "metric" not in resolved_request_context(result.slots)
    assert result.question == question
    assert result.slots["fab_id"] == analysis.slots["fab_id"]


@pytest.mark.parametrize(("value", "column"), [
    ("QUEUE_TIME", "avg_queue_minutes"), ("대기 시간", "avg_queue_minutes"),
    ("UTILIZATION", "utilization_percent"), ("YIELD_PERCENT", "yield_percent"),
    ("완료 LOT", "lot_completions"),
])
def test_semantic_metric_survives_request_handoff_and_reaches_the_correct_sql_column(value, column):
    question = "FAB12 etch의 해당 지표를 알려줘"
    result = enrich_analysis(analyze_request(question), [{"name": "metric", "value": value, "raw_text": "해당 지표"}])
    context = resolved_request_context(result.slots)
    catalog = {"fab12.live_process_snapshots_fab12": {
        "logical_table": "live_process_snapshots", "data_source_type": "simulation_snapshot",
        "columns": [{"name": name} for name in ["interval_start", "interval_end", "fab_id", "area", *METRICS]],
    }}
    query = plan_text2sql(question, query_type="status", fab=context["fab"], metric=context["metric"], database_catalog=catalog)
    assert query.sql
    selected = {item.name for item in parse_one(query.sql, dialect="postgres").find_all(exp.Column)}
    assert selected & set(METRICS) == {column}
    assert "fab12.live_process_snapshots_fab12" in query.sql


def test_explicit_metric_remains_authoritative_over_model_suggestions():
    analysis = analyze_request("FAB13 photo 수율을 알려줘")
    result = enrich_analysis(analysis, [{"name": "metric", "value": "wip", "raw_text": "수율"}])
    assert result.slots["metric"] == analysis.slots["metric"]
