import pytest

from app.db.read_only import ReadOnlyQueryExecutor, SqlValidationError


def executor() -> ReadOnlyQueryExecutor:
    return ReadOnlyQueryExecutor(
        dsn="postgresql://example",
        allowed_schemas={"fab10", "fab11", "fab12", "fab13"},
        timeout_seconds=5,
        max_rows=100,
    )


def test_validate_allows_schema_qualified_select() -> None:
    sql = executor().validate("SELECT toolgroup FROM fab10.toolgroups")
    assert sql == "SELECT toolgroup FROM fab10.toolgroups"


def test_validate_allows_with_query() -> None:
    sql = executor().validate(
        "WITH station AS (SELECT stn FROM fab11.autosched_stn) SELECT * FROM station"
    )
    assert sql.startswith("WITH station")


def test_validate_rejects_write_statement() -> None:
    with pytest.raises(SqlValidationError, match="Only SELECT or WITH"):
        executor().validate("DELETE FROM fab10.toolgroups")


def test_validate_rejects_forbidden_keyword_inside_select() -> None:
    with pytest.raises(SqlValidationError, match="Forbidden SQL keyword"):
        executor().validate("SELECT * FROM fab10.toolgroups FOR UPDATE")


def test_validate_ignores_forbidden_keyword_inside_string_literal() -> None:
    sql = executor().validate("SELECT * FROM fab10.toolgroups WHERE area = 'drop'")
    assert "drop" in sql


def test_validate_ignores_semicolon_inside_string_literal() -> None:
    sql = executor().validate("SELECT * FROM fab10.toolgroups WHERE area = 'Dry;Etch'")
    assert "Dry;Etch" in sql


def test_validate_rejects_unqualified_table() -> None:
    with pytest.raises(SqlValidationError, match="schema-qualified"):
        executor().validate("SELECT * FROM toolgroups")


def test_validate_rejects_unallowed_schema() -> None:
    with pytest.raises(SqlValidationError, match="Schema is not allowed"):
        executor().validate("SELECT * FROM public.users")


def test_validate_rejects_multiple_statements() -> None:
    with pytest.raises(SqlValidationError, match="Only one SQL statement"):
        executor().validate("SELECT * FROM fab10.toolgroups; SELECT * FROM fab11.toolgroups")


def test_with_limit_wraps_query_and_caps_limit() -> None:
    sql, limit = executor().with_limit("SELECT * FROM fab13.route_product_10", limit=500)
    assert limit == 100
    assert sql.endswith("LIMIT 100")
    assert "fab13.route_product_10" in sql
