from copy import deepcopy

import pytest
from app.db.semantic_metadata import enrich
from app.sub_agent.semantic_plan import validate_plan
from app.sub_agent.text2sql import OpenAIText2SQLClient

REF = 'fab11.live_process_snapshots_fab11'


def context():
    entry = enrich({'logical_table': 'live_process_snapshots',
                    'data_source_type': 'simulation_snapshot',
                    'columns': [{'name': 'wip_lots', 'type': 'integer'},
                                {'name': 'yield_percent', 'type': 'numeric'},
                                {'name': 'area', 'type': 'text'}]})
    return {'tables': {REF: [c['name'] for c in entry['columns']]},
            'table_details': {REF: entry}}


def plan():
    return {'supported': True, 'reason': 'area WIP', 'tables': [REF],
            'projections': [{'table': REF, 'column': 'wip_lots'}], 'aggregates': [],
            'group_by': [], 'filters': [], 'joins': [], 'latest_by': [], 'time_basis': 'snapshot',
            'result_grain': 'area snapshot', 'limitations': ['synthetic']}


def test_unknown_column_is_rejected_before_generation():
    raw = plan()
    raw['projections'][0]['column'] = 'invented_column'
    with pytest.raises(ValueError, match='Unknown plan column'):
        validate_plan(raw, context())


@pytest.mark.parametrize('column, message', [('yield_percent', 'metric meaning'), ('area', 'nonnumeric')])
def test_invalid_aggregation_is_rejected(column, message):
    raw = plan()
    raw['aggregates'] = [{'source': {'table': REF, 'column': column}, 'function': 'sum', 'alias': 'total'}]
    with pytest.raises(ValueError, match=message):
        validate_plan(raw, context())


def test_disconnected_sources_and_cross_fab_sources_are_rejected():
    raw, ctx = plan(), context()
    other = 'fab11.live_process_events_fab11'
    raw['tables'].append(other)
    ctx['tables'][other] = ['area']
    with pytest.raises(ValueError, match='connected joins'):
        validate_plan(raw, ctx)
    raw['tables'][-1] = 'fab10.live_process_events_fab10'
    with pytest.raises(ValueError, match='absent'):
        validate_plan(raw, ctx)


def test_production_client_compiles_validated_simple_plan_without_second_model_call(monkeypatch):
    client = OpenAIText2SQLClient(api_key='test-key')
    calls = []

    def complete(payload):
        calls.append(deepcopy(payload))
        if len(calls) == 1:
            return plan()
        assert 'validated plan' in payload['messages'][-1]['content']
        return {'supported': True, 'sql': f'SELECT wip_lots FROM {REF} LIMIT 5'}

    monkeypatch.setattr(client, '_complete_payload', complete)
    ctx = context()
    result = client.create_sql(question='fab11 simulation WIP', query_type='status',
                               fab_id='fab11', slots={}, schema_context=ctx)
    assert result['supported']
    assert len(calls) == 1
    assert calls[0]['response_format']['json_schema']['name'] == 'fab_semantic_plan'
    assert ctx['validated_semantic_plan']['tables'] == [REF]
    assert ctx['grounding']['sql_generation_mode'] == 'compiled_validated_plan'
    from app.sub_agent.semantic_plan import SemanticPlan, validate_sql_plan
    validate_sql_plan(result['sql'], SemanticPlan.model_validate(ctx['validated_semantic_plan']))


def test_invalid_plan_prevents_any_sql_generation_call(monkeypatch):
    client = OpenAIText2SQLClient(api_key='test-key')
    calls = []

    def complete(payload):
        calls.append(payload)
        raw = plan()
        raw['projections'][0]['column'] = 'invented'
        return raw

    monkeypatch.setattr(client, '_complete_payload', complete)
    result = client.create_sql(question='fab11 WIP', query_type='status', fab_id='fab11',
                               slots={}, schema_context=context())
    assert not result['supported']
    assert len(calls) == 1


def aggregate_plan():
    raw = plan()
    raw['projections'] = [{'table': REF, 'column': 'area'}]
    raw['group_by'] = raw['projections']
    raw['aggregates'] = [{'source': {'table': REF, 'column': 'wip_lots'}, 'function': 'avg', 'alias': 'mean_wip'}]
    return validate_plan(raw, context())


@pytest.mark.parametrize('sql', [
    f'SELECT area, AVG(wip_lots) AS mean_wip FROM {REF} GROUP BY area',
    f'WITH x AS (SELECT area, AVG(wip_lots) AS mean_wip FROM {REF} GROUP BY area) SELECT area, mean_wip FROM x',
])
def test_sql_aggregate_lineage_matches_direct_or_cte_plan(sql):
    from app.sub_agent.semantic_plan import validate_sql_plan
    validate_sql_plan(sql, aggregate_plan())


