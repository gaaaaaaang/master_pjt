from copy import deepcopy

import pytest
from app.agents.intent import analyze_request, enrich_analysis
from app.sub_agent.business_query import entity_literals, record_requirements
from app.sub_agent.result_delivery import effective_row_limit, query_result_payload
from app.sub_agent.semantic_plan import SemanticPlan, validate_plan, validate_sql_plan
from app.sub_agent.semantic_sql import compile_single_table
from app.sub_agent.text2sql import Text2SQLResult, extract_query_slots, plan_text2sql


@pytest.mark.parametrize('question,expected', [
    ('LOT AB-991의 제한 이력과 설정자', {'lot_id': 'AB-991'}),
    ('장비 EQ.X_004 현재 상태', {'equipment_id': 'EQ.X_004'}),
    ('FAB10_ETCH_TG01_EQ03 장비의 현재 비가동 사유', {'equipment_id': 'FAB10_ETCH_TG01_EQ03'}),
    ('자원 그룹 GRP_BLUE 내 빈 자원', {'resource_group': 'GRP_BLUE'}),
    ('FAB10-IM-09121559-015의 공정 진행 제한 이력', {'lot_id': 'FAB10-IM-09121559-015'}),
    ('fab10 Dry_Etch toolgroup 목록', {}),
    ('equipment utilization in fab10', {}),
])
def test_opaque_ids_and_non_entity_words(question, expected):
    assert entity_literals(question) == expected


def test_equipment_is_not_truncated_to_group():
    slots = extract_query_slots('FAB10_ETCH_TG01_EQ03 장비의 현재 상태', fab='fab10')
    assert slots['equipment_id'].value == 'FAB10_ETCH_TG01_EQ03'
    assert 'toolgroup' not in slots


def context_and_plan():
    table = 'fab10.event_history_fab10'
    columns = ['lot_id', 'event_time', 'event_type', 'actor_role']
    request = record_requirements('LOT AB-991의 제한 이력과 설정자')
    ctx = {'tables': {table: columns}, 'request_requirements': {'business_request': request}}
    ref = lambda name: {'table': table, 'column': name}
    plan = {'supported': True, 'reason': 'Available events, no personal setter',
            'tables': [table], 'projections': [ref(c) for c in columns],
            'aggregates': [], 'group_by': [], 'joins': [], 'latest_by': [],
            'filters': [{'source': ref('lot_id'), 'operator': 'eq', 'values': ['AB-991']}],
            'time_basis': 'event_time', 'result_grain': 'event', 'limitations': ['No person ID'],
            'answer_coverage': [
                {'requirement': 'history', 'status': 'available', 'columns': [ref('event_time'), ref('event_type')], 'reason': ''},
                {'requirement': 'setter', 'status': 'unavailable', 'columns': [], 'reason': 'Only actor role, no personal ID'},
            ]}
    return ctx, plan


def test_partial_evidence_compiles_and_preserves_entity_in_sql():
    ctx, raw = context_and_plan()
    plan = validate_plan(raw, ctx)
    sql = compile_single_table(plan)
    assert "'AB-991'" in sql
    validate_sql_plan(sql, plan)
    assert plan.answer_coverage[1].status == 'unavailable'


@pytest.mark.parametrize('change', ['missing_id', 'wrong_id', 'group_instead', 'missing_facet', 'invented_evidence'])
def test_wrong_target_or_unfulfilled_facet_is_rejected(change):
    ctx, plan = context_and_plan()
    if change == 'missing_id': plan['filters'] = []
    if change == 'wrong_id': plan['filters'][0]['values'] = ['AB-992']
    if change == 'group_instead': plan['filters'][0]['source']['column'] = 'toolgroup'
    if change == 'missing_facet': plan['answer_coverage'].pop()
    if change == 'invented_evidence': plan['answer_coverage'][0]['columns'][0]['column'] = 'unknown'
    with pytest.raises(ValueError):
        validate_plan(plan, ctx)


def test_latest_entity_scope_must_not_use_global_maximum():
    ctx, plan = context_and_plan()
    ctx['request_requirements']['business_request']['current'] = True
    plan['latest_by'] = [{'table': plan['tables'][0], 'column': 'event_time'}]
    with pytest.raises(ValueError, match='filtered'):
        validate_plan(plan, ctx)
    plan['latest_scope'] = 'filtered'
    compiled = compile_single_table(validate_plan(plan, ctx))
    assert compiled.count("'AB-991'") == 2


