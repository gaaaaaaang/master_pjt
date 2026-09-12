from app.db.schema_retrieval import rank_tables, select_catalog


def table(name, *columns):
    return {"logical_table": name, "columns": [{"name": col} for col in columns]}


def test_explicit_unknown_table_is_retained_over_high_trust_distractors():
    catalog = {f"fab10.route_product_{i}_fab10": table(f"route_product_{i}", "toolgroup") for i in range(1, 10)}
    catalog["fab10.custom_sensor_fab10"] = table("custom_sensor", "value")
    selected, trace = select_catalog("fab10 custom_sensor의 value 조회", catalog, max_tables=1)
    assert list(selected) == ["fab10.custom_sensor_fab10"]
    assert trace["selected_columns"] == 1
    assert trace["total_columns"] == 10


def test_identifier_boundaries_keep_product_1_distinct_from_product_10():
    catalog = {f"fab11.route_product_{i}_fab11": table(f"route_product_{i}", "step") for i in (1, 10)}
    ranking = rank_tables("route_product_10 조회", catalog)
    assert ranking[0].table_ref == "fab11.route_product_10_fab11"
    assert ranking[0].explicit
    assert not ranking[1].explicit


def test_no_match_keeps_full_catalog_and_all_explicit_tables_survive_budget():
    catalog = {f"fab10.custom_{i}_fab10": table(f"custom_{i}", "value") for i in range(5)}
    selected, trace = select_catalog("모르는 용어", catalog, max_tables=1)
    assert len(selected) == 5 and trace["broadened_no_match"]
    selected, _ = select_catalog("custom_1과 custom_2를 조회", catalog, max_tables=1)
    assert set(selected) == {"fab10.custom_1_fab10", "fab10.custom_2_fab10"}


def test_meaning_aliases_find_maintenance_without_table_name():
    catalog = {"fab10.pm_fab10": table("pm", "mean"),
               "fab10.toolgroups_fab10": table("toolgroups", "number_of_tools")}
    assert rank_tables("예방정비의 정의 목록", catalog)[0].table_ref == "fab10.pm_fab10"


def test_primary_join_tables_survive_budget_without_mutating_catalog():
    catalog = {"fab10.pm_fab10": table("pm", "mean"),
               "fab10.toolgroups_fab10": table("toolgroups", "area", "toolgroup")}
    selected, _ = select_catalog("예방정비", catalog, max_tables=1,
                                 primary_refs=["fab10.toolgroups_fab10"])
    assert "fab10.toolgroups_fab10" in selected
    assert "semantics" not in catalog["fab10.pm_fab10"]


def test_focused_generation_broadens_once_when_retrieval_cannot_answer():
    from app.db.metadata_catalog import group_tables
    from app.sub_agent.text2sql import plan_text2sql

    names = ['live_process_snapshots', *(f'custom_{i}' for i in range(8))]
    catalog = group_tables([{'fab': 'fab11', 'physical_table': f'{name}_fab11',
                            'column_name': 'wip_lots', 'data_type': 'integer',
                            'nullable': False} for name in names])
    calls = []

    class Client:
        def create_sql(self, **kwargs):
            context = kwargs['schema_context']
            calls.append(context)
            if len(calls) == 1:
                assert len(context['tables']) == 1
                assert 'fab11.custom_7_fab11' not in context['tables']
                return {'supported': False, 'reason': 'Need another measurement source'}
            assert len(context['tables']) == 9
            assert context['execution_feedback'][-1]['stage'] == 'schema_retrieval'
            return {'supported': True, 'sql': 'SELECT wip_lots FROM fab11.custom_7_fab11 LIMIT 5'}

    result = plan_text2sql('fab11 시뮬레이션 WIP 조회', database_catalog=catalog, llm_client=Client())
    assert len(calls) == 2
    assert result.status == 'succeeded'
    assert 'fab11.custom_7_fab11' in result.sql


