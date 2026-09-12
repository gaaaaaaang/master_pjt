
from app.agents.graph import build_agent_graph, initial_graph_state
from app.agents.planner import PlannerDecision, create_plan
from app.agents.supervisor import review_plan
from app.db.metadata_catalog import group_tables, shared_records
from app.db.read_only import ReadOnlyQueryResult
from app.schemas.chat import ChatRequest
from app.sub_agent.text2sql import answer_question, plan_text2sql


class Reply:
    def __init__(self, value):
        self.value = value

    def complete_json(self, **kwargs):
        return self.value


def unavailable_plan():
    return {"status": "data_unavailable", "query_type": "master_data_lookup",
            "intent": "lookup toolgroups", "fab_id": "fab10", "rag_knowledge_base": None,
            "missing_slots": [], "selected_sub_agents": [], "execution_steps": [],
            "clarification_question": None,
            "limitations": ["The last request failed; alternate sources are prohibited."]}


def rejected_review():
    return {"status": "data_unavailable", "proceed": False, "selected_sub_agents": [],
            "answer": "No data access", "reason": "old conversation failure",
            "limitations": ["All access is unavailable"]}


def test_historical_failure_does_not_prevent_new_plan_or_supervisor_execution():
    plan = create_plan("fab10 Dry_Etch toolgroup 목록", conversation_history=[
        {"role": "assistant", "content": "All data inaccessible; no alternate sources"},
    ], llm_client=Reply(unavailable_plan()))
    assert plan.status == "ready"
    assert plan.selected_sub_agents == ["text2sql"]
    assert not plan.limitations
    reviewed, decision = review_plan(plan, "fab10 toolgroups", llm_client=Reply(rejected_review()))
    assert reviewed.status == "ready"
    assert decision["proceed"] is True
    assert reviewed.selected_sub_agents == ["text2sql"]
    assert not reviewed.limitations


def test_missing_user_slot_is_still_clarified():
    output = {**unavailable_plan(), "status": "needs_clarification", "missing_slots": ["fab_id"]}
    plan = create_plan("toolgroup 목록", llm_client=Reply(output))
    assert plan.status == "needs_clarification"
    base = PlannerDecision("needs_clarification", "master_data_lookup", "lookup", [], [])
    reviewed, _ = review_plan(base, "toolgroups", llm_client=Reply(rejected_review()))
    assert reviewed.status != "ready"


def test_explicit_master_lookup_replaces_previous_wip_trend_task():
    output = {**unavailable_plan(), "status": "ready", "query_type": "trend",
              "selected_sub_agents": ["text2sql", "visualization"], "limitations": []}
    plan = create_plan("fab10 Dry_Etch toolgroup 목록 보여줘", conversation_history=[
        {"role": "user", "content": "fab10 Dry_Etch WIP 추이 그래프"},
    ], llm_client=Reply(output))
    assert plan.query_type == "master_data_lookup"
    assert plan.selected_sub_agents == ["text2sql"]
    result = plan_text2sql("fab10 Dry_Etch toolgroup 목록 보여줘", query_type=plan.query_type)
    assert "fab10.toolgroups_fab10" in result.sql
    assert "autosched" not in result.sql
    compound = create_plan("fab10 Dry_Etch toolgroup별 WIP 추이 비교", llm_client=Reply(output))
    assert compound.query_type == "trend"


def catalog_row(fab, table, name="wip_lots", dtype="integer"):
    return {"fab": fab, "physical_table": f"{table}_{fab}", "description": None,
            "column_name": name, "data_type": dtype, "nullable": False,
            "column_description": None}