@pytest.mark.parametrize('sql,limit', [
    ('SELECT * FROM x LIMIT 50', 50),
    ('SELECT * FROM x LIMIT 999', 200),
    ('WITH x AS (SELECT * FROM y LIMIT 5) SELECT * FROM x', 200),
    ('SELECT * FROM x', 200),
])
def test_effective_outer_limit(sql, limit):
    assert effective_row_limit(sql, 200) == limit


def test_fifty_rows_are_not_a_complete_population():
    result = Text2SQLResult(status='succeeded', query_type='master_data_lookup', answer='',
                            sql='SELECT * FROM x LIMIT 50', row_count=50)
    payload = query_result_payload(result)
    assert payload['row_limit'] == 50
    assert payload['limit_reached'] is True


def test_business_request_bypasses_irrelevant_report_template():
    class Client:
        def __init__(self):
            self.calls = []
        def create_sql(self, **kwargs):
            self.calls.append(kwargs)
            return {'supported': False, 'answer': 'No registered work-code source'}
    client = Client()
    result = plan_text2sql('fab10 etch 운영 시스템 작업 코드 종류', llm_client=client,
                          query_type='master_data_lookup')
    assert client.calls
    assert result.status == 'unsupported'
    assert result.sql is None


def test_model_can_add_lot_id_without_rewriting_it():
    analysis = analyze_request('fab10 ZX-99의 현재 상태를 확인해줘')
    item = {'name': 'lot_id', 'value': 'ZX-99', 'raw_text': 'ZX-99'}
    assert enrich_analysis(analysis, [item]).slots['lot_id'].value == 'ZX-99'
    changed = deepcopy(item)
    changed['value'] = 'ZX99'
    assert 'lot_id' not in enrich_analysis(analysis, [changed]).slots


@pytest.mark.parametrize('column', ['event_type', 'toolgroup', 'step'])
def test_related_codes_cannot_be_substituted_for_work_codes(column):
    ctx, plan = context_and_plan()
    table = plan['tables'][0]
    ctx['tables'][table].append(column)
    ctx['request_requirements']['business_request'] = record_requirements('등록된 작업 코드 종류')
    plan['answer_coverage'] = [{'requirement': 'work_code', 'status': 'available',
                               'columns': [{'table': table, 'column': column}], 'reason': ''}]
    with pytest.raises(ValueError, match='work codes'):
        validate_plan(plan, ctx)


def test_new_business_source_can_bind_nonstandard_entity_and_code_names():
    table = 'fab10.operations_registry_fab10'
    ctx = {'tables': {table: ['batch_key', 'op_cd']},
           'table_details': {table: {'semantics': {'managed_by': 'editor',
               'entity_columns': {'lot_id': 'batch_key'}, 'facet_columns': {'work_code': ['op_cd']}}}},
           'request_requirements': {'business_request': record_requirements('LOT BX-987 작업 코드')}}
    ref = lambda column: {'table': table, 'column': column}
    plan = {'supported': True, 'reason': 'Registered business mapping', 'tables': [table],
            'projections': [ref('op_cd')], 'aggregates': [], 'group_by': [], 'joins': [],
            'filters': [{'source': ref('batch_key'), 'operator': 'eq', 'values': ['BX-987']}],
            'latest_by': [], 'time_basis': 'registry', 'result_grain': 'work code', 'limitations': [],
            'answer_coverage': [{'requirement': 'work_code', 'status': 'available', 'columns': [ref('op_cd')], 'reason': ''}]}
    sql = compile_single_table(validate_plan(plan, ctx))
    assert 'batch_key' in sql and "'BX-987'" in sql


def test_current_request_schema_cannot_prefilter_a_historical_failure():
    ctx, raw = context_and_plan()
    business = ctx['request_requirements']['business_request']
    business.update(current=True, facets=['latest_record'])
    schema = SemanticPlan.strict_response_schema(ctx)
    assert schema['properties']['latest_scope']['enum'] == ['filtered']
    permitted = schema['$defs']['Predicate']['properties']['source']['properties']['column']['enum']
    assert 'lot_id' in permitted and 'event_type' not in permitted
    raw['answer_coverage'] = [{'requirement': 'latest_record', 'status': 'available',
                             'columns': [raw['projections'][1]], 'reason': ''}]
    raw['filters'].append({'source': raw['projections'][2], 'operator': 'eq', 'values': ['hold_start']})
    with pytest.raises(ValueError, match='latest entity record'):
        validate_plan(raw, ctx)


def test_matching_entity_source_outranks_empty_projection():
    from app.db.schema_retrieval import select_catalog
    def entry(logical, match):
        return {'logical_table': logical, 'columns': [{'name': 'lot_id'}, {'name': 'event_time'}],
                'entity_presence': {'matches': match, 'predicates': {'lot_id': 'AB-991'}}}
    catalog = {'fab10.raw_fab10': entry('raw', True), 'fab10.projection_fab10': entry('projection', False)}
    selected, _ = select_catalog('LOT AB-991 현재 상태', catalog, max_tables=1)
    assert list(selected) == ['fab10.raw_fab10']


