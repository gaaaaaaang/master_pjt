from scripts.evaluate_text2sql_semantics import rows_match


def test_single_measure_aliases_and_unordered_rows_do_not_change_answer():
    assert rows_match([{'area': 'etch', 'm': 1.5}, {'area': 'photo', 'm': 2}],
                      [{'area': 'photo', 'mean': 2}, {'area': 'etch', 'mean': 1.5}])


def test_order_multiplicity_and_wrong_values_are_detected():
    expected = [{'area': 'etch', 'wip': 200}, {'area': 'photo', 'wip': 100}]
    assert not rows_match(list(reversed(expected)), expected, ordered=True)
    assert not rows_match(expected + [expected[0]], expected)
    assert not rows_match([{'area': 'etch', 'wip': 201}, expected[1]], expected)
    assert not rows_match([{'count': None}], [{'count': 0}])
    assert not rows_match([{'flag': True}], [{'flag': 1}])
    assert rows_match([], [])


def test_only_explicit_context_columns_may_be_omitted_from_comparison():
    from scripts.evaluate_text2sql_semantics import compare_case_rows
    expected = [{'area': 'etch', 'wip': 200}, {'area': 'photo', 'wip': 100}]
    actual = [{**row, 'interval_end': '2026-09-07T12:00:00Z'} for row in expected]
    case = {'ordered': True, 'allowed_context_columns': ['interval_end', 'wip']}
    result = compare_case_rows(case, actual, expected)
    assert result['result_matches_reference']
    assert not result['strict_result_matches_reference']
    assert result['ignored_context_columns'] == ['interval_end']
    assert not compare_case_rows({}, actual, expected)['result_matches_reference']
    assert not compare_case_rows(case, list(reversed(actual)), expected)['result_matches_reference']
    actual[0]['wip'] = 201
    assert not compare_case_rows(case, actual, expected)['result_matches_reference']
    actual[0]['wip'] = 200
    actual[0]['invented_extra_measure'] = 999
    assert not compare_case_rows(case, actual, expected)['result_matches_reference']


def test_explicit_output_order_prevents_swapping_multiple_numeric_roles():
    expected = [{'wip_total': 300, 'queue_total': 100}]
    assert rows_match([{'a': 300, 'b': 100}], expected, ordered_columns=True)
    assert not rows_match([{'a': 100, 'b': 300}], expected, ordered_columns=True)


def test_report_separates_abstentions_and_handles_database_values_in_failures():
    from decimal import Decimal
    from scripts.evaluate_text2sql_semantics import summarize
    summary = summarize([
        {'id': 'query', 'answerable': True, 'passed': False, 'reference_rows': [{'avg': Decimal('1.2')}]},
        {'id': 'missing', 'answerable': False, 'passed': True},
        {'id': 'limited', 'answerable': True, 'passed': False, 'error': 'rate_limit_tpm'}])
    assert summary['answerable'] == {'cases': 2, 'passed': 0}
    assert summary['negative'] == {'cases': 1, 'passed': 1}
    assert summary['rate_limited_ids'] == ['limited']