@pytest.mark.parametrize('sql', [
    f'SELECT area, wip_lots FROM {REF} GROUP BY area, wip_lots',
    f'SELECT area, SUM(wip_lots) FROM {REF} GROUP BY area',
    f'SELECT area, AVG(wip_lots) FROM {REF} GROUP BY area, wip_lots',
    f'WITH unused AS (SELECT AVG(wip_lots) FROM {REF}) SELECT area,wip_lots FROM {REF}',
])
def test_sql_cannot_fake_planned_aggregate_with_grouping_or_unused_cte(sql):
    from app.sub_agent.semantic_plan import validate_sql_plan
    with pytest.raises(ValueError):
        validate_sql_plan(sql, aggregate_plan())


def filtered_plan():
    raw = plan()
    raw['filters'] = [{'source': {'table': REF, 'column': 'area'}, 'operator': 'eq', 'values': ['etch']}]
    return validate_plan(raw, context())


@pytest.mark.parametrize('condition', ["", " WHERE area = 'photo'", " WHERE area = 'etch' OR 1 = 1", " WHERE 'etch' = 'etch'"])
def test_missing_changed_or_optional_filter_is_rejected(condition):
    from app.sub_agent.semantic_plan import validate_sql_plan
    with pytest.raises(ValueError, match='predicate'):
        validate_sql_plan(f'SELECT wip_lots FROM {REF}{condition}', filtered_plan())


@pytest.mark.parametrize('sql', [
    f"SELECT wip_lots FROM {REF} WHERE area = 'etch'",
    f"SELECT x.wip_lots FROM {REF} x WHERE 'etch' = x.area",
    f"WITH x AS (SELECT * FROM {REF} WHERE area = 'etch') SELECT wip_lots FROM x",
])
def test_mandatory_filter_in_direct_query_or_used_cte_is_accepted(sql):
    from app.sub_agent.semantic_plan import validate_sql_plan
    validate_sql_plan(sql, filtered_plan())


def test_half_open_time_range_must_preserve_both_boundaries():
    from app.sub_agent.semantic_plan import validate_sql_plan
    ctx = context()
    ctx['tables'][REF].append('interval_end')
    raw = plan()
    raw['filters'] = [
        {'source': {'table': REF, 'column': 'interval_end'}, 'operator': 'gte', 'values': ['2026-09-06 00:00:00+09:00']},
        {'source': {'table': REF, 'column': 'interval_end'}, 'operator': 'lt', 'values': ['2026-09-07 00:00:00+09:00']},
    ]
    checked = validate_plan(raw, ctx)
    query = f"SELECT wip_lots FROM {REF} WHERE interval_end >= '2026-09-06 00:00:00+09:00'::timestamptz AND interval_end < '2026-09-07 00:00:00+09:00'::timestamptz"
    validate_sql_plan(query, checked)
    with pytest.raises(ValueError, match='predicate'):
        validate_sql_plan(query.replace('interval_end <', 'interval_end <='), checked)


def test_not_null_filter_and_invalid_predicate_arity():
    from app.sub_agent.semantic_plan import validate_sql_plan
    raw = plan()
    raw['filters'] = [{'source': {'table': REF, 'column': 'area'}, 'operator': 'not_null', 'values': []}]
    checked = validate_plan(raw, context())
    validate_sql_plan(f'SELECT wip_lots FROM {REF} WHERE area IS NOT NULL', checked)
    raw['filters'][0]['values'] = ['etch']
    with pytest.raises(ValueError, match='value count'):
        validate_plan(raw, context())


def latest_plan():
    raw, ctx = plan(), context()
    raw['latest_by'] = [{'table': REF, 'column': 'interval_end'}]
    ctx['tables'][REF].append('interval_end')
    ctx['table_details'][REF]['columns'].append({'name': 'interval_end', 'type': 'timestamp with time zone'})
    return validate_plan(raw, ctx)


def test_latest_snapshot_requires_matching_global_maximum_not_sorting():
    from app.sub_agent.semantic_plan import validate_sql_plan
    checked = latest_plan()
    good = f'SELECT wip_lots FROM {REF} WHERE interval_end = (SELECT MAX(interval_end) FROM {REF})'
    validate_sql_plan(good, checked)
    for bad in [f'SELECT wip_lots FROM {REF} ORDER BY interval_end DESC LIMIT 6',
                good.replace('MAX(interval_end)', 'MIN(interval_end)'),
                good.replace('MAX(interval_end)', 'MAX(interval_start)'),
                good.replace(f'FROM {REF})', f"FROM {REF} WHERE area='etch')"),
                good + ' OR 1=1']:
        with pytest.raises(ValueError, match='latest snapshot'):
            validate_sql_plan(bad, checked)


