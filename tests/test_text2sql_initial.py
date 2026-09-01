from app.db.read_only import ReadOnlyQueryResult
from app.sub_agent.text2sql import (
    OpenAIText2SQLClient,
    answer_question,
    generate_sql,
    plan_text2sql,
)


class FakeLLM:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls: list[dict] = []

    def create_sql(self, **kwargs) -> dict:
        self.calls.append(kwargs)
        return self.payload


def llm_payload(sql: str, **overrides) -> dict:
    payload = {
        "supported": True,
        "sql": sql,
        "source_tables": [],
        "select_items": [],
        "filters": [],
        "group_by": [],
        "order_by": [],
        "aggregation": None,
        "expected_result_shape": "rows",
        "chart_intent": None,
        "answer": "LLM이 read-only SQL을 생성했습니다.",
        "limitations": [],
        "confidence": 0.82,
    }
    payload.update(overrides)
    return payload


def test_status_query_reports_llm_call_failure(monkeypatch) -> None:
    class MissingKeyLLM:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def create_sql(self, **kwargs) -> dict:
            raise RuntimeError("OPENAI_API_KEY is not configured.")

    monkeypatch.setattr("app.sub_agent.text2sql.OpenAIText2SQLClient", MissingKeyLLM)

    result = plan_text2sql("지금 fab10 WIP 몇 개야?")

    assert result.status == "failed"
    assert result.query_type == "status"
    assert result.sql is None
    assert "OPENAI_API_KEY" in " ".join(result.limitations)


def test_openai_client_uses_azure_chat_completions_endpoint(monkeypatch) -> None:
    captured = {}

    class FakeResponse:
        status_code = 200
        text = ""

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "choices": [
                    {
                        "message": {
                            "content": '{"supported":false,"sql":"","source_tables":[],"select_items":[],"filters":[],"group_by":[],"order_by":[],"aggregation":null,"expected_result_shape":null,"chart_intent":null,"answer":"unsupported","limitations":[],"confidence":0.1}'
                        }
                    }
                ]
            }

    class FakeHttpClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

        def post(self, url, *, headers, json):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return FakeResponse()

    monkeypatch.setattr("app.sub_agent.text2sql.httpx.Client", FakeHttpClient)

    client = OpenAIText2SQLClient(
        api_key="test-key",
        model="gpt-4.1",
        endpoint="https://skax.ai-talentlab.com",
        api_version="2024-12-01-preview",
    )
    output = client.create_sql(
        question="hello",
        query_type="master_data_lookup",
        fab_id="fab10",
        slots={},
        schema_context={"allowed_table_refs": ["fab10.toolgroups"]},
    )

    assert output["supported"] is False
    assert (
        captured["url"]
        == "https://skax.ai-talentlab.com/openai/deployments/gpt-4.1/chat/completions?api-version=2024-12-01-preview"
    )
    assert captured["headers"]["api-key"] == "test-key"
    assert "Authorization" not in captured["headers"]
    assert captured["json"]["model"] == "gpt-4.1"
    assert captured["json"]["messages"][0]["role"] == "system"
    assert captured["json"]["response_format"]["type"] == "json_schema"
    assert "type" not in captured["json"]["response_format"]["json_schema"]


def test_status_query_parses_fab_before_korean_particle() -> None:
    result = plan_text2sql(
        "fab10에서 Queue Time이 10% 늘면 output 영향은?",
        llm_client=FakeLLM(llm_payload("SELECT * FROM fab10.autosched_perf LIMIT 1")),
    )

    assert result.status == "succeeded"
    assert result.plan is not None
    assert result.plan.fab_id == "fab10"


def test_release_route_is_parsed_before_korean_particle() -> None:
    result = plan_text2sql(
        "fab10 lotrelease에서 Route_Product_3의 2018-01-01 release plan 목록을 보여줘",
        llm_client=FakeLLM(
            llm_payload(
                "SELECT * FROM fab10.lotrelease "
                "WHERE route_name = 'Route_Product_3' LIMIT 20"
            )
        ),
    )

    assert result.status == "succeeded"
    assert result.plan is not None
    assert result.plan.slots["route"].value == "Route_Product_3"


