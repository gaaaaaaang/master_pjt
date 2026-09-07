from types import SimpleNamespace

from app.sub_agent.text2sql import QueryPlan, QuerySlot, Text2SQLResult
from scripts.smoke_text2sql_postgres import evaluate_case


def test_evaluate_case_uses_semantic_result_contract_for_em() -> None:
    case = {
        "id": "semantic",
        "expected_status": "succeeded",
        "expected_query_type": "trend",
        "expect_sql": True,
        "expected_sql_contains": ["from FAB10.AUTOSCHED_PART"],
        "target_source_tables": ["fab10.autosched_part"],
        "target_columns": ["part", "cycleavg", "ontime_percent"],
        "target_chart_type": "grouped_bar",
        "target_date_basis": "due_date",
        "target_relative_period": "last_week",
    }
    result = Text2SQLResult(
        status="succeeded",
        query_type="trend",
        answer="ok",
        sql="SELECT part, cycleavg, ontime_percent FROM fab10.autosched_part",
        columns=["part", "cycleavg", "ontime_percent"],
        row_count=2,
        plan=QueryPlan(
            query_type="trend",
            template_id=None,
            fab_id="fab10",
            source_tables=["fab10.autosched_part"],
            slots={
                "date_basis": QuerySlot("due_date", "parser", 1.0, "due_date"),
                "relative_period": QuerySlot("last_week", "parser", 1.0, "지난주"),
            },
            chart_intent={"type": "grouped_bar"},
        ),
    )

    assert evaluate_case(case, result) == {"all": [], "ex": [], "em": [], "intent": []}


def test_evaluate_case_reports_missing_semantic_columns() -> None:
    case = {
        "id": "missing-column",
        "expected_status": "succeeded",
        "expected_query_type": "status",
        "expect_sql": True,
        "target_columns": ["ontime_percent"],
    }
    result = SimpleNamespace(
        status="succeeded",
        query_type="status",
        sql="SELECT part FROM fab10.autosched_part",
        answer="ok",
        limitations=[],
        row_count=1,
        columns=["part"],
        plan=None,
    )

    evaluation = evaluate_case(case, result)

    assert any("ontime_percent" in failure for failure in evaluation["em"])