def test_latest_maximum_allows_redundant_bound_fab_filter():
    from app.sub_agent.semantic_plan import validate_sql_plan
    sql = f"SELECT wip_lots FROM {REF} WHERE interval_end=(SELECT MAX(interval_end) FROM {REF} WHERE fab_id='fab11')"
    validate_sql_plan(sql, latest_plan())
    with pytest.raises(ValueError, match='latest snapshot'):
        validate_sql_plan(sql.replace("fab_id='fab11'", "fab_id='fab12'"), latest_plan())


def test_whole_factory_total_does_not_inherit_input_area_grain():
    from app.sub_agent.semantic_plan import request_requirements
    raw, ctx = plan(), context()
    ctx['request_requirements'] = request_requirements('fab11 전체 WIP 합계를 영역별 wip_lots 합산으로 구해줘')
    raw['projections'] = [{'table': REF, 'column': 'area'}]
    raw['group_by'] = raw['projections']
    raw['aggregates'] = [{'source': {'table': REF, 'column': 'wip_lots'}, 'function': 'sum', 'alias': 'total'}]
    with pytest.raises(ValueError, match='whole-factory total'):
        validate_plan(raw, ctx)
    raw['projections'], raw['group_by'] = [], []
    validate_plan(raw, ctx)
    assert not request_requirements('전체 기간 영역별 평균 WIP')
    assert not request_requirements('공장 전체 WIP를 영역별로 보여줘')


def test_latest_marker_becomes_temporal_contract_and_never_a_string_filter():
    raw, ctx = plan(), context()
    ctx['tables'][REF].append('interval_end')
    ctx['table_details'][REF]['columns'].append({'name': 'interval_end', 'type': 'timestamp with time zone'})
    raw['filters'] = [{'source': {'table': REF, 'column': 'interval_end'}, 'operator': 'eq', 'values': ['LATEST']}]
    checked = validate_plan(raw, ctx)
    assert checked.latest_by[0].column == 'interval_end'
    assert checked.filters == []
    raw['filters'][0]['values'] = ['2026-09-07 00:00:00+09:00']
    checked = validate_plan(raw, ctx)
    assert not checked.latest_by
    assert checked.filters[0].values == ['2026-09-07 00:00:00+09:00']


def test_only_exact_same_source_max_expression_is_normalized():
    from app.sub_agent.semantic_plan import ColumnRef, _latest_expression
    ref = ColumnRef(table=REF, column='interval_end')
    good = f"(SELECT MAX(interval_end) FROM {REF} WHERE fab_id='fab11')"
    assert _latest_expression(good, ref)
    for bad in [good.replace('MAX(', 'MIN('), good.replace("fab_id='fab11'", "area='etch'"),
                good.replace('fab11', 'fab12'), good.replace('interval_end', 'interval_start')]:
        assert not _latest_expression(bad, ref)


@pytest.mark.parametrize('sql', [
    f'SELECT AVG(wip_lots) AS mean_wip FROM {REF}',
    f"SELECT 'etch' AS area, AVG(wip_lots) AS mean_wip FROM {REF}",
    f'SELECT area, AVG(wip_lots) FROM {REF} GROUP BY area,wip_lots',
])
def test_output_dimension_omission_or_changed_group_is_rejected(sql):
    from app.sub_agent.semantic_plan import validate_sql_plan
    with pytest.raises(ValueError):
        validate_sql_plan(sql, aggregate_plan())


@pytest.mark.parametrize('grouping', ['1', 'area_alias'])
def test_grouping_by_output_position_or_alias_is_resolved(grouping):
    from app.sub_agent.semantic_plan import validate_sql_plan
    validate_sql_plan(f'SELECT area AS area_alias, AVG(wip_lots) FROM {REF} GROUP BY {grouping}', aggregate_plan())


def test_undeclared_aggregate_cannot_be_added_to_correct_planned_measure():
    from app.sub_agent.semantic_plan import validate_sql_plan
    sql = f'SELECT area, AVG(wip_lots), SUM(wip_lots) FROM {REF} GROUP BY area'
    with pytest.raises(ValueError, match='extra'):
        validate_sql_plan(sql, aggregate_plan())