def test_status_query_calls_llm_and_executes_generated_sql(monkeypatch) -> None:
    llm = FakeLLM(
        llm_payload(
            """
SELECT
    'fab10' AS fab_id,
    report_time,
    period,
    stngrp,
    wiplotavg
FROM fab10.autosched_stngrp
WHERE relative = 'Y'
  AND period <> 'WarmUp'
  AND stngrp ILIKE '%Dry_Etch%'
ORDER BY report_time DESC NULLS LAST, source_row_id DESC
LIMIT 20
""".strip(),
            source_tables=["fab10.autosched_stngrp"],
            select_items=["report_time", "period", "stngrp", "wiplotavg"],
            filters=[
                {"field": "relative", "operator": "eq", "value": "Y"},
                {"field": "period", "operator": "neq", "value": "WarmUp"},
                {"field": "stngrp", "operator": "contains", "value": "Dry_Etch"},
            ],
            order_by=["report_time DESC", "source_row_id DESC"],
            expected_result_shape="process_group_status_rows",
        )
    )

    class FakeExecutor:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def validate(self, sql: str) -> str:
            return sql

        def execute(self, sql: str) -> ReadOnlyQueryResult:
            assert "FROM fab10.autosched_stngrp" in sql
            return ReadOnlyQueryResult(
                columns=["fab_id", "stngrp", "wiplotavg"],
                rows=[{"fab_id": "fab10", "stngrp": "Dry_Etch", "wiplotavg": 2256.05}],
                row_count=1,
                sql=sql,
                limit=100,
            )

    monkeypatch.setattr("app.sub_agent.text2sql.ReadOnlyQueryExecutor", FakeExecutor)

    result = answer_question("지금 fab10 Dry_Etch WIP 몇 개야?", execute=True, llm_client=llm)

    assert result.status == "succeeded"
    assert result.query_type == "status"
    assert result.plan is not None
    assert result.plan.template_id is None
    assert result.plan.source_tables == ["fab10.autosched_stngrp"]
    assert len(llm.calls) == 1
    assert llm.calls[0]["schema_context"]["data_source_type"] == "operational_report"
    assert "AutoSched report 기준으로 1개 상태 행" in result.answer


def test_status_query_stays_data_unavailable_when_autosched_table_is_missing(monkeypatch) -> None:
    llm = FakeLLM(
        llm_payload(
            """
SELECT report_time, period, wiplotavg
FROM fab10.autosched_perf
WHERE relative = 'Y'
ORDER BY report_time DESC NULLS LAST, source_row_id DESC
LIMIT 1
""".strip(),
            source_tables=["fab10.autosched_perf"],
        )
    )

    class FakeExecutor:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def validate(self, sql: str) -> str:
            return sql

        def execute(self, sql: str) -> ReadOnlyQueryResult:
            raise RuntimeError('relation "fab10.autosched_perf" does not exist')

    monkeypatch.setattr("app.sub_agent.text2sql.ReadOnlyQueryExecutor", FakeExecutor)

    result = answer_question("지금 fab10 WIP 몇 개야?", execute=True, llm_client=llm)

    assert result.status == "data_unavailable"
    assert result.sql is None
    assert "autosched_*" in " ".join(result.limitations)


