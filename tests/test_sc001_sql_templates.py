import pytest

from app.db.read_only import ReadOnlyQueryExecutor
from app.sub_agent.sql_templates import (
    fab_status_summary,
    lot_status,
    process_group_status,
    product_status,
    station_status,
)


@pytest.fixture()
def executor() -> ReadOnlyQueryExecutor:
    return ReadOnlyQueryExecutor(
        dsn="postgresql://example",
        allowed_schemas={"fab10", "fab11", "fab12", "fab13"},
        timeout_seconds=5,
        max_rows=100,
    )


def test_sc001_templates_are_read_only_valid(executor: ReadOnlyQueryExecutor) -> None:
    templates = [
        fab_status_summary("fab10"),
        process_group_status("fab11", "Dry_Etch"),
        station_status("fab12", "DE_BE_11"),
        product_status("fab13", "part_3"),
        lot_status("fab10", "Init_Lot_3_24"),
    ]

    for template in templates:
        assert template.name.startswith("sc001_")
        assert executor.validate(template.sql)


def test_sc001_templates_reject_unsupported_fab() -> None:
    with pytest.raises(ValueError, match="Unsupported fab_id"):
        fab_status_summary("public")


def test_sc001_templates_escape_string_slots(executor: ReadOnlyQueryExecutor) -> None:
    template = station_status("fab10", "DE_BE_11'; DROP TABLE fab10.toolgroups; SELECT '")
    assert "DROP TABLE" in template.sql
    assert executor.validate(template.sql)