def test_join_keys_and_join_type_must_match_the_plan():
    from app.sub_agent.semantic_plan import SemanticPlan, validate_sql_plan
    left, right = 'fab11.pm_fab11', 'fab11.toolgroups_fab11'
    raw = plan()
    raw.update(tables=[left, right], projections=[{'table': left, 'column': 'mean'}],
               joins=[{'left': {'table': left, 'column': 'type_name'},
                       'right': {'table': right, 'column': 'toolgroup'}, 'kind': 'inner'}])
    checked = SemanticPlan.model_validate(raw)
    good = f'SELECT p.mean FROM {left} p JOIN {right} t ON p.type_name=t.toolgroup'
    validate_sql_plan(good, checked)
    for bad in [good.replace('t.toolgroup', 't.area'), good.replace(' JOIN ', ' LEFT JOIN '),
                good + ' OR 1=1', good.replace('p.type_name=t.toolgroup', '1=1')]:
        with pytest.raises(ValueError, match='join'):
            validate_sql_plan(bad, checked)


def test_composite_metadata_join_requires_all_keys_and_prevents_snapshot_fanout_sum():
    from app.db.semantic_metadata import enrich
    event = 'fab11.live_process_events_fab11'
    target = REF
    keys = ['interval_end', 'fab_id', 'area']
    ctx = {'tables': {event: keys + ['detail_id'], target: keys + ['wip_lots']},
           'table_details': {}}
    for ref, logical in [(event, 'live_process_events'), (target, 'live_process_snapshots')]:
        ctx['table_details'][ref] = enrich({'logical_table': logical,
            'data_source_type': 'simulation_snapshot',
            'columns': [{'name': c, 'type': 'integer' if c in {'detail_id', 'wip_lots'} else 'text'} for c in ctx['tables'][ref]]})
    ctx['table_details'][event]['relationships'][0]['target_table_ref'] = target
    raw = plan()
    raw.update(tables=[event,target], projections=[{'table': event, 'column': 'detail_id'}],
               joins=[{'left': {'table': event, 'column': k}, 'right': {'table': target, 'column': k}, 'kind': 'inner'} for k in keys])
    validate_plan(raw, ctx)
    missing = deepcopy(raw)
    missing['joins'] = missing['joins'][1:]
    with pytest.raises(ValueError, match='omits keys'):
        validate_plan(missing, ctx)
    raw['projections'] = []
    raw['aggregates'] = [{'source': {'table': target, 'column': 'wip_lots'}, 'function': 'sum', 'alias': 'bad_total'}]
    with pytest.raises(ValueError, match='duplicates'):
        validate_plan(raw, ctx)


def test_row_count_contract_supports_count_star_without_confusing_nullable_count():
    from app.sub_agent.semantic_plan import validate_sql_plan
    raw = plan()
    raw['projections'] = []
    raw['aggregates'] = [{'source': {'table': REF, 'column': '*'}, 'function': 'count_rows', 'alias': 'n'}]
    checked = validate_plan(raw, context())
    validate_sql_plan(f'SELECT COUNT(*) AS n FROM {REF}', checked)
    validate_sql_plan(f'SELECT COUNT(1) AS n FROM {REF}', checked)
    with pytest.raises(ValueError, match='aggregates'):
        validate_sql_plan(f'SELECT COUNT(area) AS n FROM {REF}', checked)
    raw['aggregates'][0] = {'source': {'table': REF, 'column': 'area'}, 'function': 'count', 'alias': 'n'}
    checked = validate_plan(raw, context())
    with pytest.raises(ValueError, match='aggregates'):
        validate_sql_plan(f'SELECT COUNT(*) AS n FROM {REF}', checked)


def test_latest_timestamp_output_is_normalized_before_total_sql_generation():
    from app.sub_agent.semantic_plan import validate_sql_plan
    raw, ctx = plan(), context()
    ref = {'table': REF, 'column': 'interval_end'}
    ctx['tables'][REF].append('interval_end')
    ctx['table_details'][REF]['columns'].append({'name': 'interval_end', 'type': 'timestamptz'})
    raw.update(projections=[ref], latest_by=[ref], aggregates=[{
        'source': {'table': REF, 'column': 'wip_lots'}, 'function': 'sum', 'alias': 'total'}])
    checked = validate_plan(raw, ctx)
    assert checked.projections == []
    assert [a.function for a in checked.aggregates] == ['sum', 'max']
    sql = f'SELECT MAX(interval_end), SUM(wip_lots) FROM {REF} WHERE interval_end=(SELECT MAX(interval_end) FROM {REF})'
    validate_sql_plan(sql, checked)
    with pytest.raises(ValueError, match='aggregates differ'):
        validate_sql_plan(sql.replace('MAX(interval_end), ', ''), checked)
    raw['latest_by'] = []
    assert validate_plan(raw, ctx).projections  # No invented latest constraint.