def test_lotrelease_route_count_line_chart_uses_llm_generated_sql() -> None:
    llm = FakeLLM(
        llm_payload(
            """
SELECT start_date::date AS release_date,
       COUNT(*)::bigint AS lot_count
FROM fab10.lotrelease
WHERE route_name = 'Route_Product_3'
GROUP BY start_date::date
ORDER BY release_date ASC
""".strip(),
            source_tables=["fab10.lotrelease"],
            select_items=["start_date::date AS release_date", "COUNT(*)::bigint AS lot_count"],
            filters=[{"field": "route_name", "operator": "eq", "value": "Route_Product_3"}],
            group_by=["start_date::date"],
            order_by=["release_date ASC"],
            aggregation="count",
            expected_result_shape="time_series",
            chart_intent={
                "type": "line",
                "x": "release_date",
                "y": "lot_count",
                "x_title": "Release date",
                "y_title": "Lot release count",
                "series": "Route_Product_3",
            },
        )
    )

    result = plan_text2sql(
        "fab10의 lotrelease 테이블에서 route_product_3 건수를 start_date 기준으로 라인차트로 그려줘.",
        llm_client=llm,
    )

    assert result.status == "succeeded"
    assert result.query_type == "trend"
    assert result.plan is not None
    assert result.plan.template_id is None
    assert result.plan.source_tables == ["fab10.lotrelease"]
    assert result.plan.aggregation == "count"
    assert result.plan.chart_intent == {
        "type": "line",
        "x": "release_date",
        "y": "lot_count",
        "x_title": "Release date",
        "y_title": "Lot release count",
        "series": "Route_Product_3",
    }
    assert "GROUP BY start_date::date" in (result.sql or "")
    assert len(llm.calls) == 1


def test_ambiguous_lotrelease_date_basis_asks_for_clarification() -> None:
    llm = FakeLLM(llm_payload("SELECT * FROM fab10.lotrelease LIMIT 1"))

    result = plan_text2sql("fab10 Product_3 lotrelease 일별 추세 보여줘", llm_client=llm)

    assert result.status == "needs_clarification"
    assert result.sql is None
    assert "start_date" in result.answer
    assert "due_date" in result.answer
    assert llm.calls == []


def test_explicit_due_date_lotrelease_adds_date_slots_to_schema_context() -> None:
    llm = FakeLLM(
        llm_payload(
            """
SELECT due_date::date AS due_date,
       COUNT(*)::bigint AS lot_count
FROM fab10.lotrelease
WHERE product_name = 'Product_3'
GROUP BY due_date::date
ORDER BY due_date ASC
""".strip(),
            source_tables=["fab10.lotrelease"],
        )
    )

    result = plan_text2sql(
        "fab10 Product_3 lotrelease를 due_date 기준 일별로 집계해줘",
        llm_client=llm,
    )

    assert result.status == "succeeded"
    schema_context = llm.calls[0]["schema_context"]
    assert schema_context["data_source_type"] == "release_plan"
    assert schema_context["slots"]["date_basis"]["value"] == "due_date"
    assert schema_context["slots"]["date_grain"]["value"] == "day"
    assert "fab10.lotrelease" in schema_context["allowed_table_refs"]


def test_operational_trend_uses_autosched_schema_context() -> None:
    llm = FakeLLM(
        llm_payload(
            """
SELECT report_time::date AS report_date,
       AVG(wiplotavg) AS wiplotavg
FROM fab10.autosched_perf
WHERE relative = 'Y'
GROUP BY report_time::date
ORDER BY report_date ASC
""".strip(),
            source_tables=["fab10.autosched_perf"],
        )
    )

    result = plan_text2sql("fab10 WIP 일별 추세를 보여줘", llm_client=llm)

    assert result.status == "succeeded"
    schema_context = llm.calls[0]["schema_context"]
    assert schema_context["data_source_type"] == "operational_report"
    assert "fab10.autosched_perf" in schema_context["allowed_table_refs"]
    assert "fab10.lotrelease" not in schema_context["allowed_table_refs"]
    assert schema_context["slots"]["metric"]["value"] == "wiplotavg"


def test_pm_and_breakdown_tables_are_available_for_master_lookup() -> None:
    llm = FakeLLM(
        llm_payload(
            """
SELECT pm_event_name, type_name, pm_type, mean
FROM fab10.pm
ORDER BY source_row_id
LIMIT 50
""".strip(),
            source_tables=["fab10.pm"],
        )
    )

    result = plan_text2sql("fab10 PM policy 목록 보여줘", llm_client=llm)

    assert result.status == "succeeded"
    schema_context = llm.calls[0]["schema_context"]
    assert "fab10.pm" in schema_context["allowed_table_refs"]
    assert "fab10.breakdown" in schema_context["allowed_table_refs"]


