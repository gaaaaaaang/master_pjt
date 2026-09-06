import json

from app.db.read_only import ReadOnlyQueryExecutor
from scripts.check_sc001_release_gate import DEFAULT_CONTRACT, compare_contract


def _contract() -> dict:
    return json.loads(DEFAULT_CONTRACT.read_text(encoding="utf-8"))


def test_sc001_golden_queries_are_read_only_and_schema_qualified() -> None:
    contract = _contract()
    validator = ReadOnlyQueryExecutor(dsn="postgresql://validation-only")

    for query in contract["queries"]:
        assert "fab10.autosched_" in query["sql"]
        assert validator.validate(query["sql"])


def test_sc001_robustness_queries_use_distinct_entities_and_phrasings() -> None:
    queries = [
        query
        for query in _contract()["queries"]
        if query.get("evaluation_role") == "robustness"
    ]

    assert len(queries) >= 5
    assert {query["table"] for query in queries} == {
        "autosched_perf",
        "autosched_stngrp",
        "autosched_stn",
        "autosched_part",
        "autosched_lot",
    }
    assert all(len(query["question_variants"]) >= 2 for query in queries)
    assert len({variant for query in queries for variant in query["question_variants"]}) == 10


def test_release_gate_passes_matching_snapshot() -> None:
    contract = _contract()
    tables = {name: expected.copy() for name, expected in contract["tables"].items()}
    queries = {query["id"]: query["expected"].copy() for query in contract["queries"]}

    result = compare_contract(contract, tables, queries)

    assert result["passed"] is True
    assert result["failures"] == []


def test_release_gate_fails_partial_load_and_wrong_golden_result() -> None:
    contract = _contract()
    tables = {name: expected.copy() for name, expected in contract["tables"].items()}
    queries = {query["id"]: query["expected"].copy() for query in contract["queries"]}
    tables["autosched_stn"]["row_count"] = 5
    queries["station_latest_status"] = None

    result = compare_contract(contract, tables, queries)

    assert result["passed"] is False
    assert any("autosched_stn" in failure for failure in result["failures"])
    assert any("station_latest_status" in failure for failure in result["failures"])
