import pytest
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


def test_retry_feedback_is_included_in_text2sql_schema_context() -> None:
    llm = FakeLLM(llm_payload("SELECT toolgroup FROM fab10.toolgroups_fab10 LIMIT 20"))
    feedback = [{"reason": "The first SQL failed validation."}]

    result = plan_text2sql(
        "fab10 toolgroup 목록 보여줘",
        execution_feedback=feedback,
        llm_client=llm,
    )

    assert result.status == "succeeded"
    assert llm.calls[0]["schema_context"]["execution_feedback"] == feedback


def test_status_query_uses_deterministic_sql_without_llm_key(monkeypatch) -> None:
    class MissingKeyLLM:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def create_sql(self, **kwargs) -> dict:
            raise RuntimeError("OPENAI_API_KEY is not configured.")

    monkeypatch.setattr("app.sub_agent.text2sql.OpenAIText2SQLClient", MissingKeyLLM)

    result = plan_text2sql("지금 fab10 WIP 몇 개야?")

    assert result.status == "succeeded"
    assert result.query_type == "status"
    assert result.sql is not None
    assert "FROM fab10.autosched_perf_fab10" in result.sql
    assert "relative = 'Y'" in result.sql
    assert result.plan is not None
    assert result.plan.template_id == "deterministic_status_autosched_perf"
    assert result.confidence == 0.98


@pytest.mark.parametrize(
    ("question", "table", "fragment"),
    [
        ("fab10 DE_BE_11 설비 현재 상태 알려줘", "autosched_stn", "DE_BE_11"),
        ("fab10 Dry_Etch utilization 어때?", "autosched_stngrp", "Dry_Etch"),
        ("fab10 Product_3 현재 WIP와 ontime 어때?", "autosched_part", "part_3"),
        ("fab10 Init_Lot_3_24 진행 step이랑 현재 설비 알려줘", "autosched_lot", "Init_Lot_3_24"),
    ],
)
def test_deterministic_status_fast_paths_select_semantic_table(
    question: str,
    table: str,
    fragment: str,
) -> None:
    result = plan_text2sql(question)

    assert result.status == "succeeded"
    assert result.sql is not None
    assert f"FROM fab10.{table}_fab10" in result.sql
    assert fragment.casefold() in result.sql.casefold()
    assert result.plan is not None
    assert result.plan.source_tables == [f"fab10.{table}_fab10"]


def test_status_query_preserves_multiple_products_and_requested_metrics() -> None:
    result = plan_text2sql(
        "fab10 Product_3과 Product_4 현재 WIP과 cycle time 알려줘",
        deterministic_only=True,
    )

    assert result.status == "succeeded"
    assert result.query_type == "status"
    assert result.plan is not None
    assert result.plan.template_id == "deterministic_status_autosched_part_multi"
    assert result.plan.select_items == [
        "report_time",
        "period",
        "part",
        "wiplotcur",
        "wiplotavg",
        "cycleavg",
    ]
    assert "lower(part) IN ('part_3', 'part_4')" in (result.sql or "")
    assert "ORDER BY part, source_row_id DESC" in (result.sql or "")
    assert "LIMIT 20" in (result.sql or "")


def test_deterministic_release_trend_preserves_date_basis_and_chart() -> None:
    result = plan_text2sql(
        "fab10 Route_Product_3 lotrelease 건수를 start_date 기준 일별 라인차트로 보여줘"
    )

    assert result.status == "succeeded"
    assert result.sql is not None
    assert "start_date::date AS release_date" in result.sql
    assert "COUNT(*)::bigint AS lot_count" in result.sql
    assert "Route_Product_3" in result.sql
    assert result.plan is not None
    assert result.plan.chart_intent == {
        "type": "line",
        "x": "release_date",
        "y": "lot_count",
        "x_title": "Release Date",
        "y_title": "Lot Count",
        "series": None,
        "grain": "day",
        "missing_policy": "zero",
    }


def test_deterministic_product_comparison_preserves_targets_and_metric_order() -> None:
    result = plan_text2sql(
        "fab10 Product_3와 Product_4 cycle time과 ontime 비교해줘"
    )

    assert result.status == "succeeded"
    assert result.sql is not None
    assert "lower(part) IN ('part_3', 'part_4')" in result.sql
    assert "SELECT part, cycleavg, ontime_percent" in result.sql
    assert result.plan is not None
    assert result.plan.chart_intent["type"] == "grouped_bar"
    assert result.plan.chart_intent["y"] == ["cycleavg", "ontime_percent"]


def test_deterministic_period_comparison_preserves_periods_and_metrics() -> None:
    result = plan_text2sql("fab10 Period_2와 Period_3의 WIP과 ontime을 비교해줘")

    assert result.status == "succeeded"
    assert result.sql is not None
    assert "period IN ('Period_2', 'Period_3')" in result.sql
    assert "SELECT period, wiplotavg, ontime_percent" in result.sql
    assert result.plan is not None
    assert result.plan.chart_intent["x"] == "period"


def test_deterministic_operational_trend_aggregates_by_report_date() -> None:
    result = plan_text2sql("fab10 WIP 일별 추세를 라인차트로 보여줘")

    assert result.status == "succeeded"
    assert result.sql is not None
    assert "report_time::date AS report_date" in result.sql
    assert "AVG(wiplotavg) AS wiplotavg" in result.sql
    assert "GROUP BY report_time::date" in result.sql
    assert result.plan is not None
    assert result.plan.chart_intent["type"] == "line"


def test_operational_trend_applies_explicit_inclusive_date_range() -> None:
    result = plan_text2sql(
        "fab10 WIP 2020-01-01부터 2020-01-07까지 일별 추세 보여줘",
        query_type="trend",
        deterministic_only=True,
    )

    assert result.status == "succeeded"
    assert "report_time::date >= '2020-01-01'::date" in (result.sql or "")
    assert "report_time::date < '2020-01-08'::date" in (result.sql or "")
    assert result.plan is not None
    assert result.plan.slots["relative_period"].value == "explicit_range"
    assert result.plan.slots["date_end"].value == "2020-01-08"
    assert result.plan.chart_intent["grain"] == "day"
    assert result.plan.chart_intent["range_start"] == "2020-01-01"
    assert result.plan.chart_intent["range_end_exclusive"] == "2020-01-08"