def test_shared_meta_deduplicates_fabs_and_preserves_actual_schema_variants():
    tables = group_tables([
        catalog_row("fab10", "live_process_snapshots"),
        catalog_row("fab11", "live_process_snapshots"),
        catalog_row("fab12", "live_process_snapshots", dtype="numeric"),
    ])
    records = shared_records(tables)
    assert len(records) == 1
    record = records[0]
    assert record["table_pattern"] == "{fab}.live_process_snapshots_{fab}"
    assert record["columns"][0]["type"] == "integer"
    assert "columns_override" not in record["fab_status"]["fab11"]
    assert record["fab_status"]["fab12"]["columns_override"][0]["type"] == "numeric"
    assert record["fab_status"]["fab13"]["is_available"] is False


def test_text2sql_can_read_new_tables_and_columns_outside_static_catalog():
    tables = group_tables([catalog_row("fab11", "live_process_snapshots"),
                           catalog_row("fab11", "new_measurements", name="new_metric")])

    class SQLClient:
        def create_sql(self, **kwargs):
            context = kwargs["schema_context"]
            assert set(context["allowed_table_refs"]) == set(tables)
            assert context["tables"]["fab11.new_measurements_fab11"] == ["new_metric"]
            return {"supported": True, "sql": "SELECT wip_lots FROM fab11.live_process_snapshots_fab11 LIMIT 5"}

    result = plan_text2sql("fab11 시뮬레이션 WIP 조회. new_measurements의 new_metric도 참고해", database_catalog=tables, llm_client=SQLClient())
    assert result.status == "succeeded"
    assert result.plan.data_source_type == "simulation_snapshot"
    assert any("시뮬레이션" in item for item in result.limitations)
    assert result.plan.source_tables == ["fab11.live_process_snapshots_fab11"]


def test_graph_requeries_after_old_missing_table_error(monkeypatch):
    from app.agents.llm import AzureAgentClient
    original = AzureAgentClient.complete_json
    def complete(_self, **kwargs):
        if kwargs["schema_name"] == "fab_planner_decision":
            return unavailable_plan()
        if kwargs["schema_name"] == "fab_supervisor_decision":
            return rejected_review()
        if kwargs["schema_name"] == "fab_final_answer":
            return {"answer": "fab10 Dry_Etch의 toolgroup은 DE_BE_11입니다. 저장된 모델 입력 기준입니다."}
        return original(_self, **kwargs)

    monkeypatch.setattr("app.agents.graph.answer_question", answer_question)
    monkeypatch.setattr("app.agents.llm.AzureAgentClient.complete_json", complete)
    monkeypatch.setattr("app.sub_agent.text2sql.load_fab_catalog", lambda fab: group_tables([
        catalog_row(fab, "toolgroups", "toolgroup", "text"),
    ]))
    queries = []

    def execute(_self, sql, limit=None):
        queries.append(sql)
        return ReadOnlyQueryResult(["toolgroup", "area"],
                                   [{"toolgroup": "DE_BE_11", "area": "Dry_Etch"}], 1, sql, 50)

    monkeypatch.setattr("app.db.read_only.ReadOnlyQueryExecutor.execute", execute)
    state = initial_graph_state(ChatRequest(message="fab10 Dry_Etch toolgroup 목록 보여줘"),
                                conversation_history=[{
                                    "role": "assistant",
                                    "content": "fab10.toolgroups_fab10 does not exist. All access denied.",
                                }])
    result = build_agent_graph().invoke(state)
    assert len(queries) == 1
    assert result["status"] == "succeeded", (result["answer"], result["answer_review"])
    assert "fab10.toolgroups_fab10" in result["sql"]
    assert "DE_BE_11" in result["answer"]
    assert "All access" not in result["answer"]


def test_general_data_disclaimer_is_not_misread_as_a_live_claim():
    from app.sub_agent.reflection import _sounds_like_live_state
    assert not _sounds_like_live_state("live/current factory state로 해석하면 안 됩니다.")
    assert not _sounds_like_live_state("실시간 데이터가 아닙니다.")
    assert not _sounds_like_live_state("This is not live factory state.")
    assert _sounds_like_live_state("현재 WIP는 128개입니다. 실시간 데이터가 아닙니다.")


