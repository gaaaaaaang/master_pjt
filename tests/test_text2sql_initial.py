from app.sub_agent.text2sql import generate_sql, plan_text2sql


def test_status_query_returns_data_unavailable_until_autosched_loaded() -> None:
    result = plan_text2sql("지금 fab10 WIP 몇 개야?")

    assert result.status == "data_unavailable"
    assert result.query_type == "status"
    assert result.sql is None
    assert "autosched_*" in " ".join(result.limitations)


def test_status_query_parses_fab_before_korean_particle() -> None:
    result = plan_text2sql("fab10에서 Queue Time이 10% 늘면 output 영향은?")

    assert result.status == "data_unavailable"
    assert result.plan is not None
    assert result.plan.fab_id == "fab10"


def test_lotrelease_route_count_line_chart_builds_semantic_query_plan() -> None:
    result = plan_text2sql(
        "fab10의 lotrelease 테이블에서 route_product_3 건수를 날짜 기준으로 라인차트로 그려줘."
    )

    assert result.status == "succeeded"
    assert result.query_type == "trend"
    assert result.plan is not None
    assert result.plan.template_id is None
    assert result.plan.source_tables == ["fab10.lotrelease"]
    assert result.plan.aggregation == "count"
    assert result.plan.filters == [
        {"field": "route_name", "operator": "eq", "value": "Route_Product_3"}
    ]
    assert result.plan.group_by == ["start_date::date"]
    assert result.plan.chart_intent == {
        "type": "line",
        "x": "release_date",
        "y": "lot_count",
        "x_title": "Release date",
        "y_title": "Lot release count",
        "series": "Route_Product_3",
    }
    assert "SELECT start_date::date AS release_date" in (result.sql or "")
    assert "COUNT(*)::bigint AS lot_count" in (result.sql or "")
    assert "FROM fab10.lotrelease" in (result.sql or "")
    assert "WHERE route_name = 'Route_Product_3'" in (result.sql or "")
    assert "GROUP BY start_date::date" in (result.sql or "")
    assert "ORDER BY release_date ASC" in (result.sql or "")


def test_toolgroup_lookup_generates_general_data_sql() -> None:
    result = plan_text2sql("fab10 Dry_Etch toolgroup 목록 보여줘")

    assert result.status == "succeeded"
    assert result.query_type == "master_data_lookup"
    assert result.plan is not None
    assert result.plan.template_id == "master_toolgroups"
    assert result.sql is not None
    assert "FROM fab10.toolgroups" in result.sql
    assert "Dry_Etch" in result.sql


def test_route_lookup_uses_product_specific_allowlisted_table() -> None:
    result = plan_text2sql("fab11 Product_10 route step 보여줘")

    assert result.status == "succeeded"
    assert result.plan is not None
    assert result.plan.template_id == "master_route_steps"
    assert result.sql is not None
    assert "FROM fab11.route_product_10" in result.sql


def test_route_lookup_rejects_missing_fab_route_table() -> None:
    result = plan_text2sql("fab10 Product_1 route step 보여줘")

    assert result.status == "unsupported"
    assert result.sql is None
    assert "route_product_1" in " ".join(result.limitations)


def test_release_lookup_requires_selective_constraint() -> None:
    result = plan_text2sql("fab13 release plan 보여줘")

    assert result.status == "needs_clarification"
    assert result.sql is None
    assert "product" in result.answer


def test_release_lookup_generates_sql_with_product_constraint() -> None:
    result = plan_text2sql("fab13 Product_1 release plan 보여줘")

    assert result.status == "succeeded"
    assert result.query_type == "release_plan_lookup"
    assert result.sql is not None
    assert "fab13.lotrelease_variable_due_dates" in result.sql
    assert "Product_1" in result.sql


def test_missing_fab_asks_for_clarification() -> None:
    result = plan_text2sql("Dry_Etch toolgroup 목록 보여줘")

    assert result.status == "needs_clarification"
    assert result.sql is None
    assert "fab10" in result.answer


def test_generate_sql_keeps_backward_compatible_api() -> None:
    sql = generate_sql("fab10 Dry_Etch toolgroup 목록 보여줘")

    assert "FROM fab10.toolgroups" in sql