def test_partial_answer_status_is_separate_from_query_success():
    from app.sub_agent.text2sql import QueryPlan
    ctx, raw = context_and_plan()
    plan = validate_plan(raw, ctx)
    result = Text2SQLResult(status='succeeded', query_type='status', answer='', rows=[{'actor_role': 'scheduler'}],
                            row_count=1, plan=QueryPlan('status', None, semantic_plan=plan.model_dump()))
    assert query_result_payload(result)['answer_status'] == 'partial'


def test_empty_lookup_does_not_treat_lot_identifier_as_numeric_measurement():
    from app.sub_agent.reflection import verify_response
    question = 'LOT BX-20260913-015 현재 상태'
    evidence = [{'source_type': 'text2sql_plan', 'metadata': {'status': 'succeeded',
        'sql': "SELECT lot_id FROM fab10.events_fab10 WHERE lot_id='BX-20260913-015'",
        'row_count': 0, 'sample_rows': [], 'columns': ['lot_id']}}]
    checked = verify_response('BX-20260913-015 LOT에 해당하는 기록은 조회 결과 0건입니다.',
                              evidence=evidence, query_type='status', question=question)
    assert 'Numeric operational claims require SQL evidence.' not in checked['warnings']
    bad = verify_response('BX-20260913-015 LOT의 WIP는 30개입니다.', evidence=evidence,
                          query_type='status', question=question)
    assert 'Numeric operational claims require SQL evidence.' in bad['warnings']


def union_plan():
    tables = ['fab10.route_a_fab10', 'fab10.route_b_fab10']
    context = {'tables': {table: ['route', 'step', 'area'] for table in tables}}
    raw = {'supported': True, 'reason': 'All route partitions', 'set_operation': 'union_all',
           'tables': tables, 'projections': [{'table': table, 'column': c} for table in tables for c in ['route', 'step', 'area']],
           'filters': [{'source': {'table': table, 'column': 'area'}, 'operator': 'eq', 'values': ['TF_Met']} for table in tables],
           'joins': [], 'aggregates': [], 'group_by': [], 'latest_by': [], 'time_basis': 'model',
           'result_grain': 'route step', 'limitations': [], 'order_by': [{'output': 'route', 'direction': 'asc'}, {'output': 'step', 'direction': 'asc'}],
           'result_limit': 20}
    return validate_plan(raw, context)


def test_union_compiles_all_sources_with_one_final_limit():
    plan = union_plan()
    sql = compile_single_table(plan)
    assert sql.count('UNION ALL') == 1
    assert sql.count('LIMIT') == 1
    assert sql.count("'TF_Met'") == 2
    validate_sql_plan(sql, plan)


@pytest.mark.parametrize('change', ['drop_branch', 'drop_filter', 'truncate_branch', 'distinct', 'wrong_order'])
def test_union_cannot_drop_source_or_filter_or_truncate_a_branch(change):
    plan = union_plan()
    sql = compile_single_table(plan)
    if change == 'drop_branch': sql = sql.split(' UNION ALL ')[0]
    if change == 'drop_filter': sql = sql.replace(" = 'TF_Met'", " = 'Other'", 1)
    if change == 'truncate_branch': sql = sql.replace(" = 'TF_Met')", " = 'TF_Met' LIMIT 1)", 1)
    if change == 'distinct': sql = sql.replace('UNION ALL', 'UNION')
    if change == 'wrong_order': sql = sql.replace('"route" ASC', '"route" DESC')
    with pytest.raises(ValueError):
        validate_sql_plan(sql, plan)


def test_missing_business_field_requires_semantic_claim_review():
    from app.sub_agent.reflection import verify_response
    evidence = [{'source_type': 'text2sql_plan', 'metadata': {'status': 'succeeded',
        'row_count': 1, 'sample_rows': [{'narrative': 'normal hold_start'}],
        'query_plan': {'semantic_plan': {'answer_coverage': [
            {'requirement': 'reason', 'status': 'unavailable'}]}}}}]
    result = verify_response('제한 사유는 normal hold_start입니다.', evidence=evidence,
                             query_type='status', question='제한 사유')
    assert 'business_field_coverage' in result['semantic_review_topics']
    assert any('narrative' in warning for warning in result['semantic_warnings'])
    assert any('falsely attributed' in warning for warning in result['blocking_warnings'])
    good = verify_response('제한 사유는 확인할 수 없습니다. 이벤트 설명은 normal hold_start입니다.',
                           evidence=evidence, query_type='status', question='제한 사유')
    assert not any('falsely attributed' in warning for warning in good['blocking_warnings'])