def test_grouped_latest_timestamp_can_represent_its_max_but_requires_latest_filter():
    from app.sub_agent.semantic_plan import validate_sql_plan
    raw, ctx = plan(), context()
    ref = {'table': REF, 'column': 'interval_end'}
    ctx['tables'][REF].append('interval_end')
    raw.update(projections=[ref], group_by=[ref], latest_by=[ref], aggregates=[
        {'source': {'table': REF, 'column': 'wip_lots'}, 'function': 'sum', 'alias': 'total'},
        {'source': ref, 'function': 'max', 'alias': 'latest_interval_end'}],
        filters=[{'source': ref, 'operator': 'eq', 'values': ['<latest_interval_end>']}])
    checked = validate_plan(raw, ctx)
    assert not checked.filters
    sql = f'SELECT interval_end, SUM(wip_lots) FROM {REF} WHERE interval_end=(SELECT MAX(interval_end) FROM {REF}) GROUP BY interval_end'
    validate_sql_plan(sql, checked)
    with pytest.raises(ValueError, match='latest snapshot'):
        validate_sql_plan(f'SELECT interval_end, SUM(wip_lots) FROM {REF} GROUP BY interval_end', checked)


@pytest.mark.parametrize('expression', [
    'SUM(wip_lots * 2)', 'SUM(DISTINCT wip_lots)', 'SUM(wip_lots) * 2',
    'COALESCE(SUM(wip_lots), 0)', 'SUM(CASE WHEN area = \'etch\' THEN wip_lots ELSE 0 END)',
])
def test_aggregate_contract_rejects_undeclared_measure_transformations(expression):
    from app.sub_agent.semantic_plan import validate_sql_plan
    raw = plan()
    raw.update(projections=[], aggregates=[{
        'source': {'table': REF, 'column': 'wip_lots'}, 'function': 'sum', 'alias': 'total'}])
    checked = validate_plan(raw, context())
    validate_sql_plan(f'SELECT SUM(wip_lots) AS total FROM {REF}', checked)
    with pytest.raises(ValueError, match='aggregate (input|output)'):
        validate_sql_plan(f'SELECT {expression} AS total FROM {REF}', checked)


def ranked_plan():
    raw = plan()
    raw.update(projections=[{'table': REF, 'column': 'area'}, {'table': REF, 'column': 'wip_lots'}],
               order_by=[{'output': 'wip_lots', 'direction': 'desc'}, {'output': 'area', 'direction': 'asc'}],
               result_limit=3)
    return validate_plan(raw, context())


@pytest.mark.parametrize('suffix', [
    'ORDER BY wip_lots ASC, area LIMIT 3', 'ORDER BY wip_lots DESC LIMIT 3',
    'ORDER BY area, wip_lots DESC LIMIT 3', 'ORDER BY wip_lots DESC, area LIMIT 5',
    'ORDER BY wip_lots DESC, area LIMIT 3 OFFSET 1', 'LIMIT 3',
])
def test_ranked_output_requires_order_tie_breaker_limit_and_first_page(suffix):
    from app.sub_agent.semantic_plan import validate_sql_plan
    with pytest.raises(ValueError, match='ordering|limit|OFFSET'):
        validate_sql_plan(f'SELECT area, wip_lots FROM {REF} {suffix}', ranked_plan())


def test_ranking_accepts_source_alias_ordinal_and_aggregate_output():
    from app.sub_agent.semantic_plan import validate_sql_plan
    for order in ('wip_lots DESC, area ASC', 'amount DESC, area', '2 DESC, 1'):
        validate_sql_plan(f'SELECT area, wip_lots AS amount FROM {REF} ORDER BY {order} LIMIT 3', ranked_plan())
    raw = plan()
    raw.update(projections=[{'table': REF, 'column': 'area'}], group_by=[{'table': REF, 'column': 'area'}],
               aggregates=[{'source': {'table': REF, 'column': 'wip_lots'}, 'function': 'avg', 'alias': 'mean_wip'}],
               order_by=[{'output': 'mean_wip', 'direction': 'desc'}], result_limit=3)
    checked = validate_plan(raw, context())
    for ordering in ('average_wip DESC', 'AVG(wip_lots) DESC', '2 DESC'):
        validate_sql_plan(f'SELECT area, AVG(wip_lots) AS average_wip FROM {REF} GROUP BY area ORDER BY {ordering} LIMIT 3', checked)


def test_model_schema_requires_new_fields_but_saved_plans_remain_compatible():
    from app.sub_agent.semantic_plan import SemanticPlan, request_requirements
    assert validate_plan(plan(), context()).order_by == []
    schema = SemanticPlan.strict_response_schema()
    assert set(schema['required']) == set(schema['properties'])
    assert 'default' not in schema['properties']['result_limit']
    for question in ('상위 3 영역', '상위 3개', 'top 3 areas', 'WIP가 가장 큰 영역 3개'):
        assert request_requirements(question)['result_limit'] == 3
    for question in ('상위 10%', '상위 10 퍼센트', 'top 3.5 percent'):
        assert 'result_limit' not in request_requirements(question)
    raw = plan()
    ctx = context()
    ctx['request_requirements'] = {'result_limit': 3}
    with pytest.raises(ValueError, match='specific top-N'):
        validate_plan(raw, ctx)