def test_semantic_failure_repairs_same_schema_once_and_preserves_attempts():
    from app.db.metadata_catalog import group_tables
    from app.sub_agent.text2sql import plan_text2sql

    ref = 'fab11.live_process_snapshots_fab11'
    catalog = group_tables([{'fab': 'fab11', 'physical_table': 'live_process_snapshots_fab11',
                            'column_name': 'wip_lots', 'data_type': 'integer', 'nullable': False}])
    calls = []

    class Client:
        def create_sql(self, **kwargs):
            ctx = kwargs['schema_context']
            calls.append(ctx)
            ctx['validated_semantic_plan'] = {
                'supported': True, 'reason': 'time average', 'tables': [ref], 'projections': [],
                'aggregates': [{'source': {'table': ref, 'column': 'wip_lots'}, 'function': 'avg', 'alias': 'mean_wip'}],
                'group_by': [], 'filters': [], 'joins': [], 'latest_by': [],
                'time_basis': 'all intervals', 'result_grain': 'one mean', 'limitations': []}
            if len(calls) == 2:
                assert ctx['tables'] == calls[0]['tables']
                assert ctx['execution_feedback'][-1]['stage'] == 'validation_repair'
                assert 'SUM' in ctx['execution_feedback'][-1]['previous_sql']
            fn = 'SUM' if len(calls) == 1 else 'AVG'
            return {'supported': True, 'sql': f'SELECT {fn}(wip_lots) AS mean_wip FROM {ref}'}

    result = plan_text2sql('fab11 시뮬레이션 WIP 시간평균', database_catalog=catalog, llm_client=Client())
    assert result.status == 'succeeded'
    assert len(calls) == 2
    assert [a['status'] for a in result.plan.generation_attempts] == ['failed', 'succeeded']
    assert result.plan.generation_attempts[1]['action'] == 'validation_repair'


def test_repeated_validation_failure_stops_after_two_candidates():
    from app.db.metadata_catalog import group_tables
    from app.sub_agent.text2sql import plan_text2sql
    catalog = group_tables([{'fab': 'fab11', 'physical_table': 'live_process_snapshots_fab11',
                            'column_name': 'wip_lots', 'data_type': 'integer', 'nullable': False}])
    calls = []

    class Client:
        def create_sql(self, **kwargs):
            calls.append(kwargs)
            return {'supported': True, 'sql': 'SELECT wip_lots FROM fab10.unknown_fab10 LIMIT 5'}

    result = plan_text2sql('fab11 시뮬레이션 WIP', database_catalog=catalog, llm_client=Client())
    assert result.status == 'failed'
    assert len(calls) == 2
    assert len(result.plan.generation_attempts) == 2


def test_zero_score_fillers_are_omitted_but_verified_join_neighbor_is_kept():
    catalog = {'fab10.custom_sensor_fab10': table('custom_sensor', 'reading'),
               'fab10.dimension_fab10': table('dimension', 'key'),
               'fab10.unrelated_fab10': table('unrelated', 'other')}
    catalog['fab10.custom_sensor_fab10']['relationships'] = [
        {'target_table_ref': 'fab10.dimension_fab10', 'keys': [{'source': 'reading', 'target': 'key'}]}]
    selected, _ = select_catalog('custom_sensor reading', catalog)
    assert set(selected) == {'fab10.custom_sensor_fab10', 'fab10.dimension_fab10'}


def test_compacted_prompt_keeps_grounding_semantics_without_mutating_trace():
    from app.sub_agent.text2sql import _compact_model_context
    original = {'grounding': {'ranking': [1]}, 'table_details': {'t': {
        'columns': [{'name': 'wip_lots', 'description': 'snapshot count'}],
        'semantics': {'grain': 'area/time', 'column_meanings': {'wip_lots': {}}},
        'metrics': [{'column': 'wip_lots', 'kind': 'snapshot_count'}]}}}
    compact = _compact_model_context(original)
    assert 'grounding' not in compact
    assert 'column_meanings' not in compact['table_details']['t']['semantics']
    assert compact['table_details']['t']['metrics'] == original['table_details']['t']['metrics']
    assert 'grounding' in original
    assert 'column_meanings' in original['table_details']['t']['semantics']


def test_table_mentions_and_pm_domain_do_not_match_inside_equipment_identifier():
    from app.db.schema_retrieval import mentions_table
    from app.sub_agent.text2sql import _parse_master_domain
    assert not mentions_table('equipment_id가 NULL이 아닌 행 개수', 'fab13.pm_fab13', 'pm')
    assert _parse_master_domain('equipment_id가 null이 아닌 행 개수') is None
    assert _parse_master_domain('pm_percent 평균') is None
    for question in ('PM 설정', 'pm_fab13 테이블', 'fab13.pm_fab13 조회', 'fab13."pm_fab13" 조회'):
        assert mentions_table(question, 'fab13.pm_fab13', 'pm')
        assert _parse_master_domain(question.casefold()) == 'pm'
