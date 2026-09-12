from copy import deepcopy

import pytest
from app.agents.prompt_context import compact_prompt_data
from app.sub_agent.semantic_plan import SemanticPlan, validate_sql_plan
from app.sub_agent.semantic_sql import compile_single_table
from sqlglot import exp, parse_one

REF = "fab12.live_process_snapshots_fab12"


def plan(**updates):
    raw = {"supported": True, "reason": "query", "tables": [REF],
               "projections": [{"table": REF, "column": "area"}], "aggregates": [],
               "group_by": [], "filters": [], "joins": [], "latest_by": [], "time_basis": "interval_end",
               "result_grain": "area", "limitations": ["simulation"]}
    return SemanticPlan.model_validate({**raw, **updates})


@pytest.mark.parametrize("function", ["count", "count_rows", "count_distinct", "sum", "avg", "min", "max"])
def test_compiles_distinct_aggregate_contracts(function):
    p = plan(projections=[], aggregates=[{"source": {"table": REF, "column": "*" if function == "count_rows" else "wip_lots"}, "function": function, "alias": "total"}])
    sql = compile_single_table(p)
    assert sql is not None
    validate_sql_plan(sql, p)
    if function == "count_rows":
        assert "COUNT(*)" in sql
    elif function == "count_distinct":
        assert "COUNT(DISTINCT" in sql


@pytest.mark.parametrize("scope", ["global", "filtered"])
def test_latest_scope_and_static_predicates_survive_compilation(scope):
    p = plan(filters=[{"source": {"table": REF, "column": "area"}, "operator": "eq", "values": ["etch"]}],
             latest_by=[{"table": REF, "column": "interval_end"}], latest_scope=scope,
             order_by=[{"output": "area", "direction": "desc"}], result_limit=2)
    sql = compile_single_table(p)
    assert sql is not None
    validate_sql_plan(sql, p)
    subquery = next(parse_one(sql, read="postgres").find_all(exp.Subquery)).this
    assert (subquery.args.get("where") is not None) == (scope == "filtered")
    assert "DESC LIMIT 2" in sql


def test_literal_is_data_and_cannot_create_a_second_statement():
    p = plan(filters=[{"source": {"table": REF, "column": "area"}, "operator": "eq", "values": ["etch'; DROP TABLE secrets; --"]}])
    sql = compile_single_table(p)
    assert sql is not None
    tree = parse_one(sql, read="postgres")
    assert [literal.this for literal in tree.find_all(exp.Literal)] == ["etch'; DROP TABLE secrets; --"]


def test_declines_ambiguous_mixed_projection_and_aggregate():
    p = plan(aggregates=[{"source": {"table": REF, "column": "wip_lots"}, "function": "sum", "alias": "total"}])
    assert compile_single_table(p) is None


def test_compaction_keeps_values_limits_and_source_identity_without_mutating_trace():
    original = {"evidence": [{"source_type": "rag_chunk", "content": "Do not auto release",
                             "metadata": {"page_number": 17, "source_document": "manual.pdf", "retrieval_trace": {"rankings": list(range(100))}}},
                            {"source_type": "text2sql_plan", "metadata": {"status": "succeeded", "sample_rows": [{"wip_lots": 188}], "query_plan": {"source_tables": [REF], "generation_attempts": [1]}}}],
                "limitations": ["simulation only"], "retry_budget_remaining": 0}
    saved = deepcopy(original)
    compact = compact_prompt_data(original)
    assert original == saved
    assert compact["evidence"][0]["metadata"] == {"page_number": 17, "source_document": "manual.pdf"}
    assert compact["evidence"][1]["metadata"]["sample_rows"] == [{"wip_lots": 188}]
    assert compact["limitations"] == original["limitations"]
    assert compact["retry_budget_remaining"] == 0
