"""Build a reproducible cross-FAB query grid from explicit metric contracts.

This fixture generator imports no query-builder code. Its output can be checked
by evaluate_snapshot_variants.py with all model and HTTP calls disabled.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

# Column, natural-language label, period operator. WIP is a stock; completed
# lots and durations are interval flows. The period mean cases override this
# operator explicitly and remain averages per observation, never hourly rates.
CONTRACTS = (
    ("wip_lots", "재공", "평균"),
    ("queue_lots", "대기 LOT", "평균"),
    ("avg_queue_minutes", "대기 시간", "평균"),
    ("avg_cycle_hours", "Cycle Time", "평균"),
    ("yield_percent", "수율", "평균"),
    ("utilization_percent", "가동률", "평균"),
    ("lot_completions", "완료 LOT", "합계"),
    ("lot_starts", "투입량", "합계"),
    ("down_minutes", "downtime", "합계"),
    ("pm_minutes", "정비 시간", "합계"),
    ("bottleneck_score", "bottleneck_score", "평균"),
    ("temperature_c", "온도", "평균"),
    ("humidity_percent", "습도", "평균"),
    ("defect_ppm", "불량 ppm", "평균"),
)


def build_cases():
    cases = []
    for number in range(10, 14):
        fab = f"fab{number}"
        for metric, label, operator in CONTRACTS:
            contexts = (
                ("current_total", f"현재 전체 {label}", "current", [], {}),
                ("current_areas", f"현재 공정별 {label}", "current", "all", {}),
                ("current_etch", f"현재 etch {label}", "current", ["etch"], {}),
                ("period", f"최근 3일 공정별 {label} {operator}",
                 "period_sum" if operator == "합계" else "period_mean", "all", {"days":3}),
                ("mean", f"최근 3일 공정별 {label} 평균", "period_mean", "all", {"days":3}),
                ("rank", f"현재 공정별 {label} 상위 3개", "rank", "all", {"limit":3, "direction":"desc"}),
            )
            for name, question, kind, areas, extra in contexts:
                # Non-flow period means are already covered by the period case.
                if name == "mean" and operator != "합계":
                    continue
                cases.append({"id":f"{fab}_{metric}_{name}", "question":f"{fab.upper()} {question}",
                              "kind":kind, "fab":fab, "metrics":[metric], "areas":areas, **extra})
    return cases


def build_mixed_cases():
    cases = []
    for number in range(10, 14):
        fab = f"fab{number}"
        current = (
            ("WIP과 util, cycleavg", ["wip_lots", "utilization_percent", "avg_cycle_hours"]),
            ("queue_time과 avg_cycle_hours, utilization_percent", ["avg_queue_minutes", "avg_cycle_hours", "utilization_percent"]),
            ("WIP과 PM 시간, 비가동 시간", ["wip_lots", "pm_minutes", "down_minutes"]),
        )
        for index, (question, metrics) in enumerate(current):
            cases.append({"id":f"{fab}_aliases_{index}", "question":f"{fab.upper()} 현재 공정별 {question}",
                          "kind":"current", "fab":fab, "metrics":metrics, "areas":"all"})
        periods = (
            ("완료 LOT 평균과 정비 시간 합계", "lot_completions", "pm_minutes"),
            ("완료 LOT 합계와 투입량 평균", "lot_starts", "lot_completions"),
            ("평균 정비 시간과 downtime 합계", "pm_minutes", "down_minutes"),
            ("투입 LOT 평균과 비가동 시간 합계", "lot_starts", "down_minutes"),
        )
        for index, (question, mean_metric, sum_metric) in enumerate(periods):
            cases.append({"id":f"{fab}_mixed_flow_{index}", "question":f"{fab.upper()} 최근 3일 공정별 {question}",
                          "kind":"period_mixed", "fab":fab, "metrics":[mean_metric, sum_metric],
                          "areas":"all", "days":3, "aggregations":{mean_metric:"mean", sum_metric:"sum"}})
    return cases


def build_temporal_cases():
    cases = []
    for number in range(10, 14):
        fab = f"fab{number}"
        for metric, label, operator in CONTRACTS:
            for scope, areas in (("etch", ["etch"]), ("전체", [])):
                cases.append({"id":f"{fab}_{metric}_hourly_{scope}",
                              "question":f"{fab.upper()} {scope} 최근 24시간 {label} 시간별 추세",
                              "kind":"hourly", "fab":fab, "metrics":[metric], "areas":areas, "hours":24})
            cases.append({"id":f"{fab}_{metric}_daily", "question":f"{fab.upper()} 최근 3일 공정별 {label} 일별 추세",
                          "kind":"daily", "fab":fab, "metrics":[metric], "areas":"all", "days":3})
            cases.append({"id":f"{fab}_{metric}_period_rank", "question":f"{fab.upper()} 최근 3일 공정별 {label} {operator} 상위 3개",
                          "kind":"period_rank", "fab":fab, "metrics":[metric], "areas":"all", "days":3,
                          "limit":3, "direction":"desc", "aggregations":{metric:"sum" if operator == "합계" else "mean"}})
    return cases


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--mixed-metrics", action="store_true", help="Check shorthand aliases and different per-metric period operators")
    mode.add_argument("--temporal-metrics", action="store_true", help="Check hourly/daily series and period ranks across every metric")
    args = parser.parse_args()
    cases = build_mixed_cases() if args.mixed_metrics else build_temporal_cases() if args.temporal_metrics else build_cases()
    args.output.write_text(json.dumps(cases, ensure_ascii=False, indent=2) + "\n")
    print(f"Wrote {len(cases)} explicit contract cases to {args.output}")


if __name__ == "__main__":
    main()