def test_toolgroup_lookup_uses_llm_generated_general_data_sql() -> None:
    llm = FakeLLM(
        llm_payload(
            """
SELECT area, toolgroup, number_of_tools
FROM fab10.toolgroups
WHERE area ILIKE '%Dry_Etch%'
ORDER BY area, toolgroup
LIMIT 50
""".strip(),
            source_tables=["fab10.toolgroups"],
        )
    )

    result = plan_text2sql("fab10 Dry_Etch toolgroup 목록 보여줘", llm_client=llm)

    assert result.status == "succeeded"
    assert result.query_type == "master_data_lookup"
    assert result.plan is not None
    assert result.plan.template_id is None
    assert "FROM fab10.toolgroups" in (result.sql or "")
    assert len(llm.calls) == 1


def test_route_lookup_exposes_allowed_route_table_to_llm() -> None:
    llm = FakeLLM(
        llm_payload(
            """
SELECT route, step, area, toolgroup
FROM fab11.route_product_10
ORDER BY step
LIMIT 100
""".strip(),
            source_tables=["fab11.route_product_10"],
        )
    )

    result = plan_text2sql("fab11 Product_10 route step 보여줘", llm_client=llm)

    assert result.status == "succeeded"
    assert result.plan is not None
    assert result.plan.template_id is None
    assert "fab11.route_product_10" in llm.calls[0]["schema_context"]["allowed_table_refs"]


def test_route_lookup_rejects_llm_sql_for_non_allowlisted_table() -> None:
    llm = FakeLLM(
        llm_payload(
            """
SELECT route, step
FROM fab10.route_product_1
ORDER BY step
LIMIT 100
""".strip(),
            source_tables=["fab10.route_product_1"],
        )
    )

    result = plan_text2sql("fab10 Product_1 route step 보여줘", llm_client=llm)

    assert result.status == "failed"
    assert result.sql is not None
    assert "non-allowlisted" in " ".join(result.limitations)


def test_release_lookup_requires_selective_constraint() -> None:
    result = plan_text2sql("fab13 release plan 보여줘")

    assert result.status == "needs_clarification"
    assert result.sql is None
    assert "product" in result.answer


def test_release_lookup_generates_sql_with_product_constraint() -> None:
    llm = FakeLLM(
        llm_payload(
            """
SELECT product_name, route_name, start_date, due_date, release_scenario
FROM fab13.lotrelease_variable_due_dates
WHERE product_name = 'Product_1'
ORDER BY start_date, source_row_id
LIMIT 50
""".strip(),
            source_tables=["fab13.lotrelease_variable_due_dates"],
        )
    )

    result = plan_text2sql("fab13 Product_1 release plan 보여줘", llm_client=llm)

    assert result.status == "succeeded"
    assert result.query_type == "release_plan_lookup"
    assert result.sql is not None
    assert "fab13.lotrelease_variable_due_dates" in result.sql
    assert "Product_1" in result.sql


def test_missing_fab_asks_for_clarification_without_llm_call() -> None:
    llm = FakeLLM(llm_payload("SELECT * FROM fab10.toolgroups LIMIT 1"))
    result = plan_text2sql("Dry_Etch toolgroup 목록 보여줘", llm_client=llm)

    assert result.status == "needs_clarification"
    assert result.sql is None
    assert "fab10" in result.answer
    assert llm.calls == []


def test_generate_sql_keeps_backward_compatible_api_with_llm_client() -> None:
    llm = FakeLLM(
        llm_payload(
            """
SELECT area, toolgroup
FROM fab10.toolgroups
ORDER BY area, toolgroup
LIMIT 50
""".strip(),
            source_tables=["fab10.toolgroups"],
        )
    )

    sql = generate_sql("fab10 Dry_Etch toolgroup 목록 보여줘", llm_client=llm)

    assert "FROM fab10.toolgroups" in sql