def test_filtered_latest_preserves_exact_entity_predicates_and_no_outer_reference():
    from app.sub_agent.semantic_plan import validate_sql_plan
    raw, ctx = plan(), context()
    ctx['tables'][REF].append('interval_end')
    raw.update(latest_by=[{'table': REF, 'column': 'interval_end'}], latest_scope='filtered',
               filters=[{'source': {'table': REF, 'column': 'area'}, 'operator': 'eq', 'values': ['photo']}])
    checked = validate_plan(raw, ctx)
    good = f"SELECT s.wip_lots FROM {REF} s WHERE s.area='photo' AND s.interval_end=(SELECT MAX(i.interval_end) FROM {REF} i WHERE i.area='photo')"
    validate_sql_plan(good, checked)
    for bad in (good.replace("i.area='photo'", "i.area='etch'"),
                good.replace(" WHERE i.area='photo'", ''), good.replace('MAX(i.interval_end)', 'MAX(s.interval_end)')):
        with pytest.raises(ValueError, match='latest snapshot'):
            validate_sql_plan(bad, checked)
    raw['latest_scope'] = 'global'
    with pytest.raises(ValueError, match='latest snapshot'):
        validate_sql_plan(good, validate_plan(raw, ctx))


def test_count_rows_after_join_counts_joined_rows_without_dropping_join_contract():
    from app.sub_agent.semantic_plan import validate_sql_plan
    raw, ctx = plan(), context()
    other = 'fab11.custom_fab11'
    ctx['tables'][other] = ['area']
    raw.update(tables=[REF,other], projections=[], aggregates=[{
        'source': {'table': REF, 'column': '*'}, 'function': 'count_rows', 'alias': 'n'}],
        joins=[{'left': {'table': REF, 'column': 'area'}, 'right': {'table': other, 'column': 'area'}, 'kind': 'inner'}])
    checked = validate_plan(raw, ctx)
    validate_sql_plan(f'SELECT COUNT(*) FROM {REF} s JOIN {other} t ON s.area=t.area', checked)
    with pytest.raises(ValueError, match='join.*keys'):
        validate_sql_plan(f'SELECT COUNT(*) FROM {REF} s JOIN {other} t ON 1=1', checked)


def test_explicit_scalar_output_keeps_metric_and_moves_timestamp_out_of_result():
    from app.sub_agent.semantic_plan import validate_sql_plan
    raw, ctx = plan(), context()
    ref = {'table': REF, 'column': 'interval_end'}
    ctx['tables'][REF].append('interval_end')
    ctx['request_requirements'] = {'output_column_count': 1}
    raw.update(projections=[ref], latest_by=[ref], aggregates=[{
        'source': {'table': REF, 'column': 'wip_lots'}, 'function': 'sum', 'alias': 'total'}])
    checked = validate_plan(raw, ctx)
    assert checked.projections == []
    assert len(checked.aggregates) == 1
    validate_sql_plan(f'SELECT SUM(wip_lots) FROM {REF} WHERE interval_end=(SELECT MAX(interval_end) FROM {REF})', checked)


def test_declared_latest_allows_empty_duplicate_predicate_but_other_empty_eq_is_invalid():
    raw, ctx = plan(), context()
    ref = {'table': REF, 'column': 'interval_end'}
    ctx['tables'][REF].append('interval_end')
    raw.update(latest_by=[ref], filters=[{'source': ref, 'operator': 'eq', 'values': []}])
    assert not validate_plan(raw, ctx).filters
    raw['latest_by'] = []
    with pytest.raises(ValueError, match='value count'):
        validate_plan(raw, ctx)


def test_unused_cte_scope_does_not_hide_outer_aggregate_and_scalar_max_cte_is_grounded():
    from app.sub_agent.semantic_plan import validate_sql_plan
    raw, ctx = plan(), context()
    ctx['tables'][REF].append('interval_end')
    raw.update(projections=[], aggregates=[{'source': {'table': REF, 'column': 'wip_lots'}, 'function': 'avg', 'alias': 'mean'}],
               latest_by=[{'table': REF, 'column': 'interval_end'}])
    checked = validate_plan(raw, ctx)
    sql = f'WITH latest AS (SELECT MAX(interval_end) AS ts FROM {REF}) SELECT AVG(wip_lots) FROM {REF} WHERE interval_end=(SELECT ts FROM latest)'
    validate_sql_plan(sql, checked)
    with pytest.raises(ValueError, match='latest snapshot'):
        validate_sql_plan(sql.replace('MAX(interval_end)', 'MIN(interval_end)'), checked)
    with pytest.raises(ValueError, match='latest snapshot'):
        validate_sql_plan(sql.replace('SELECT ts FROM latest', 'SELECT ts FROM latest WHERE false'), checked)