def test_natural_language_provenance_disclaimers_are_accepted():
    from app.sub_agent.reflection import _sounds_like_live_state
    assert not _sounds_like_live_state("이 정보는 실시간 fab10의 실제 운영 상태가 아니며, 현장 상황과 다를 수 있습니다.")
    assert not _sounds_like_live_state("현장 실시간 공정 또는 설비 운영과 불일치할 수 있습니다.")
    assert not _sounds_like_live_state("현재 fab10 공장의 실시간 운영 현황이 아님을 명확히 밝혀야 합니다.")
    assert _sounds_like_live_state("실시간 WIP는 128이며, 실시간 공정 데이터가 아닙니다.")
    assert _sounds_like_live_state("모델 입력이 아닙니다. 실시간 WIP는 128입니다.")


def test_numeric_grounding_accepts_display_rounding_but_not_different_values():
    from decimal import Decimal

    from app.sub_agent.reflection import _grounded_numeric_claim, _numeric_claims
    allowed = {Decimal("196.6666666666667")}
    assert _grounded_numeric_claim("196.67", Decimal("196.67"), allowed)
    assert not _grounded_numeric_claim("196.68", Decimal("196.68"), allowed)
    assert not _grounded_numeric_claim("196", Decimal(196), allowed)
    assert _numeric_claims("2026-09-07 10:00 WIP 181.67") == {"181.67": Decimal("181.67")}


def test_executed_sql_limit_is_not_an_answer_numeric_claim():
    from app.sub_agent.reflection import _unsupported_status_numeric_claims
    sql = 'SELECT SUM(wip_lots) FROM fab12.live_process_snapshots_fab12 LIMIT 1;'
    evidence = [{'source_type': 'text2sql_plan', 'metadata': {'status': 'succeeded', 'sql': sql,
                             'sample_rows': [{'total': 1183}]}}]
    answer = 'WIP 합계는 1,183입니다. SQL 근거: ' + sql
    assert _unsupported_status_numeric_claims('fab12 WIP', answer, evidence) == []
    assert '999' in _unsupported_status_numeric_claims('fab12 WIP', answer + ' 처리량은 999입니다.', evidence)
    assert '2' in _unsupported_status_numeric_claims('fab12 WIP', answer.replace('LIMIT 1', 'LIMIT 2'), evidence)


def test_grounded_sql_quote_ignores_formatting_around_parentheses():
    from app.sub_agent.reflection import _without_grounded_sql
    sql = 'SELECT SUM(wip_lots) FROM fab12.live_process_snapshots_fab12 WHERE interval_end = (\n SELECT MAX(interval_end) FROM fab12.live_process_snapshots_fab12\n) LIMIT 1;'
    quote = sql.replace('(\n SELECT', '(SELECT').replace('\n)', ')')
    evidence = [{'source_type': 'text2sql_plan', 'metadata': {'status': 'succeeded', 'sql': sql}}]
    assert _without_grounded_sql(quote, evidence).strip() == ''


def test_planner_delegates_physical_table_discovery_instead_of_asking_user_for_schema():
    for missing in ('table_name', 'data_layer', 'data_source_type', 'table_name_or_data_layer'):
        output = {**unavailable_plan(), 'status': 'needs_clarification', 'query_type': 'status',
                  'missing_slots': [missing], 'clarification_question': 'Which physical data layer?'}
        plan = create_plan('fab12 시뮬레이션 스냅샷 lot_starts 합계', fab='fab10', llm_client=Reply(output))
        assert plan.status == 'ready'
        assert plan.slots['fab_id'].value == 'fab12'
        assert plan.selected_sub_agents == ['text2sql']
        assert not plan.missing_slots and plan.clarification_question is None
        assert not plan.limitations
        output['missing_slots'] = [missing, 'date_basis']
        plan = create_plan('fab12 lotrelease 날짜 추이', llm_client=Reply(output))
        assert plan.status == 'needs_clarification'
        assert plan.missing_slots == ['date_basis']
