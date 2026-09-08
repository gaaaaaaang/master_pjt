import pytest
from app.db.fab_catalog import bind_table_pattern, resolve_fab, table_ref
from app.sub_agent.text2sql import plan_text2sql
from scripts.migrate_fab_table_names import build_rename_plan


@pytest.mark.parametrize("fab", ["fab10", "fab11", "fab12", "fab13"])
def test_common_pattern_binds_both_schema_and_table(fab):
    assert bind_table_pattern("{fab}.toolgroups_{fab}", fab) == f"{fab}.toolgroups_{fab}"
    result = plan_text2sql(f"{fab} toolgroup 목록", deterministic_only=True)
    assert result.status == "succeeded"
    assert result.plan.source_tables == [f"{fab}.toolgroups_{fab}"]
    assert f"FROM {fab}.toolgroups_{fab}" in result.sql
    assert "{fab}" not in result.sql


@pytest.mark.parametrize("alias", ["FAB 11", "FAB-11", "팹11", "11번 팹", "M11"])
def test_alias_overrides_old_request_scope(alias):
    result = plan_text2sql(f"{alias} toolgroup 목록", fab="fab10", deterministic_only=True)
    assert result.status == "succeeded"
    assert result.plan.fab_id == "fab11"
    assert result.plan.slots["fab_id"].source == "explicit_user"
    assert result.plan.source_tables == ["fab11.toolgroups_fab11"]


def test_followup_uses_latest_user_scope_not_assistant_examples():
    history = [
        {"role": "user", "content": "fab10 toolgroup 목록"},
        {"role": "user", "content": "이번에는 12번 팹"},
        {"role": "assistant", "content": "예: fab13.toolgroups_fab13"},
    ]
    result = plan_text2sql("그럼 toolgroup 목록", conversation_history=history,
                          deterministic_only=True)
    assert result.plan.fab_id == "fab12"
    assert result.plan.slots["fab_id"].source == "conversation_context"
    assert result.plan.source_tables == ["fab12.toolgroups_fab12"]


@pytest.mark.parametrize("question", ["fab10과 fab11 비교", "fab99 toolgroups", "팹14 목록"])
def test_ambiguous_or_invalid_explicit_scope_does_not_fall_back(question):
    result = plan_text2sql(question, fab="fab10", deterministic_only=True)
    assert result.status == "needs_clarification"
    assert result.sql is None
    assert result.plan.fab_id is None


def test_no_fab_is_inferred_from_product_or_assistant_history():
    assert resolve_fab("Product_3 조회").fab_id is None
    result = plan_text2sql("toolgroup 목록", conversation_history=[
        {"role": "assistant", "content": "fab10이 있습니다."},
    ], deterministic_only=True)
    assert result.status == "needs_clarification"
    assert result.sql is None


@pytest.mark.parametrize("pattern,fab", [
    ("{fab}.toolgroups_{fab}; DROP TABLE x", "fab10"),
    ("{fab}.toolgroups_{fab}", "fab10;select"),
    ("{fab}.toolgroups_fab11", "fab10"),
    ("public.toolgroups_{fab}", "fab10"),
    ("{fab}.toolgroups_{fab}", "fab99"),
])
def test_binding_rejects_non_catalog_identifiers(pattern, fab):
    with pytest.raises(ValueError):
        bind_table_pattern(pattern, fab)


def test_double_suffix_is_rejected():
    with pytest.raises(ValueError):
        table_ref("fab10", "toolgroups_fab10")


def test_llm_gets_bound_names_and_cannot_switch_fab_or_use_old_names():
    class Client:
        def __init__(self, sql):
            self.sql = sql

        def create_sql(self, **kwargs):
            context = kwargs["schema_context"]
            assert context["allowed_table_refs"] == ["fab11.toolgroups_fab11"]
            assert context["table_patterns"] == {
                "fab11.toolgroups_fab11": "{fab}.toolgroups_{fab}",
            }
            return {"supported": True, "sql": self.sql,
                    "source_tables": ["fab10.toolgroups_fab10"]}

    for ref in ("fab10.toolgroups_fab10", "fab11.toolgroups_fab10", "fab11.toolgroups"):
        result = plan_text2sql("팹11 Dry_Etch toolgroup 목록", llm_client=Client(f"SELECT * FROM {ref}"))
        assert result.status == "failed"
    result = plan_text2sql("팹11 Dry_Etch toolgroup 목록", llm_client=Client(
        "SELECT toolgroup FROM fab11.toolgroups_fab11 ORDER BY toolgroup LIMIT 5"))
    assert result.status == "succeeded"
    assert result.plan.source_tables == ["fab11.toolgroups_fab11"]


def test_migration_is_idempotent_and_reversible():
    before = [("fab10", "toolgroups", 123), ("fab11", "toolgroups", 456)]
    plan = build_rename_plan(before)
    assert len(plan) == 2
    after = [(row["schema"], row["to"], row["oid"]) for row in plan]
    assert build_rename_plan(after) == []
    reverse = build_rename_plan(after, reverse=True)
    assert [(row["schema"], row["to"], row["oid"]) for row in reverse] == before


def test_migration_refuses_collisions_and_mismatched_suffixes():
    with pytest.raises(ValueError, match="already exists"):
        build_rename_plan([("fab10", "pm", 1), ("fab10", "pm_fab10", 2)])
    with pytest.raises(ValueError, match="Mismatched"):
        build_rename_plan([("fab10", "pm_fab11", 1)])
