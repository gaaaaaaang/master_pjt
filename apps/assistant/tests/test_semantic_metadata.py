from app.db.semantic_metadata import definition, enrich


def columns(*names):
    return [{"name": name, "type": "numeric", "description": ""} for name in names]


def test_snapshot_counts_are_not_summed_across_time_and_ratios_have_no_fake_denominator():
    result = definition("live_process_snapshots", columns("wip_lots", "yield_percent", "interval_end"))
    metrics = {metric["id"]: metric for metric in result["metrics"]}
    assert set(metrics) == {"wip_lots", "yield_percent"}
    assert "서로 다른 시점은 SUM하지" in metrics["wip_lots"]["aggregation_rule"]
    assert "분모는 제공하지 않음" in metrics["yield_percent"]["definition"]
    assert result["semantics"]["review_status"] == "code_grounded"
    assert any("동일 키가 아니다" in note for note in result["semantics"]["notes"])


def test_variant_never_inherits_unavailable_metric_and_enrichment_does_not_mutate_input():
    source = {"logical_table": "autosched_part", "description": "autosched part",
              "columns": columns("wiplotavg"),
              "metrics": [{"column": "wiplotavg"}, {"column": "wiplotcur"}]}
    result = enrich(source)
    assert result["metrics"] == [{"column": "wiplotavg"}]
    assert result["columns"][0]["description"]
    assert source["columns"][0]["description"] == ""
    assert len(source["metrics"]) == 2


def test_unknown_table_remains_accessible_without_manufactured_meaning():
    result = enrich({"logical_table": "new_measurements", "columns": columns("custom_value")})
    assert result["semantics"]["review_status"] == "unreviewed"
    assert result["semantics"]["grain"] == "unknown"
    assert result["metrics"] == []
    assert result["semantics"]["trust_weight"] == 1


def test_editorial_description_and_column_comments_are_preserved():
    source = {"logical_table": "toolgroups", "description": "업무 담당자가 검토한 설명",
              "columns": [{"name": "number_of_tools", "description": "검토한 컬럼 설명"}],
              "semantics": {"managed_by": "editor", "review_status": "reviewed"}}
    result = enrich(source)
    assert result["description"] == source["description"]
    assert result["columns"] == source["columns"]
    assert result["semantics"] == source["semantics"]


def test_editorial_column_meanings_reach_grounding_without_inventing_schema():
    source = {'logical_table': 'live_process_snapshots', 'columns': columns('wip_lots', 'area'),
              'semantics': {'managed_by': 'editor', 'column_meanings': {
                  'wip_lots': {'description': '검토된 snapshot 시점의 영역별 재공 롯 수'},
                  'nonexistent': {'description': '이 컬럼은 현재 FAB에 없음'}}}}
    result = enrich(source)
    assert result['columns'][0]['description'] == '검토된 snapshot 시점의 영역별 재공 롯 수'
    assert [c['name'] for c in result['columns']] == ['wip_lots', 'area']
    assert result['columns'][0]['type'] == 'numeric'
    assert source['columns'][0]['description'] == ''
    source['columns'][0]['description'] = '현재 PostgreSQL comment'
    assert enrich(source)['columns'][0]['description'] == '현재 PostgreSQL comment'


def test_column_names_alone_do_not_assign_simulation_or_report_meanings():
    unknown = definition('actual_measurements', columns('wip_lots', 'wiplotavg', 'number_of_tools'))
    assert unknown['metrics'] == []
    assert unknown['semantics']['column_meanings'] == {}
    model = definition('toolgroups', columns('number_of_tools', 'wip_lots'))
    assert [m['column'] for m in model['metrics']] == ['number_of_tools']
    report = definition('autosched_part', columns('wiplotavg', 'wip_lots'))
    assert [m['column'] for m in report['metrics']] == ['wiplotavg']


def test_editor_owned_empty_metrics_stay_empty_when_loaded_for_grounding():
    source = {'logical_table': 'toolgroups', 'columns': columns('number_of_tools'),
              'metrics': [], 'semantics': {'managed_by': 'editor'}}
    assert enrich(source)['metrics'] == []
    source['semantics']['managed_by'] = 'code'
    assert enrich(source)['metrics'][0]['column'] == 'number_of_tools'


def test_breakdown_keys_are_areas_and_snapshot_has_explicit_default_time_basis():
    row = definition('breakdown', columns('type_name'))
    relationship = row['relationships'][0]
    assert relationship['keys'] == [{'source': 'type_name', 'target': 'area'}]
    assert relationship['cardinality'] == 'many_to_many'
    assert 'COUNT(DISTINCT' in relationship['aggregation_rule']
    assert definition('live_process_snapshots', columns('interval_end'))['semantics']['default_time_column'] == 'interval_end'
