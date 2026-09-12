"""Compare live Text2SQL outputs with separately authored SQL oracles.

Column aliases are ignored while row multiplicity, dimensions, values, and
explicitly requested row/column ordering are checked.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
from dataclasses import asdict
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT / 'src'))
from app.db.read_only import ReadOnlyQueryExecutor
from app.sub_agent.text2sql import OpenAIText2SQLClient, answer_question


def canonical(value):
    if isinstance(value, bool) or value is None:
        return ('bool_null', str(value))
    if isinstance(value, (int, float, Decimal)):
        return ('number', float(value))
    if isinstance(value, (date, datetime)):
        return ('time', value.isoformat())
    return ('text', str(value))


def rows_match(actual, expected, *, ordered=False, ordered_columns=False, tolerance=1e-6):
    def normalize(rows):
        values = [tuple(canonical(v) for v in row.values()) if ordered_columns else
                  tuple(sorted((canonical(v) for v in row.values()), key=lambda x: (x[0], str(x[1])))) for row in rows]
        return values if ordered else sorted(values, key=repr)

    left, right = normalize(actual), normalize(expected)
    if len(left) != len(right):
        return False
    for a, b in zip(left, right):
        if len(a) != len(b):
            return False
        for av, bv in zip(a, b):
            if av[0] != bv[0]:
                return False
            if av[0] == 'number':
                if not math.isclose(av[1], bv[1], rel_tol=tolerance, abs_tol=tolerance):
                    return False
            elif av != bv:
                return False
    return True


def compare_case_rows(case, actual, expected):
    reference_columns = {key for row in expected for key in row}
    allowed_context = set(case.get('allowed_context_columns', [])) - reference_columns
    ignored = sorted({key for row in actual for key in row} & allowed_context)
    projected = [{key: value for key, value in row.items() if key not in allowed_context} for row in actual]
    return {
        'strict_result_matches_reference': rows_match(actual, expected, ordered=case.get('ordered', False),
                                                      ordered_columns=case.get('ordered_columns', False)),
        'result_matches_reference': rows_match(projected, expected, ordered=case.get('ordered', False),
                                               ordered_columns=case.get('ordered_columns', False)),
        'ignored_context_columns': ignored,
    }


def run_case(case):
    executor = ReadOnlyQueryExecutor()
    answerable = bool(case.get('reference_sql'))
    before = executor.execute(case['reference_sql']).rows if answerable else []
    calls = []
    original = OpenAIText2SQLClient._complete_payload

    def measured(client, payload):
        started = time.monotonic()
        try:
            return original(client, payload)
        finally:
            calls.append({'schema': payload['response_format']['json_schema']['name'],
                          'seconds': round(time.monotonic() - started, 3),
                          'input_characters': len(json.dumps(payload['messages'], ensure_ascii=False))})

    started = time.monotonic()
    with patch.object(OpenAIText2SQLClient, '_complete_payload', measured):
        result = answer_question(case['question'], execute=True, **case.get('context', {}))
    after = executor.execute(case['reference_sql']).rows if answerable else []
    stable = rows_match(before, after, ordered=case.get('ordered', False),
                        ordered_columns=case.get('ordered_columns', False))
    comparison = compare_case_rows(case, result.rows, after)
    correct_fab = not case.get('expected_fab') or bool(result.plan and result.plan.fab_id == case['expected_fab'])
    passed = (stable and result.status == 'succeeded' and comparison['result_matches_reference'] and correct_fab
              if answerable else result.status in case['expected_statuses'] and not result.sql and not result.rows)
    return {'id': case['id'], 'question': case['question'], 'category': case['category'],
            'reference_sql': case.get('reference_sql'), 'reference_rows': after,
            'answerable': answerable, 'correct_fab': correct_fab,
            'reference_stable': stable, **comparison,
            'passed': passed,
            'seconds': round(time.monotonic() - started, 3), 'llm_calls': calls,
            'result': asdict(result)}


def summarize(results):
    """Keep abstentions and infrastructure failures separate from successful queries."""
    summary = {}
    for name, answerable in [('answerable', True), ('negative', False)]:
        rows = [row for row in results if row.get('answerable') is answerable]
        summary[name] = {'cases': len(rows), 'passed': sum(row['passed'] for row in rows)}
    summary['rate_limited_ids'] = [row['id'] for row in results if not row['passed']
                                  and any(term in json.dumps(row, default=str).lower()
                                          for term in ('rate_limit_tpm', 'rate_limit_exceeded', 'http 429'))]
    summary['unstable_reference_ids'] = [row['id'] for row in results if row.get('reference_stable') is False]
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture', type=Path, default=APP_ROOT / 'tests/fixtures/text2sql_semantic_regression.json')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--ids', nargs='*')
    parser.add_argument('--run-label', default='development_semantic_regression_not_holdout')
    parser.add_argument('--pause-seconds', type=float, default=0)
    args = parser.parse_args()
    cases = json.loads(args.fixture.read_text())
    if args.ids:
        cases = [case for case in cases if case['id'] in args.ids]
    if not cases:
        raise SystemExit('No cases selected')
    results = []
    source_hashes = {str(path.relative_to(APP_ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                     for path in sorted((APP_ROOT / 'src').rglob('*.py'))}
    started_at = datetime.now().astimezone().isoformat()
    evaluator_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    for case in cases:
        if results and args.pause_seconds > 0:
            time.sleep(min(args.pause_seconds, 60))
        try:
            result = run_case(case)
        except Exception as exc:  # noqa: BLE001 - record each case failure and continue the evaluation
            result = {'id': case['id'], 'answerable': bool(case.get('reference_sql')),
                      'passed': False, 'error': f'{type(exc).__name__}: {exc}'}
        results.append(result)
        report = {'kind': args.run_label, 'started_at': started_at, 'source_sha256': source_hashes,
                  'evaluator_sha256': evaluator_hash, 'planned_cases': len(cases),
                  'completed': len(results) == len(cases),
                  'fixture_sha256': hashlib.sha256(args.fixture.read_bytes()).hexdigest(),
                  'summary': summarize(results),
                  'cases': len(results), 'passed': sum(row['passed'] for row in results), 'results': results}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_suffix(args.output.suffix + '.tmp')
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        temporary.replace(args.output)
        print(json.dumps({k: result.get(k) for k in ('id', 'passed', 'seconds', 'error')}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