def test_product_comparison_uses_rolling_window_aggregation() -> None:
    result = plan_text2sql(
        "fab10 Product_3와 Product_4 최근 7일 cycle time 비교해줘",
        query_type="trend",
        deterministic_only=True,
    )

    assert result.status == "succeeded"
    assert "AVG(NULLIF(cycleavg::text, '')::numeric) AS cycleavg" in (result.sql or "")
    assert "GROUP BY part" in (result.sql or "")
    assert "report_time::date >=" in (result.sql or "")
    assert result.plan is not None
    assert result.plan.slots["relative_period"].value == "last_7_days"
    assert result.plan.expected_result_shape == "comparison"


def test_quarter_range_defaults_to_monthly_operational_buckets() -> None:
    result = plan_text2sql(
        "fab10 2020년 1분기 WIP 추세 보여줘",
        query_type="trend",
        deterministic_only=True,
    )

    assert result.status == "succeeded"
    assert "report_time::date >= '2020-01-01'::date" in (result.sql or "")
    assert "report_time::date < '2020-04-01'::date" in (result.sql or "")
    assert "date_trunc('month', report_time::timestamp)::date AS report_month" in (
        result.sql or ""
    )
    assert result.plan is not None
    assert result.plan.slots["relative_period"].value == "quarter_2020_q1"
    assert result.plan.slots["date_grain"].value == "month"
    assert result.plan.chart_intent["x"] == "report_month"
    assert result.plan.chart_intent["grain"] == "month"
    assert result.plan.chart_intent["range_start"] == "2020-01-01"
    assert result.plan.chart_intent["range_end_exclusive"] == "2020-04-01"


@pytest.mark.parametrize(
    ("question", "relative_period"),
    [
        ("fab10 2020년 1분기부터 2분기까지 WIP 월별 추세", "quarter_range_2020_q1_2020_q2"),
        ("fab10 WIP Q1 2020 to Q2 2020 monthly trend", "quarter_range_2020_q1_2020_q2"),
    ],
)
def test_quarter_ranges_include_the_final_quarter(
    question: str, relative_period: str
) -> None:
    result = plan_text2sql(question, query_type="trend", deterministic_only=True)

    assert result.status == "succeeded"
    assert "report_time::date >= '2020-01-01'::date" in (result.sql or "")
    assert "report_time::date < '2020-07-01'::date" in (result.sql or "")
    assert result.plan is not None
    assert result.plan.slots["relative_period"].value == relative_period
    assert result.plan.slots["date_grain"].value == "month"


@pytest.mark.parametrize("phrase", ["이번 분기", "this quarter", "지난 분기", "last quarter"])
def test_relative_quarters_always_have_bounded_monthly_ranges(phrase: str) -> None:
    result = plan_text2sql(
        f"fab10 {phrase} WIP 추세", query_type="trend", deterministic_only=True
    )

    assert result.status == "succeeded"
    assert result.plan is not None
    assert result.plan.slots["date_grain"].value == "month"
    assert result.plan.slots["date_start"].value
    assert result.plan.slots["date_end"].value
    assert "report_time::date >=" in (result.sql or "")
    assert "report_time::date <" in (result.sql or "")


def test_single_calendar_month_preserves_daily_default_range() -> None:
    result = plan_text2sql(
        "fab10 2020년 2월 WIP 일별 추세",
        query_type="trend",
        deterministic_only=True,
    )

    assert result.status == "succeeded"
    assert "report_time::date >= '2020-02-01'::date" in (result.sql or "")
    assert "report_time::date < '2020-03-01'::date" in (result.sql or "")
    assert result.plan is not None
    assert result.plan.slots["relative_period"].value == "month_2020_02"


def test_monthly_lotrelease_uses_selected_date_basis_bucket() -> None:
    result = plan_text2sql(
        "fab10 Product_3 2020년 1월부터 2020년 3월까지 due_date 기준 월별 추세",
        query_type="trend",
        deterministic_only=True,
    )

    assert result.status == "succeeded"
    assert "date_trunc('month', due_date::timestamp)::date AS release_month" in (
        result.sql or ""
    )
    assert "due_date >= '2020-01-01'::date" in (result.sql or "")
    assert "due_date < '2020-04-01'::date" in (result.sql or "")
    assert result.plan is not None
    assert result.plan.chart_intent["x"] == "release_month"
    assert result.plan.chart_intent["grain"] == "month"
    assert result.plan.chart_intent["range_start"] == "2020-01-01"
    assert result.plan.chart_intent["range_end_exclusive"] == "2020-04-01"


@pytest.mark.parametrize(
    "question",
    [
        "fab10 WIP 2020-02-01부터 2020-01-01까지 추세",
        "fab10 WIP 2020년 3월부터 2020년 1월까지 추세",
        "fab10 WIP 2020년 5분기 추세",
        "fab10 WIP 2020년 3분기부터 2분기까지 추세",
        "fab10 WIP Q4 2020 to Q1 2020 추세",
    ],
)
def test_invalid_or_reversed_calendar_range_is_blocked(question: str) -> None:
    result = plan_text2sql(question, query_type="trend", deterministic_only=True)

    assert result.status == "needs_clarification"
    assert result.sql is None
    assert "날짜 범위가 올바르지 않습니다" in result.answer