def test_count_null_rows_uses_row_count_but_explicit_count_expression_is_preserved():
    from app.sub_agent.semantic_plan import request_requirements
    raw, ctx = plan(), context()
    raw.update(projections=[], aggregates=[{'source': {'table': REF, 'column': 'area'}, 'function': 'count', 'alias': 'n'}],
               filters=[{'source': {'table': REF, 'column': 'area'}, 'operator': 'is_null', 'values': []}])
    ctx['request_requirements'] = request_requirements('area가 NULL인 행 개수만')
    assert validate_plan(raw, ctx).aggregates[0].function == 'count_rows'
    ctx['request_requirements'] = request_requirements('area가 NULL인 행에서 COUNT(area)만')
    assert validate_plan(raw, ctx).aggregates[0].function == 'count'


@pytest.mark.parametrize('extra', [" AND wip_lots > 0", " AND false", " AND (wip_lots > 0 OR wip_lots IS NULL)"])
def test_sql_cannot_silently_narrow_planned_rows(extra):
    from app.sub_agent.semantic_plan import validate_sql_plan
    with pytest.raises(ValueError, match='predicate'):
        validate_sql_plan(f"SELECT wip_lots FROM {REF} WHERE area='etch'{extra}", filtered_plan())


def test_additional_filter_is_accepted_only_when_declared_and_cte_is_checked():
    from app.sub_agent.semantic_plan import validate_sql_plan
    checked = filtered_plan()
    sql = f"WITH x AS (SELECT * FROM {REF} WHERE area='etch' AND wip_lots>0) SELECT wip_lots FROM x"
    with pytest.raises(ValueError, match='unplanned predicates'):
        validate_sql_plan(sql, checked)
    raw = checked.model_dump()
    raw['filters'].append({'source': {'table': REF, 'column': 'wip_lots'}, 'operator': 'gt', 'values': ['0']})
    validate_sql_plan(sql, validate_plan(raw, context()))


def test_model_setting_count_rejects_area_join_fanout_but_accepts_distinct_identity():
    raw, ctx = plan(), context()
    other = 'fab11.toolgroups_fab11'
    ctx['tables'][REF].append('source_row_id')
    ctx['table_details'][REF]['columns'].append({'name': 'source_row_id', 'type': 'bigint'})
    ctx['tables'][other] = ['area']
    ctx['table_details'][REF]['relationships'] = [{
        'target_table_ref': other, 'cardinality': 'many_to_many',
        'keys': [{'source': 'area', 'target': 'area'}]}]
    ctx['request_requirements'] = {'count_model_settings': True}
    raw.update(tables=[REF, other], projections=[], aggregates=[{
        'source': {'table': REF, 'column': '*'}, 'function': 'count_rows', 'alias': 'n'}],
        joins=[{'left': {'table': REF, 'column': 'area'}, 'right': {'table': other, 'column': 'area'}, 'kind': 'inner'}])
    with pytest.raises(ValueError, match='fanout'):
        validate_plan(raw, ctx)
    raw['aggregates'][0].update(function='count_distinct', source={'table': REF, 'column': 'source_row_id'})
    validate_plan(raw, ctx)


@pytest.mark.parametrize('marker', ['LATEST_INTERVAL_END', '${LATEST_INTERVAL_END}', '<latest_interval_end>'])
def test_latest_field_placeholder_only_normalizes_for_declared_latest_source(marker):
    raw, ctx = plan(), context()
    ctx['tables'][REF].append('interval_end')
    ref = {'table': REF, 'column': 'interval_end'}
    raw.update(latest_by=[ref], filters=[{'source': ref, 'operator': 'eq', 'values': [marker]}])
    assert not validate_plan(raw, ctx).filters
    raw['latest_by'] = []
    assert validate_plan(raw, ctx).filters[0].values == [marker]


@pytest.mark.parametrize('expression', ['mean_wip + 1', 'mean_wip * 0', 'COALESCE(mean_wip, 0)'])
def test_derived_aggregate_alias_cannot_hide_undeclared_math(expression):
    from app.sub_agent.semantic_plan import validate_sql_plan
    query = f'WITH a AS (SELECT area, AVG(wip_lots) AS mean_wip FROM {REF} GROUP BY area) SELECT area, {expression} FROM a'
    with pytest.raises(ValueError, match='transforms a planned aggregate'):
        validate_sql_plan(query, aggregate_plan())