def test_proven_latest_row_limit_is_not_an_unsupported_measurement():
    from app.sub_agent.reflection import verify_response
    evidence = [{'source_type': 'text2sql_plan', 'metadata': {'status': 'succeeded',
        'row_count': 1, 'row_limit': 1, 'limit_reached': True,
        'sample_rows': [{'state': 'active'}]}}]
    result = verify_response('최신 1건만 조회했습니다(반환 한도 1). 상태 기록은 active입니다.',
                             evidence=evidence, query_type='status', question='장비 현재 상태')
    assert not result['unsupported_numeric_claims']
    assert not result['undisclosed_row_limit']


def test_truncated_query_is_not_a_complete_answer():
    result = Text2SQLResult(status='succeeded', query_type='master_data_lookup', answer='',
                            rows=[{'step': 'A'}], row_count=1, row_limit=1)
    assert query_result_payload(result)['answer_status'] == 'partial'


def test_availability_cannot_invent_idle_event_filter():
    ctx, raw = context_and_plan()
    business = ctx['request_requirements']['business_request']
    business['facets'] = ['availability']
    raw['answer_coverage'] = [{'requirement': 'availability', 'status': 'unavailable',
                             'columns': [], 'reason': 'No availability field'}]
    raw['filters'].append({'source': raw['projections'][2], 'operator': 'eq', 'values': ['idle']})
    schema = SemanticPlan.strict_response_schema(ctx)
    assert 'event_type' not in schema['$defs']['Predicate']['properties']['source']['properties']['column']['enum']
    with pytest.raises(ValueError, match='No source defines availability'):
        validate_plan(raw, ctx)


def test_shared_union_shape_applies_predicates_to_every_partition():
    plan = union_plan()
    raw = plan.model_dump()
    raw['projections'] = [p for p in raw['projections'] if p['table'] == plan.tables[0]]
    raw['filters'] = [p for p in raw['filters'] if p['source']['table'] == plan.tables[0]]
    context = {'tables': {table: ['route', 'step', 'area'] for table in plan.tables}}
    expanded = validate_plan(raw, context)
    assert expanded == plan
    assert compile_single_table(expanded).count("'TF_Met'") == 2
    context['tables'][plan.tables[1]].remove('area')
    with pytest.raises(ValueError, match='columns must exist in every'):
        validate_plan(raw, context)


def test_role_cannot_be_relabelled_setter_with_an_unofficial_disclaimer():
    from app.sub_agent.reflection import verify_response
    evidence = [{'source_type': 'text2sql_plan', 'metadata': {'status': 'succeeded',
        'row_count': 1, 'columns': ['actor_role', 'narrative'],
        'sample_rows': [{'actor_role': 'scheduler', 'narrative': 'normal hold_start'}],
        'query_plan': {'semantic_plan': {'answer_coverage': [
            {'requirement': 'setter', 'status': 'unavailable'}]}}}}]
    result = verify_response('설정자 정보는 actor_role=scheduler로 제공됩니다. 개인 이름은 아닙니다. Hold 사유는 normal hold_start입니다.',
                             evidence=evidence, query_type='status', question='Hold 이력과 누가 설정했는지')
    assert sum('falsely attributed' in warning for warning in result['blocking_warnings']) == 2
    good = verify_response('설정자는 확인할 수 없습니다. 이벤트 기록 역할(actor_role)은 scheduler입니다. 이벤트 설명은 normal hold_start입니다.',
                           evidence=evidence, query_type='status', question='Hold 이력과 누가 설정했는지')
    assert not any('falsely attributed' in warning for warning in good['blocking_warnings'])


def test_empty_record_query_cannot_prove_entity_never_existed():
    from app.sub_agent.reflection import verify_response
    evidence = [{'source_type': 'text2sql_plan', 'metadata': {'status': 'succeeded',
        'row_count': 0, 'sample_rows': [], 'query_plan': {'semantic_plan': {'answer_coverage': [
            {'requirement': 'latest_record', 'status': 'unavailable'}]}}}}]
    bad = verify_response('이 LOT은 실존하지 않는 것으로 확인됩니다.', evidence=evidence,
                          query_type='status', question='LOT 상태')
    assert any('does not prove' in warning for warning in bad['blocking_warnings'])
    good = verify_response('조회한 출처에서 해당 LOT의 기록을 찾지 못했습니다.', evidence=evidence,
                           query_type='status', question='LOT 상태')
    assert not any('does not prove' in warning for warning in good['blocking_warnings'])