@pytest.mark.parametrize(
    ("question", "template_id", "sql_fragments"),
    [
        (
            "fab10 Dry_Etch toolgroup 목록 보여줘",
            "deterministic_master_toolgroups",
            ["FROM fab10.toolgroups_fab10", "Dry_Etch"],
        ),
        (
            "fab10 Dry_Etch 관련 PM mean 조회",
            "deterministic_master_pm",
            ["FROM fab10.pm_fab10", "JOIN fab10.toolgroups_fab10", "Dry_Etch"],
        ),
        (
            "fab10 DE_BE 타입 고장 MTTR 알려줘",
            "deterministic_master_breakdown",
            ["FROM fab10.breakdown_fab10", "EXISTS", "fab10.toolgroups_fab10", "DE_BE%"],
        ),
        (
            "fab10 Product_3 route step 보여줘",
            "deterministic_master_route_steps",
            ["FROM fab10.route_product_3_fab10", "step", "toolgroup"],
        ),
        (
            "fab10 Product_3 release plan 보여줘",
            "deterministic_release_lookup",
            ["FROM fab10.lotrelease_fab10", "Product_3", "Route_Product_3"],
        ),
    ],
)
def test_deterministic_master_and_release_contracts(
    question: str,
    template_id: str,
    sql_fragments: list[str],
) -> None:
    result = plan_text2sql(question)

    assert result.status == "succeeded"
    assert result.sql is not None
    assert result.plan is not None
    assert result.plan.template_id == template_id
    for fragment in sql_fragments:
        assert fragment.casefold() in result.sql.casefold()


def test_deterministic_only_mode_never_falls_through_to_llm(monkeypatch) -> None:
    class UnexpectedLLM:
        def __init__(self, *args, **kwargs) -> None:
            raise AssertionError("LLM client must not be constructed")

    monkeypatch.setattr("app.sub_agent.text2sql.OpenAIText2SQLClient", UnexpectedLLM)

    result = plan_text2sql(
        "fab10 setup policy 조회",
        deterministic_only=True,
    )

    assert result.status == "unsupported"
    assert result.sql is None
    assert "LLM SQL 생성을 호출하지 않습니다" in " ".join(result.limitations)


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
        schema_context={"allowed_table_refs": ["fab10.toolgroups_fab10"]},
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
        "fab10에서 utilization이 10% 늘면 output 영향은?",
        llm_client=FakeLLM(llm_payload("SELECT * FROM fab10.autosched_perf_fab10 LIMIT 1")),
    )

    assert result.status == "succeeded"
    assert result.plan is not None
    assert result.plan.fab_id == "fab10"


def test_queue_time_request_is_rejected_without_llm_call() -> None:
    llm = FakeLLM(llm_payload("SELECT * FROM fab10.autosched_perf_fab10 LIMIT 1"))

    result = plan_text2sql("fab10 큐 상태 보여줘", llm_client=llm)

    assert result.status == "data_unavailable"
    assert result.query_type == "status"
    assert result.sql is None
    assert "Queue Time" in result.answer
    assert llm.calls == []


def test_simulation_queue_trend_preserves_snapshot_area_without_llm_call() -> None:
    llm = FakeLLM(llm_payload("SELECT * FROM fab13.autosched_perf_fab13 LIMIT 1"))

    result = plan_text2sql(
        "FAB13 합성 시뮬레이션의 etch 영역에서, 저장된 최신 시점 기준 최근 24시간 "
        "평균 대기시간 추이를 보여줘. WIP, 대기 LOT 수, 가동률도 비교해줘.",
        llm_client=llm,
    )

    assert result.status == "succeeded"
    assert result.sql is not None
    assert "FROM fab13.live_process_snapshots_fab13" in result.sql
    assert "s.area = 'etch'" in result.sql
    assert "Dry_Etch" not in result.sql
    assert "avg_queue_minutes" in result.sql
    assert "queue_lots" in result.sql
    assert result.plan is not None
    assert result.plan.data_source_type == "simulation_snapshot"
    assert result.plan.slots["area"].value == "etch"
    assert result.plan.chart_intent is not None
    assert result.plan.chart_intent["x"] == "interval_end"
    assert llm.calls == []


@pytest.mark.parametrize("fab", ["FAB10", "FAB11", "FAB12", "FAB13"])
def test_simulation_queue_trend_uses_same_contract_for_supported_fabs(fab: str) -> None:
    result = plan_text2sql(
        f"{fab} 합성 시뮬레이션의 etch 영역에서, 저장된 최신 시점 기준 최근 24시간 "
        "평균 대기시간 추이를 보여줘. 대기시간이 증가한 구간을 찾아 WIP, 대기 LOT 수, "
        "가동률과 함께 비교하고 원인 후보를 설명해 줘.",
        deterministic_only=True,
    )

    fab_id = fab.casefold()
    assert result.status == "succeeded"
    assert result.sql is not None
    assert f"FROM {fab_id}.live_process_snapshots_{fab_id}" in result.sql
    assert "s.area = 'etch'" in result.sql
    assert result.plan is not None
    assert result.plan.fab_id == fab_id
    assert result.plan.data_source_type == "simulation_snapshot"
    assert result.plan.slots["area"].value == "etch"


def test_simulation_empty_result_suggests_available_fabs_and_areas(monkeypatch) -> None:
    class EmptySimulationExecutor:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def validate(self, sql: str) -> str:
            return sql

        def execute(self, sql: str, limit=None) -> ReadOnlyQueryResult:
            if "GROUP BY area" in sql:
                return ReadOnlyQueryResult(
                    ["area", "rows"],
                    [{"area": "photo", "rows": 30}, {"area": "cmp", "rows": 30}],
                    2,
                    sql,
                    limit or 200,
                )
            if "WHERE area = 'etch'" in sql:
                rows = [{"rows": 12}] if "fab10.live_process_snapshots_fab10" in sql else [{"rows": 0}]
                return ReadOnlyQueryResult(["rows"], rows, 1, sql, limit or 1)
            return ReadOnlyQueryResult([], [], 0, sql, limit or 200)

    monkeypatch.setattr("app.sub_agent.text2sql.ReadOnlyQueryExecutor", EmptySimulationExecutor)
    monkeypatch.setattr("app.sub_agent.text2sql.load_fab_catalog", lambda fab: {
        f"{fab}.live_process_snapshots_{fab}": {
            "logical_table": "live_process_snapshots",
            "data_source_type": "simulation_snapshot",
            "columns": [{"name": name} for name in (
                "fab_id", "area", "interval_start", "interval_end", "avg_queue_minutes",
            )],
        },
    })

    result = answer_question(
        "FAB13 합성 시뮬레이션의 etch 영역 최근 24시간 평균 대기시간 추이를 보여줘."
    )

    assert result.status == "data_unavailable"
    assert "area='etch'" in result.answer
    assert "fab10(12행)" in " ".join(result.limitations)
    assert "photo" in result.answer
    assert result.plan is not None
    assert result.plan.data_source_type == "simulation_snapshot"