def test_matching_setting_count_cannot_drop_relationship_target_to_avoid_fanout():
    from app.sub_agent.semantic_plan import request_requirements
    raw, ctx = plan(), context()
    other = 'fab11.toolgroups_fab11'
    ctx['request_requirements'] = request_requirements('fab11 설비군과 매칭되는 breakdown 모델 설정의 행 수')
    assert ctx['request_requirements']['require_toolgroup_matching']
    ctx['tables'][other] = ['area']
    ctx['table_details'][REF]['relationships'] = [{
        'target_logical_table': 'toolgroups', 'target_table_ref': other,
        'cardinality': 'many_to_many', 'keys': [{'source': 'area', 'target': 'area'}]}]
    with pytest.raises(ValueError, match='requires the metadata relationship target'):
        validate_plan(raw, ctx)
    assert not request_requirements('fab11 breakdown 설정 행 수').get('require_toolgroup_matching')


def test_two_metric_aliases_cannot_swap_meanings_even_when_aggregate_set_matches():
    from app.sub_agent.semantic_plan import validate_sql_plan
    raw, ctx = plan(), context()
    ctx['tables'][REF].append('queue_lots')
    ctx['table_details'][REF]['columns'].append({'name': 'queue_lots', 'type': 'integer'})
    raw.update(projections=[], aggregates=[
        {'source': {'table': REF, 'column': 'wip_lots'}, 'function': 'sum', 'alias': 'wip_total'},
        {'source': {'table': REF, 'column': 'queue_lots'}, 'function': 'sum', 'alias': 'queue_total'}])
    checked = validate_plan(raw, ctx)
    validate_sql_plan(f'SELECT SUM(wip_lots) AS wip_total, SUM(queue_lots) AS queue_total FROM {REF}', checked)
    for sql in (
        f'SELECT SUM(wip_lots) AS queue_total, SUM(queue_lots) AS wip_total FROM {REF}',
        f'WITH x AS (SELECT SUM(wip_lots) AS w, SUM(queue_lots) AS q FROM {REF}) SELECT w AS queue_total, q AS wip_total FROM x',
    ):
        with pytest.raises(ValueError, match='alias labels a different planned metric'):
            validate_sql_plan(sql, checked)


@pytest.mark.parametrize('question, operator', [
    ('area가 NULL이 아닌 행 개수만', 'not_null'),
    ('area가 NULL인 행 개수만', 'is_null'),
    ('How many rows have area IS NOT NULL?', 'not_null'),
])
def test_explicit_null_row_count_filter_is_grounded_before_sql(question, operator):
    from app.sub_agent.semantic_plan import request_requirements
    raw, ctx = plan(), context()
    ctx['request_requirements'] = request_requirements(question)
    checked = validate_plan(raw, ctx)
    assert [(p.source.column, p.operator) for p in checked.filters] == [('area', operator)]
    raw['filters'] = [{'source': {'table': REF, 'column': 'area'},
                       'operator': 'is_null' if operator == 'not_null' else 'not_null', 'values': []}]
    with pytest.raises(ValueError, match='contradicts'):
        validate_plan(raw, ctx)


def test_quoted_null_string_and_excluded_null_rows_are_not_inferred_as_null_filter():
    from app.sub_agent.semantic_plan import request_requirements
    for question in ("area가 'NULL'인 행 개수", 'area가 NULL인 행을 제외한 행 개수'):
        assert not request_requirements(question).get('explicit_null_filters')


def test_unspecified_snapshot_range_uses_metadata_default_but_explicit_start_is_preserved():
    from app.sub_agent.semantic_plan import request_requirements
    raw, ctx = plan(), context()
    ctx['tables'][REF] += ['interval_start', 'interval_end']
    ctx['table_details'][REF]['semantics']['default_time_column'] = 'interval_end'
    raw['filters'] = [{'source': {'table': REF, 'column': 'interval_start'}, 'operator': 'gte',
                       'values': ['2026-09-07 00:00:00+00']}]
    ctx['request_requirements'] = request_requirements('2026-09-07 00:00 UTC 이상 구간의 WIP 평균')
    with pytest.raises(ValueError, match='mixes timestamp bases'):
        validate_plan(raw, ctx)
    for question in ('interval_start가 2026-09-07 00:00 UTC 이상',
                     '시작 시각이 2026-09-07 00:00 UTC 이상',
                     'interval_start는 2026-09-07 00:00 UTC 이상이고 interval_end는 06:00 미만'):
        ctx['request_requirements'] = request_requirements(question)
        assert validate_plan(raw, ctx).filters[0].source.column == 'interval_start'
    ctx['table_details'][REF]['semantics']['default_time_column'] = 'interval_start'
    ctx['request_requirements'] = request_requirements('2026-09-07 00:00 UTC 이상 구간의 WIP 평균')
    validate_plan(raw, ctx)
