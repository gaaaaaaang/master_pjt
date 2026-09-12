"""Independently audit saved live FAB comparison results against captured raw rows."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from zoneinfo import ZoneInfo


def dt(value):
    return datetime.fromisoformat(str(value))


def near(left, right):
    return abs(Decimal(str(left)) - Decimal(str(right))) < Decimal('0.0000001')


def audit(case, raw):
    result = case.get('response') or {}
    actual = (result.get('query_result') or {}).get('rows') or []
    case_id = case['id']
    fabs = case.get('fabs', [])
    if case_id == 'fab12_impact':
        source = raw['fabs']['fab12']['snapshots']
        latest = max(dt(row['interval_end']) for row in source)
        baseline = next(row for row in source if row['area'] == 'etch' and dt(row['interval_end']) == latest)
        utilization = Decimal(str(baseline['utilization_percent']))
        completed = Decimal(str(baseline['lot_completions']))
        projected = utilization - 5
        delta = (projected / utilization - 1) * 100
        expected = {'baseline_util_percent': utilization, 'projected_util_percent': projected,
                    'capacity_delta_percent': delta, 'estimated_capacity_change_percent': delta,
                    'estimated_lotcomps_delta': completed * delta / 100,
                    'projected_lotcomps': completed * projected / utilization}
        estimates = next((item['metadata'].get('estimates', {}) for item in result.get('evidence', []) if item['source_type'] == 'impact_calculation'), {})
        errors = [key for key, value in expected.items() if key not in estimates or not near(value.quantize(Decimal('0.001'), rounding=ROUND_HALF_UP), estimates[key])]
        return {'id': case_id, 'audited': True, 'passed': not errors, 'checked_cells': len(expected), 'errors': errors,
                'scope': 'Arithmetic under the stated proportional-capacity assumption, not a validated causal prediction.'}
    if case_id in {'fab12_trend', 'fab12_rank', 'fab13_diagnosis'}:
        fab = 'fab13' if case_id == 'fab13_diagnosis' else 'fab12'
        rows = raw['fabs'][fab]['snapshots']
        latest = max(dt(row['interval_end']) for row in rows)
        groups = defaultdict(list)
        for row in rows:
            when = dt(row['interval_end']).astimezone(ZoneInfo('Asia/Seoul'))
            if case_id == 'fab13_diagnosis':
                include = row['area'] == 'photo' and latest - timedelta(hours=168) < when <= latest
            else:
                include = latest.astimezone(ZoneInfo('Asia/Seoul')).date() - timedelta(days=6) <= when.date() <= latest.astimezone(ZoneInfo('Asia/Seoul')).date()
            if include:
                key = (row['area'],) if case_id == 'fab12_rank' else (when.date().isoformat(), row['area'])
                groups[key].append(row)
        means = ['wip_lots', 'avg_queue_minutes', 'utilization_percent', 'bottleneck_score'] if case_id == 'fab13_diagnosis' else ['yield_percent']
        sums = ['down_minutes', 'pm_minutes', 'lot_completions'] if case_id == 'fab13_diagnosis' else []
        expected = {key: {**{metric: sum(Decimal(str(row[metric])) for row in group)/len(group) for metric in means},
                          **{metric: sum(Decimal(str(row[metric])) for row in group) for metric in sums}}
                    for key, group in groups.items()}
        if case_id == 'fab12_rank':
            lowest = min(expected, key=lambda key: (expected[key]['yield_percent'], key))
            expected = {lowest: expected[lowest]}
        mapped = {(row['area'],) if case_id == 'fab12_rank' else (dt(row['observed_at']).date().isoformat(), row['area']): row for row in actual}
        errors = []
        if set(mapped) != set(expected):
            errors.append('date/process scope mismatch')
        checked = 0
        for key, values in expected.items():
            for metric, value in values.items():
                checked += 1
                if key not in mapped or metric not in mapped[key] or not near(value, mapped[key][metric]):
                    errors.append(f'{key} {metric}: expected={value} actual={mapped.get(key, {}).get(metric)}')
        return {'id': case_id, 'audited': True, 'passed': not errors, 'rows': len(expected), 'checked_cells': checked, 'errors': errors}
    if case_id not in {'fab11_wip', 'fab13_wip', 'screenshot_comparison', 'implicit_comparison', 'total_trend', 'fab10_wip', 'fab12_status', 'fab12_followup'}:
        return {'id': case_id, 'audited': False, 'reason': 'This independent oracle covers current metrics and cross-FAB current/hourly comparison.'}
    by_fab = {fab: raw['fabs'][fab]['snapshots'] for fab in fabs}
    common_times = set.intersection(*[{dt(row['interval_end']) for row in rows} for rows in by_fab.values()])
    latest = max(common_times)
    current = {fab: [row for row in rows if dt(row['interval_end']) == latest] for fab, rows in by_fab.items()}
    expected = []
    keys = []
    if case_id in {'fab11_wip', 'fab13_wip', 'fab10_wip'}:
        expected = [{'wip_lots': sum(Decimal(str(row['wip_lots'])) for row in current[fabs[0]]), 'interval_end': latest}]
    elif case_id in {'fab12_status', 'fab12_followup'}:
        fields = ['yield_percent', 'utilization_percent'] if case_id == 'fab12_status' else ['wip_lots']
        expected = [{key: row[key] for key in ['area', 'interval_end', *fields]} for row in current['fab12'] if row['area'] == 'etch']
        keys = ['area']
    elif case_id in {'screenshot_comparison', 'implicit_comparison'}:
        fields = ['wip_lots', 'avg_queue_minutes', 'utilization_percent', 'down_minutes', 'pm_minutes', 'lot_completions'] if case_id == 'screenshot_comparison' else ['wip_lots']
        expected = [{'fab': fab.upper(), **{key: row[key] for key in ['area', 'interval_end', *fields]}} for fab, rows in current.items() for row in rows]
        keys = ['fab', 'area']
    else:
        keys = ['fab', 'observed_at']
        for fab, rows in by_fab.items():
            by_interval = defaultdict(list)
            for row in rows:
                when = dt(row['interval_end'])
                if latest - timedelta(hours=24) < when <= latest:
                    by_interval[when].append(row)
            hourly = defaultdict(list)
            required_areas = {row["area"] for row in rows}
            for when, observations in by_interval.items():
                if {row["area"] for row in observations} != required_areas:
                    continue
                hourly[when.astimezone(ZoneInfo("Asia/Seoul")).replace(minute=0, second=0, microsecond=0, tzinfo=None)].append((when, sum(Decimal(str(row['wip_lots'])) for row in observations)))
            for bucket, values in hourly.items():
                expected.append({'fab': fab.upper(), 'observed_at': bucket, 'wip_lots': sum(value for _, value in values) / len(values),
                                 'observation_count': len(values), 'first_observed_at': min(when for when, _ in values), 'last_observed_at': max(when for when, _ in values)})

    def row_key(row):
        return tuple(dt(row[key]).isoformat() if key == 'observed_at' else row[key] for key in keys)

    errors = []
    if len(actual) != len(expected):
        errors.append(f'row_count expected={len(expected)} actual={len(actual)}')
    mapping = {row_key(row): row for row in actual}
    checked = 0
    for row in expected:
        target = mapping.get(row_key(row))
        if target is None:
            errors.append('missing row ' + str(row_key(row)))
            continue
        for key, value in row.items():
            observed = target.get(key)
            okay = observed is not None and (dt(value) == dt(observed) if key in {'observed_at', 'interval_end', 'first_observed_at', 'last_observed_at'} else value == observed if key in {'fab', 'area'} else near(value, observed))
            checked += 1
            if not okay:
                errors.append(f'{row_key(row)} {key}: expected={value} actual={observed}')
    return {'id': case_id, 'audited': True, 'passed': not errors, 'rows': len(expected), 'checked_cells': checked, 'errors': errors}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--raw', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    raw = json.loads(args.raw.read_text())
    report = json.loads(args.report.read_text())
    checks = [audit(case, raw) for case in report['results']]
    output = {'raw_sha256': hashlib.sha256(args.raw.read_bytes()).hexdigest(), 'report_sha256': hashlib.sha256(args.report.read_bytes()).hexdigest(),
              'scope': 'Independent arithmetic and scope checks; not complete narrative accuracy.', 'checks': checks,
              'audited': sum(item['audited'] for item in checks), 'passed': sum(item.get('passed', False) for item in checks)}
    args.report.with_name(args.report.stem + '_value_audit.json').write_text(json.dumps(output, ensure_ascii=False, indent=2))
    print(json.dumps({key: value for key, value in output.items() if key != "checks"}, ensure_ascii=False))
    for item in checks:
        if item.get("errors"):
            print(item["id"], item["errors"][:10])


if __name__ == '__main__':
    main()