def test_release_route_is_parsed_before_korean_particle() -> None:
    result = plan_text2sql(
        "fab10 lotrelease에서 Route_Product_3의 2018-01-01 release plan 목록을 보여줘",
        llm_client=FakeLLM(
            llm_payload(
                "SELECT * FROM fab10.lotrelease_fab10 "
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
FROM fab10.autosched_stngrp_fab10
WHERE relative = 'Y'
  AND period <> 'WarmUp'
  AND stngrp ILIKE '%Dry_Etch%'
ORDER BY report_time DESC NULLS LAST, source_row_id DESC
LIMIT 20
""".strip(),
            source_tables=["fab10.autosched_stngrp_fab10"],
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
            assert "FROM fab10.autosched_stngrp_fab10" in sql
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
    assert result.plan.source_tables == ["fab10.autosched_stngrp_fab10"]
    assert len(llm.calls) == 1
    assert llm.calls[0]["schema_context"]["data_source_type"] == "operational_report"
    assert "AutoSched report 기준으로 1개 상태 행" in result.answer


def test_status_query_stays_data_unavailable_when_autosched_table_is_missing(monkeypatch) -> None:
    llm = FakeLLM(
        llm_payload(
            """
SELECT report_time, period, wiplotavg
FROM fab10.autosched_perf_fab10
WHERE relative = 'Y'
ORDER BY report_time DESC NULLS LAST, source_row_id DESC
LIMIT 1
""".strip(),
            source_tables=["fab10.autosched_perf_fab10"],
        )
    )

    class FakeExecutor:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def validate(self, sql: str) -> str:
            return sql

        def execute(self, sql: str) -> ReadOnlyQueryResult:
            raise RuntimeError('relation "fab10.autosched_perf_fab10" does not exist')

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
FROM fab10.lotrelease_fab10
WHERE route_name = 'Route_Product_3'
GROUP BY start_date::date
ORDER BY release_date ASC
""".strip(),
            source_tables=["fab10.lotrelease_fab10"],
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
                "series": None,
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
    assert result.plan.source_tables == ["fab10.lotrelease_fab10"]
    assert result.plan.aggregation == "count"
    assert result.plan.chart_intent == {
        "type": "line",
        "x": "release_date",
        "y": "lot_count",
        "x_title": "Release date",
        "y_title": "Lot release count",
        "series": None,
        "grain": "day",
        "missing_policy": "zero",
    }
    assert "GROUP BY start_date::date" in (result.sql or "")
    assert len(llm.calls) == 1


def test_product_multi_metric_comparison_preserves_chart_y_fields() -> None:
    llm = FakeLLM(
        llm_payload(
            """
SELECT part, cycleavg, ontime_percent
FROM fab10.autosched_part_fab10
WHERE part IN ('part_3', 'part_4') AND period <> 'WarmUp'
ORDER BY part
""".strip(),
            source_tables=["fab10.autosched_part_fab10"],
            select_items=["part", "cycleavg", "ontime_percent"],
            filters=[{"field": "part", "operator": "in", "value": "part_3,part_4"}],
            order_by=["part"],
            expected_result_shape="comparison",
            chart_intent={
                "type": "line",
                "x": "report_time",
                "y": "cycleavg,ontime_percent",
                "x_title": "Product",
                "y_title": "Metric value",
                "series": "part",
            },
        )
    )

    result = plan_text2sql(
        "fab10 Product_3와 Product_4의 cycle time과 ontime을 비교해줘",
        llm_client=llm,
    )

    assert result.status == "succeeded"
    assert result.query_type == "trend"
    assert result.plan is not None
    assert result.plan.chart_intent["y"] == ["cycleavg", "ontime_percent"]
    assert result.plan.chart_intent["type"] == "grouped_bar"
    assert result.plan.chart_intent["x"] == "part"
    assert result.plan.chart_intent["series"] is None
    assert result.plan.slots["products"].value == "Product_3,Product_4"
    assert "'part_3'" in (result.sql or "")
    assert "'part_4'" in (result.sql or "")


def test_follow_up_product_comparison_merges_context_and_explicit_product() -> None:
    llm = FakeLLM(
        llm_payload(
            "SELECT part, cycleavg, ontime_percent FROM fab10.autosched_part_fab10 "
            "WHERE part IN ('part_3', 'part_4') AND period <> 'WarmUp' ORDER BY part",
            source_tables=["fab10.autosched_part_fab10"],
            chart_intent={
                "type": "grouped_bar",
                "x": "part",
                "y": ["cycleavg", "ontime_percent"],
                "x_title": "Product",
                "y_title": "Metric value",
                "series": None,
            },
        )
    )

    result = plan_text2sql(
        "Product_4와 cycle time과 ontime을 비교해줘",
        fab="fab10",
        product="Product_3",
        llm_client=llm,
    )

    assert result.status == "succeeded"
    assert result.plan is not None
    assert result.plan.slots["products"].value == "Product_3,Product_4"
    assert "'part_3'" in (result.sql or "")
    assert "'part_4'" in (result.sql or "")


def test_period_multi_metric_comparison_preserves_period_contract() -> None:
    llm = FakeLLM(
        llm_payload(
            "SELECT period, wiplotavg, ontime_percent FROM fab10.autosched_perf_fab10 "
            "WHERE period IN ('Period_2', 'Period_3') ORDER BY period",
            source_tables=["fab10.autosched_perf_fab10"],
            select_items=["period", "wiplotavg", "ontime_percent"],
            expected_result_shape="comparison",
            chart_intent={
                "type": "line",
                "x": "report_time",
                "y": "wiplotavg,ontime_percent",
                "x_title": "Period",
                "y_title": "Metric value",
                "series": "period",
            },
        )
    )

    result = plan_text2sql(
        "fab10 Period_2와 Period_3의 WIP과 ontime을 비교해줘",
        llm_client=llm,
    )

    assert result.status == "succeeded"
    assert result.plan is not None
    assert result.plan.slots["periods"].value == "Period_2,Period_3"
    assert result.plan.slots["metrics"].value == "wiplotavg,ontime_percent"
    assert result.plan.chart_intent["type"] == "grouped_bar"
    assert result.plan.chart_intent["x"] == "period"
    assert result.plan.chart_intent["y"] == ["wiplotavg", "ontime_percent"]


def test_period_comparison_rejects_sql_that_omits_requested_period() -> None:
    llm = FakeLLM(
        llm_payload(
            "SELECT period, wiplotavg FROM fab10.autosched_perf_fab10 "
            "WHERE period = 'Period_3' ORDER BY period",
            source_tables=["fab10.autosched_perf_fab10"],
        )
    )

    result = plan_text2sql(
        "fab10 Period_2와 Period_3의 WIP을 비교해줘",
        llm_client=llm,
    )

    assert result.status == "failed"
    assert "Period_2" in " ".join(result.limitations)


def test_model_sql_predicate_is_not_silently_rewritten() -> None:
    llm = FakeLLM(
        llm_payload(
            "SELECT stn, curstate FROM fab10.autosched_stn_fab10 "
            "WHERE curstate <> 'WarmUp' ORDER BY source_row_id LIMIT 20",
            source_tables=["fab10.autosched_stn_fab10"],
        )
    )

    result = plan_text2sql("fab10 DE_BE_11 설비 현재 상태 알려줘", llm_client=llm)

    assert result.status == "succeeded"
    assert "curstate <> 'WarmUp'" in result.sql
    assert "period <> 'WarmUp'" not in result.sql


def test_ambiguous_lotrelease_date_basis_asks_for_clarification() -> None:
    llm = FakeLLM(llm_payload("SELECT * FROM fab10.lotrelease_fab10 LIMIT 1"))

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
FROM fab10.lotrelease_fab10
WHERE product_name = 'Product_3'
GROUP BY due_date::date
ORDER BY due_date ASC
""".strip(),
            source_tables=["fab10.lotrelease_fab10"],
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
    assert "fab10.lotrelease_fab10" in schema_context["allowed_table_refs"]


def test_operational_trend_uses_autosched_schema_context() -> None:
    llm = FakeLLM(
        llm_payload(
            """
SELECT report_time::date AS report_date,
       AVG(wiplotavg) AS wiplotavg
FROM fab10.autosched_perf_fab10
WHERE relative = 'Y'
GROUP BY report_time::date
ORDER BY report_date ASC
""".strip(),
            source_tables=["fab10.autosched_perf_fab10"],
        )
    )

    result = plan_text2sql("fab10 WIP 일별 추세를 보여줘", llm_client=llm)

    assert result.status == "succeeded"
    schema_context = llm.calls[0]["schema_context"]
    assert schema_context["data_source_type"] == "operational_report"
    assert "fab10.autosched_perf_fab10" in schema_context["allowed_table_refs"]
    assert "fab10.lotrelease_fab10" not in schema_context["allowed_table_refs"]
    assert schema_context["slots"]["metric"]["value"] == "wiplotavg"


def test_follow_up_process_context_selects_process_group_schema() -> None:
    llm = FakeLLM(
        llm_payload(
            "SELECT stngrp, wiplotavg FROM fab10.autosched_stngrp_fab10 "
            "WHERE stngrp = 'Dry_Etch' AND period <> 'WarmUp' LIMIT 20",
            source_tables=["fab10.autosched_stngrp_fab10"],
        )
    )

    result = plan_text2sql(
        "그 공정 WIP도 보여줘",
        fab="fab10",
        process="Dry_Etch",
        llm_client=llm,
    )

    assert result.status == "succeeded"
    assert result.plan is not None
    assert result.plan.slots["area"].value == "Dry_Etch"
    assert result.plan.slots["area"].source == "request_context"
    schema_context = llm.calls[0]["schema_context"]
    assert schema_context["primary_table_refs"] == ["fab10.autosched_stngrp_fab10"]
    assert schema_context["allowed_table_refs"] == ["fab10.autosched_stngrp_fab10"]


def test_pm_and_breakdown_tables_are_available_for_master_lookup() -> None:
    llm = FakeLLM(
        llm_payload(
            """
SELECT pm_event_name, type_name, pm_type, mean
FROM fab10.pm_fab10
ORDER BY source_row_id
LIMIT 50
""".strip(),
            source_tables=["fab10.pm_fab10"],
        )
    )

    result = plan_text2sql("fab10 PM policy 목록 보여줘", llm_client=llm)

    assert result.status == "succeeded"
    schema_context = llm.calls[0]["schema_context"]
    assert "fab10.pm_fab10" in schema_context["allowed_table_refs"]
    assert "fab10.toolgroups_fab10" not in schema_context["allowed_table_refs"]
    assert "fab10.breakdown_fab10" not in schema_context["allowed_table_refs"]


def test_toolgroup_lookup_uses_llm_generated_general_data_sql() -> None:
    llm = FakeLLM(
        llm_payload(
            """
SELECT area, toolgroup, number_of_tools
FROM fab10.toolgroups_fab10
WHERE area ILIKE '%Dry_Etch%'
ORDER BY area, toolgroup
LIMIT 50
""".strip(),
            source_tables=["fab10.toolgroups_fab10"],
        )
    )

    result = plan_text2sql("fab10 Dry_Etch toolgroup 목록 보여줘", llm_client=llm)

    assert result.status == "succeeded"
    assert result.query_type == "master_data_lookup"
    assert result.plan is not None
    assert result.plan.template_id is None
    assert "FROM fab10.toolgroups_fab10" in (result.sql or "")
    assert len(llm.calls) == 1


def test_process_group_wip_lookup_prefers_operational_status_schema() -> None:
    llm = FakeLLM(
        llm_payload(
            """
SELECT stngrp, wiplotavg
FROM fab10.autosched_stngrp_fab10
WHERE stngrp ILIKE '%Dry_Etch%'
ORDER BY report_time DESC NULLS LAST, source_row_id DESC
LIMIT 20
""".strip(),
            source_tables=["fab10.autosched_stngrp_fab10"],
        )
    )

    result = plan_text2sql("fab10 Dry_Etch 공정그룹 WIP 높은 순으로 보여줘", llm_client=llm)

    assert result.status == "succeeded"
    assert result.query_type == "status"
    schema_context = llm.calls[0]["schema_context"]
    assert schema_context["primary_table_refs"] == ["fab10.autosched_stngrp_fab10"]
    assert schema_context["allowed_table_refs"] == ["fab10.autosched_stngrp_fab10"]


def test_station_pm_down_ratio_prefers_autosched_station_schema() -> None:
    llm = FakeLLM(
        llm_payload(
            """
SELECT stn, pm_percent, down_percent
FROM fab10.autosched_stn_fab10
WHERE stn ILIKE '%DE_BE_11%'
ORDER BY report_time DESC NULLS LAST, source_row_id DESC
LIMIT 20
""".strip(),
            source_tables=["fab10.autosched_stn_fab10"],
        )
    )

    result = plan_text2sql("fab10 DE_BE_11 설비 PM이랑 down 비율 알려줘", llm_client=llm)

    assert result.status == "succeeded"
    assert result.query_type == "status"
    schema_context = llm.calls[0]["schema_context"]
    assert schema_context["primary_table_refs"] == ["fab10.autosched_stn_fab10"]
    assert schema_context["allowed_table_refs"] == ["fab10.autosched_stn_fab10"]


def test_pm_mean_lookup_narrows_to_pm_master_schema() -> None:
    llm = FakeLLM(
        llm_payload(
            """
SELECT pm_event_name, type_name, mean
FROM fab10.pm_fab10
WHERE type_name ILIKE '%Dry_Etch%'
ORDER BY source_row_id
LIMIT 50
""".strip(),
            source_tables=["fab10.pm_fab10"],
        )
    )

    result = plan_text2sql("fab10 Dry_Etch 관련 PM mean 조회", llm_client=llm)

    assert result.status == "succeeded"
    assert result.query_type == "master_data_lookup"
    schema_context = llm.calls[0]["schema_context"]
    assert schema_context["primary_table_refs"] == ["fab10.pm_fab10"]
    assert schema_context["allowed_table_refs"] == ["fab10.pm_fab10", "fab10.toolgroups_fab10"]


def test_breakdown_policy_lookup_narrows_to_breakdown_master_schema() -> None:
    llm = FakeLLM(
        llm_payload(
            """
SELECT down_event_name, type_name, mttf, mttr
FROM fab10.breakdown_fab10
ORDER BY source_row_id
LIMIT 50
""".strip(),
            source_tables=["fab10.breakdown_fab10"],
        )
    )

    result = plan_text2sql("fab10 breakdown policy 조회", llm_client=llm)

    assert result.status == "succeeded"
    schema_context = llm.calls[0]["schema_context"]
    assert schema_context["primary_table_refs"] == ["fab10.breakdown_fab10"]
    assert schema_context["allowed_table_refs"] == ["fab10.breakdown_fab10"]


def test_model_supplied_catalog_value_is_preserved_before_execution(monkeypatch) -> None:
    llm = FakeLLM(
        llm_payload(
            """
SELECT part, wiplotavg
FROM fab10.autosched_part_fab10
WHERE part ILIKE '%part_3%'
ORDER BY report_time DESC NULLS LAST, source_row_id DESC
LIMIT 20
""".strip(),
            source_tables=["fab10.autosched_part_fab10"],
        )
    )
    executed_sql: list[str] = []

    class FakeExecutor:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def validate(self, sql: str) -> str:
            return sql

        def execute(self, sql: str) -> ReadOnlyQueryResult:
            executed_sql.append(sql)
            return ReadOnlyQueryResult(
                columns=["part", "wiplotavg"],
                rows=[{"part": "part_3", "wiplotavg": 12.3}],
                row_count=1,
                sql=sql,
                limit=100,
            )

    monkeypatch.setattr("app.sub_agent.text2sql.ReadOnlyQueryExecutor", FakeExecutor)

    result = answer_question("fab10 Product_3 현재 WIP와 ontime 어때?", execute=True, llm_client=llm)

    assert result.status == "succeeded"
    assert result.row_count == 1
    assert "part_3" in (result.sql or "")
    assert len(executed_sql) == 1
    assert executed_sql == [result.sql]


def test_empty_pm_area_result_preserves_query_without_canned_join(monkeypatch) -> None:
    llm = FakeLLM(
        llm_payload(
            "SELECT pm_event_name, type_name, mean FROM fab10.pm_fab10 "
            "WHERE type_name ILIKE '%Dry_Etch%' ORDER BY source_row_id LIMIT 50",
            source_tables=["fab10.pm_fab10"],
        )
    )
    executed_sql: list[str] = []

    class FakeExecutor:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def validate(self, sql: str) -> str:
            return sql

        def execute(self, sql: str) -> ReadOnlyQueryResult:
            executed_sql.append(sql)
            if "JOIN fab10.toolgroups_fab10" in sql:
                return ReadOnlyQueryResult(
                    columns=["pm_event_name", "type_name", "mean"],
                    rows=[{"pm_event_name": "PM_1", "type_name": "DE_BE_11", "mean": 720.0}],
                    row_count=1,
                    sql=sql,
                    limit=100,
                )
            return ReadOnlyQueryResult(
                columns=["pm_event_name", "type_name", "mean"],
                rows=[],
                row_count=0,
                sql=sql,
                limit=100,
            )

    monkeypatch.setattr("app.sub_agent.text2sql.ReadOnlyQueryExecutor", FakeExecutor)

    result = answer_question("fab10 Dry_Etch 관련 PM mean 조회", execute=True, llm_client=llm)

    assert result.status == "succeeded"
    assert result.row_count == 0
    assert "JOIN" not in result.sql
    assert len(executed_sql) == 1


def test_empty_breakdown_type_result_does_not_relax_exact_filter(monkeypatch) -> None:
    llm = FakeLLM(
        llm_payload(
            "SELECT type_name, mttr FROM fab10.breakdown_fab10 "
            "WHERE type_name = 'DE_BE' ORDER BY mttr DESC LIMIT 50",
            source_tables=["fab10.breakdown_fab10"],
        )
    )
    executed_sql: list[str] = []

    class FakeExecutor:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def validate(self, sql: str) -> str:
            return sql

        def execute(self, sql: str) -> ReadOnlyQueryResult:
            executed_sql.append(sql)
            if "ILIKE 'DE_BE%'" in sql:
                return ReadOnlyQueryResult(
                    columns=["type_name", "mttr"],
                    rows=[{"type_name": "DE_BE_11", "mttr": 4.0}],
                    row_count=1,
                    sql=sql,
                    limit=100,
                )
            return ReadOnlyQueryResult(
                columns=["type_name", "mttr"], rows=[], row_count=0, sql=sql, limit=100
            )

    monkeypatch.setattr("app.sub_agent.text2sql.ReadOnlyQueryExecutor", FakeExecutor)

    result = answer_question("fab10 DE_BE 타입 고장 MTTR 알려줘", execute=True, llm_client=llm)

    assert result.status == "succeeded"
    assert result.row_count == 0
    assert "type_name = 'DE_BE'" in result.sql
    assert result.plan is not None
    assert result.plan.slots["type_prefix"].value == "DE_BE"
    assert len(executed_sql) == 1


def test_empty_relative_date_result_explains_snapshot_range(monkeypatch) -> None:
    llm = FakeLLM(
        llm_payload(
            "SELECT product_name, due_date FROM fab10.lotrelease_fab10 "
            "WHERE product_name = 'Product_3' AND due_date >= DATE '2026-08-31' "
            "AND due_date < DATE '2026-09-07' ORDER BY due_date LIMIT 50",
            source_tables=["fab10.lotrelease_fab10"],
        )
    )

    class FakeExecutor:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def validate(self, sql: str) -> str:
            return sql

        def execute(self, sql: str) -> ReadOnlyQueryResult:
            return ReadOnlyQueryResult(
                columns=["product_name", "due_date"], rows=[], row_count=0, sql=sql, limit=100
            )

    monkeypatch.setattr("app.sub_agent.text2sql.ReadOnlyQueryExecutor", FakeExecutor)

    result = answer_question(
        "fab10 Product_3 지난주 due_date 기준 lotrelease 보여줘",
        execute=True,
        llm_client=llm,
    )

    assert result.status == "succeeded"
    assert result.row_count == 0
    assert "조건에 맞는 행이 없습니다" in result.answer
    assert any("적재된 스냅샷" in limitation for limitation in result.limitations)


def test_route_lookup_exposes_allowed_route_table_to_llm() -> None:
    llm = FakeLLM(
        llm_payload(
            """
SELECT route, step, area, toolgroup
FROM fab11.route_product_10_fab11
ORDER BY step
LIMIT 100
""".strip(),
            source_tables=["fab11.route_product_10_fab11"],
        )
    )

    result = plan_text2sql("fab11 Product_10 route step 보여줘", llm_client=llm)

    assert result.status == "succeeded"
    assert result.plan is not None
    assert result.plan.template_id is None
    assert "fab11.route_product_10_fab11" in llm.calls[0]["schema_context"]["allowed_table_refs"]


def test_route_lookup_rejects_llm_sql_for_non_allowlisted_table() -> None:
    llm = FakeLLM(
        llm_payload(
            """
SELECT route, step
FROM fab10.route_product_1_fab10
ORDER BY step
LIMIT 100
""".strip(),
            source_tables=["fab10.route_product_1_fab10"],
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
FROM fab13.lotrelease_variable_due_dates_fab13
WHERE product_name = 'Product_1'
ORDER BY start_date, source_row_id
LIMIT 50
""".strip(),
            source_tables=["fab13.lotrelease_variable_due_dates_fab13"],
        )
    )

    result = plan_text2sql("fab13 Product_1 release plan 보여줘", llm_client=llm)

    assert result.status == "succeeded"
    assert result.query_type == "release_plan_lookup"
    assert result.sql is not None
    assert "fab13.lotrelease_variable_due_dates_fab13" in result.sql
    assert "Product_1" in result.sql


def test_missing_fab_asks_for_clarification_without_llm_call() -> None:
    llm = FakeLLM(llm_payload("SELECT * FROM fab10.toolgroups_fab10 LIMIT 1"))
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
FROM fab10.toolgroups_fab10
ORDER BY area, toolgroup
LIMIT 50
""".strip(),
            source_tables=["fab10.toolgroups_fab10"],
        )
    )

    sql = generate_sql("fab10 Dry_Etch toolgroup 목록 보여줘", llm_client=llm)

    assert "FROM fab10.toolgroups_fab10" in sql


def test_status_query_supports_multiple_equipment_and_requested_metrics() -> None:
    result = plan_text2sql(
        "fab10 DE_BE_11과 DE_BE_12 현재 utilization과 down 알려줘",
        deterministic_only=True,
    )

    assert result.status == "succeeded"
    assert result.plan is not None
    assert result.plan.template_id == "deterministic_status_autosched_stn_multi"
    assert result.plan.slots["toolgroups"].value == "DE_BE_11,DE_BE_12"
    assert result.plan.select_items == [
        "report_time", "period", "stn", "util_percent", "down_percent"
    ]
    assert "lower(stn) IN ('de_be_11', 'de_be_12')" in (result.sql or "")


def test_status_query_preserves_metric_threshold_and_top_n() -> None:
    result = plan_text2sql(
        "fab10 Dry_Etch utilization 80% 이상인 설비군 상위 5개",
        deterministic_only=True,
    )

    assert result.status == "succeeded"
    assert result.plan is not None
    assert result.plan.slots["threshold_metric"].value == "util_percent"
    assert result.plan.slots["threshold_operator"].value == ">="
    assert result.plan.slots["threshold_value"].value == "80"
    assert result.plan.slots["top_n"].value == "5"
    assert "AND util_percent >= 80" in (result.sql or "")
    assert "ORDER BY util_percent DESC NULLS LAST" in (result.sql or "")
    assert (result.sql or "").endswith("LIMIT 5")
    assert {
        "field": "util_percent",
        "operator": ">=",
        "value": "80",
    } in result.plan.filters


def test_status_query_preserves_lower_ranking_and_strict_threshold() -> None:
    result = plan_text2sql(
        "fab10 Dry_Etch WIP 100 초과 설비군 하위 3개",
        deterministic_only=True,
    )

    assert result.status == "succeeded"
    assert "AND wiplotavg > 100" in (result.sql or "")
    assert "ORDER BY wiplotavg ASC NULLS LAST" in (result.sql or "")
    assert (result.sql or "").endswith("LIMIT 3")


def test_status_query_accepts_korean_percent_word_in_threshold() -> None:
    result = plan_text2sql(
        "fab10 Dry_Etch utilization 75 퍼센트 이상 설비군 상위 2개",
        deterministic_only=True,
    )

    assert result.status == "succeeded"
    assert "AND util_percent >= 75" in (result.sql or "")
    assert (result.sql or "").endswith("LIMIT 2")


@pytest.mark.parametrize(
    ("question", "expected_predicate"),
    [
        ("fab10 Dry_Etch utilization >= 82.5% 상위 4개", "util_percent >= 82.5"),
        ("fab10 Dry_Etch WIP < 120 하위 6개", "wiplotavg < 120"),
        ("fab10 Dry_Etch down above 5 percent 설비군 상위 3개", "down_percent > 5"),
    ],
)
def test_status_query_supports_symbolic_and_english_thresholds(
    question: str,
    expected_predicate: str,
) -> None:
    result = plan_text2sql(question, deterministic_only=True)

    assert result.status == "succeeded"
    assert f"AND {expected_predicate}" in (result.sql or "")


@pytest.mark.parametrize(
    ("question", "answer_fragment"),
    [
        ("fab10 Dry_Etch utilization 120% 이상 설비군", "0~100"),
        ("fab10 Dry_Etch WIP 50% 이상 설비군", "percent 단위가 아닙니다"),
    ],
)
def test_status_query_rejects_invalid_threshold_range_or_unit(
    question: str,
    answer_fragment: str,
) -> None:
    result = plan_text2sql(question, deterministic_only=True)

    assert result.status == "needs_clarification"
    assert result.sql is None
    assert answer_fragment in result.answer


def test_status_follow_up_uses_context_metric_for_bare_threshold() -> None:
    result = plan_text2sql(
        "그중 80% 이상만 상위 5개",
        fab="fab10",
        process="Dry_Etch",
        metric="util_percent",
        query_type="status",
        deterministic_only=True,
    )

    assert result.status == "succeeded"
    assert result.plan is not None
    assert result.plan.slots["metric"].source == "request_context"
    assert result.plan.slots["threshold_metric"].value == "util_percent"
    assert "AND util_percent >= 80" in (result.sql or "")
    assert "ORDER BY util_percent DESC NULLS LAST" in (result.sql or "")
    assert (result.sql or "").endswith("LIMIT 5")


def test_status_query_rejects_out_of_range_top_n() -> None:
    result = plan_text2sql(
        "fab10 Dry_Etch utilization 설비군 상위 500개",
        deterministic_only=True,
    )

    assert result.status == "needs_clarification"
    assert result.sql is None
    assert "1~200" in result.answer


def test_status_query_preserves_full_equipment_numeric_suffix() -> None:
    result = plan_text2sql(
        "fab10 Diffusion_FE_127_9 현재 down 알려줘",
        deterministic_only=True,
    )

    assert result.status == "succeeded"
    assert result.plan is not None
    assert result.plan.slots["toolgroup"].value == "Diffusion_FE_127_9"
    assert "lower(stn) = 'diffusion_fe_127_9'" in (result.sql or "")


def test_trend_query_supports_multiple_equipment_and_metrics() -> None:
    result = plan_text2sql(
        "fab10 DE_BE_11과 DE_BE_12 utilization과 down 일별 추세",
        deterministic_only=True,
    )

    assert result.status == "succeeded"
    assert result.plan is not None
    assert result.plan.template_id == "deterministic_trend_autosched_stn_multi"
    assert "GROUP BY report_time::date, stn" in (result.sql or "")
    assert result.plan.chart_intent == {
        "type": "line",
        "x": "report_date",
        "y": ["util_percent", "down_percent"],
        "x_title": "Report Date",
        "y_title": "Util Percent / Down Percent",
        "series": "stn",
        "grain": "day",
        "missing_policy": "gap",
    }


def test_status_query_builds_cross_source_compound_impact_baseline() -> None:
    result = plan_text2sql(
        "fab10 현재 utilization과 cycle time baseline 알려줘",
        deterministic_only=True,
    )

    assert result.status == "succeeded"
    assert result.plan is not None
    assert result.plan.template_id == "deterministic_status_cross_source_impact_baseline"
    assert result.plan.source_tables == [
        "fab10.autosched_perf_fab10",
        "fab10.autosched_stngrp_fab10",
    ]
    assert "WITH perf AS" in (result.sql or "")
    assert "AVG(s.util_percent) AS util_percent" in (result.sql or "")
    assert "s.period = p.period" in (result.sql or "")
    assert result.plan.select_items == [
        "report_time",
        "period",
        "util_percent",
        "cycleavg",
        "lotcomps",
        "ontime_percent",
    ]
