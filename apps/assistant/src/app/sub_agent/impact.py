"""Deterministic, evidence-bounded impact calculations for FAB what-if questions."""

from __future__ import annotations

import math
import re
from collections import Counter
from datetime import timedelta
from statistics import fmean
from typing import Any

_BASELINE_IDENTITY_DIMENSIONS = (
    "fab_id",
    "area",
    "interval_start",
    "interval_end",
    "observed_at",
    "part",
    "product",
    "product_name",
    "route",
    "route_name",
    "stn",
    "stngrp",
    "period",
    "report_time",
    "report_date",
    "release_date",
    "start_date",
    "due_date",
)


def estimate_output_delta(
    baseline: dict[str, Any],
    scenario: dict[str, Any],
) -> dict[str, Any]:
    """Calculate transparent first-order sensitivities without claiming causal precision."""
    question = str(scenario.get("question") or "")
    rows = list(baseline.get("rows") or [])
    baseline_metrics = _numeric_means(rows)
    mixed_dimensions = _mixed_baseline_dimensions(rows)
    changes = _parse_changes(question)
    change = changes[0] if changes else None
    estimates: dict[str, float] = {}
    inputs: dict[str, Any] = {}
    formulae: list[str] = []
    assumptions: list[str] = []
    limitations: list[str] = []

    if mixed_dimensions:
        dimensions = ", ".join(mixed_dimensions)
        limitations.append(
            "baseline에 서로 다른 대상/기간 차원"
            f"({dimensions})이 섞여 있어 단일 영향값으로 평균 계산하지 않았습니다. "
            "대상·기간별 baseline이 필요합니다."
        )
    if not changes:
        limitations.append("질문에서 변화량(%, %p, 시간)을 식별하지 못했습니다.")
    metric_counts = Counter(str(item.get("metric") or "unknown") for item in changes)
    duplicate_metrics = {metric for metric, count in metric_counts.items() if count > 1}
    reported_duplicates: set[str] = set()
    for item in [] if mixed_dimensions else changes:
        metric = str(item.get("metric") or "unknown")
        if metric in duplicate_metrics:
            if metric not in reported_duplicates:
                limitations.append(
                    f"{metric} 변화량이 여러 개라 순차·합성 규칙 없이 계산하지 않았습니다."
                )
                reported_duplicates.add(metric)
            continue
        if item.get("direction") is None:
            limitations.append(
                f"{metric} 변화량의 증가/감소 방향이 없거나 서로 충돌해 계산할 수 없습니다."
            )
            continue
        if metric == "utilization" and item["unit"] in {"percentage_point", "percent"}:
            _estimate_utilization_change(
                item,
                baseline_metrics,
                estimates=estimates,
                inputs=inputs,
                formulae=formulae,
                assumptions=assumptions,
                limitations=limitations,
            )
        elif metric == "cycle_time" and item["unit"] == "percent":
            _estimate_cycle_time_change(
                item,
                baseline_metrics,
                estimates=estimates,
                inputs=inputs,
                formulae=formulae,
                limitations=limitations,
            )
        elif metric == "queue_time" and item["unit"] == "percent":
            limitations.append(
                "Queue Time 변화와 처리량을 연결하는 관계식이 근거 자료에 없어 처리량 영향을 수치로 계산할 수 없습니다. "
                "해당 공정의 대기시간·완료량 이력과 동일 조건의 비교 또는 보정된 예측 모델이 필요합니다."
            )
        elif metric == "down" and item["unit"] == "hour":
            limitations.append(
                "시간당 lot completion rate와 추가 비가동을 적용할 설비·분석 기간이 정해지지 않아 "
                "추가 downtime의 처리량 영향을 계산할 수 없습니다. 기간 합계만으로 시간당 처리량을 가정하지 않습니다."
            )
        else:
            limitations.append(
                f"{metric}의 {item['unit']} 변화와 요청한 결과 지표를 연결하는 계산식이 근거 자료에 없습니다."
            )

    status = "succeeded" if estimates else "data_unavailable"
    return {
        "status": status,
        "summary": _summarize(estimates, inputs, formulae, assumptions, limitations),
        "baseline": {
            "row_count": len(rows),
            "metrics": baseline_metrics,
            "metric_units": _metric_units(baseline_metrics),
            "mixed_dimensions": mixed_dimensions,
        },
        "scenario": {
            **scenario,
            "parsed_change": change,
            "parsed_changes": changes,
        },
        "inputs": inputs,
        "estimates": estimates,
        "formulae": formulae,
        "provenance": {
            **_baseline_provenance(baseline, rows),
            "metric_units": _metric_units(baseline_metrics),
            "mixed_dimensions": mixed_dimensions,
        },
        "assumptions": assumptions,
        "limitations": limitations,
    }


def _estimate_utilization_change(
    change: dict[str, float | str | int | None],
    baseline_metrics: dict[str, float],
    *,
    estimates: dict[str, float],
    inputs: dict[str, Any],
    formulae: list[str],
    assumptions: list[str],
    limitations: list[str],
) -> None:
    current = _first_metric(baseline_metrics, "util_percent", "utilization_percent")
    if current is None or not 0 < current <= 100:
        limitations.append(
            "현재 utilization baseline이 없거나 0~100% 범위를 벗어나 capacity 민감도를 계산할 수 없습니다."
        )
    else:
        direction = int(change["direction"] or 0)
        signed_change = direction * float(change["value"])
        if change["unit"] == "percentage_point":
            projected = current + signed_change
            input_name = "utilization_delta_percentage_point"
            projection_formula = (
                "projected_util_percent = baseline_util_percent + "
                "utilization_delta_percentage_point"
            )
        else:
            projected = current * (1.0 + signed_change / 100.0)
            input_name = "utilization_change_percent"
            projection_formula = (
                "projected_util_percent = baseline_util_percent * "
                "(1 + utilization_change_percent / 100)"
            )
        if not 0 <= projected <= 100:
            limitations.append(
                "요청한 utilization 변화가 0~100% 물리 범위를 벗어나 계산하지 않았습니다."
            )
        else:
            capacity_change_pct = (projected / current - 1.0) * 100.0
            inputs.update(
                {
                    "baseline_util_percent": current,
                    input_name: signed_change,
                }
            )
            formulae.extend(
                [
                    projection_formula,
                    "capacity_delta_percent = (projected_util_percent / baseline_util_percent - 1) * 100",
                ]
            )
            estimates.update(
                {
                    "baseline_util_percent": round(current, 4),
                    "projected_util_percent": round(projected, 4),
                    "capacity_delta_percent": round(capacity_change_pct, 4),
                    "estimated_capacity_change_percent": round(capacity_change_pct, 4),
                }
            )
            lotcomps = _first_metric(baseline_metrics, "lotcomps", "lot_completions")
            if lotcomps is not None and lotcomps >= 0:
                inputs["baseline_lotcomps"] = lotcomps
                formulae.append(
                    "estimated_lotcomps_delta = baseline_lotcomps * capacity_delta_percent / 100"
                )
                estimates["estimated_lotcomps_delta"] = round(
                    lotcomps * capacity_change_pct / 100.0,
                    4,
                )
                formulae.append(
                    "projected_lotcomps = baseline_lotcomps * projected_util_percent / baseline_util_percent"
                )
                estimates["projected_lotcomps"] = round(lotcomps * projected / current, 4)
            assumptions.append(
                "동일 기간의 capacity가 utilization에 1차 비례한다고 가정했습니다."
            )


def _estimate_cycle_time_change(
    change: dict[str, float | str | int | None],
    baseline_metrics: dict[str, float],
    *,
    estimates: dict[str, float],
    inputs: dict[str, Any],
    formulae: list[str],
    limitations: list[str],
) -> None:
    current = _first_metric(baseline_metrics, "cycleavg", "avg_cycle_hours")
    if current is None or current <= 0:
        limitations.append("현재 cycle time baseline이 없거나 양수가 아니어서 계산할 수 없습니다.")
    else:
        direction = int(change["direction"] or 0)
        delta_ratio = direction * float(change["value"]) / 100.0
        projected = current * (1.0 + delta_ratio)
        if projected <= 0:
            limitations.append("요청한 변화로 cycle time이 0 이하가 되어 계산하지 않았습니다.")
        else:
            inputs.update(
                {
                    "baseline_cycle_time": current,
                    "cycle_time_change_percent": direction * float(change["value"]),
                }
            )
            formulae.append(
                "projected_cycle_time = baseline_cycle_time * (1 + cycle_time_change_percent / 100)"
            )
            estimates.update(
                {
                    "baseline_cycle_time": round(current, 4),
                    "projected_cycle_time": round(projected, 4),
                    "cycle_time_change_percent": round(
                        direction * float(change["value"]), 4
                    ),
                }
            )
            limitations.append(
                "Cycle time과 ontime 간 보정된 탄력성 모델이 없어 납기 준수율 변화는 산출하지 않았습니다."
            )


def _numeric_means(rows: list[dict[str, Any]]) -> dict[str, float]:
    values: dict[str, list[float]] = {}
    for row in rows:
        for key, value in row.items():
            if isinstance(value, bool) or value is None:
                continue
            numeric = _coerce_metric_value(str(key).casefold(), value)
            if numeric is None:
                continue
            if math.isfinite(numeric):
                values.setdefault(str(key).casefold(), []).append(numeric)
    return {key: round(fmean(items), 6) for key, items in values.items() if items}


def _mixed_baseline_dimensions(rows: list[dict[str, Any]]) -> list[str]:
    distinct: dict[str, set[str]] = {
        dimension: set() for dimension in _BASELINE_IDENTITY_DIMENSIONS
    }
    for row in rows:
        normalized_row = {str(key).casefold(): value for key, value in row.items()}
        for dimension in _BASELINE_IDENTITY_DIMENSIONS:
            value = normalized_row.get(dimension)
            if value is None:
                continue
            normalized_value = str(value).strip().casefold()
            if normalized_value:
                distinct[dimension].add(normalized_value)
    return [dimension for dimension, values in distinct.items() if len(values) > 1]


def _coerce_metric_value(key: str, value: Any) -> float | None:
    if isinstance(value, timedelta):
        return value.total_seconds() / 3600.0 if key in {"cycleavg", "cycle_time"} else None
    try:
        return float(value)
    except (TypeError, ValueError):
        pass
    if key not in {"cycleavg", "cycle_time"} or not isinstance(value, str):
        return None
    match = re.fullmatch(
        r"(?:(\d+)\s+days?\s+)?(\d+):(\d{2}):(\d{2}(?:\.\d+)?)",
        value.strip(),
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    days, hours, minutes, seconds = match.groups()
    return (
        int(days or 0) * 24
        + int(hours)
        + int(minutes) / 60
        + float(seconds) / 3600
    )


def _metric_units(metrics: dict[str, float]) -> dict[str, str]:
    units = {
        "util_percent": "percent",
        "utilization_percent": "percent",
        "ontime_percent": "percent",
        "down_percent": "percent",
        "pm_percent": "percent",
        "cycleavg": "hours",
        "avg_cycle_hours": "hours",
        "lotcomps": "lots",
        "lot_completions": "lots",
    }
    return {key: units[key] for key in metrics if key in units}


def _parse_change(question: str) -> dict[str, float | str | int | None] | None:
    changes = _parse_changes(question)
    return changes[0] if changes else None


def _parse_changes(question: str) -> list[dict[str, float | str | int | None]]:
    patterns = (
        (
            r"([+-]?\d+(?:\.\d+)?)\s*(?:%\s*(?:p(?![a-z])|포인트|points?)|퍼센트\s*포인트|percentage\s*points?|percent\s*points?|pp\b)",
            "percentage_point",
        ),
        (r"([+-]?\d+(?:\.\d+)?)\s*(?:%|퍼센트|percent)", "percent"),
        (r"([+-]?\d+(?:\.\d+)?)\s*(?:시간|hours?|hrs?)", "hour"),
    )
    parsed: list[tuple[int, int, dict[str, float | str | int | None]]] = []
    for pattern, unit in patterns:
        for match in re.finditer(pattern, question, flags=re.IGNORECASE):
            if unit == "hour" and re.search(r"(?:최근|지난|last|past)\s*$", question[:match.start()], re.IGNORECASE):
                continue  # This is the baseline window, not an additional downtime change.
            if any(match.start() < end and match.end() > start for start, end, _ in parsed):
                continue
            raw_value = float(match.group(1))
            explicit_direction = (
                -1 if raw_value < 0 else 1 if match.group(1).startswith("+") else None
            )
            textual_direction = _nearest_direction(question, match.span())
            direction = explicit_direction or textual_direction
            if (
                explicit_direction is not None
                and textual_direction is not None
                and explicit_direction != textual_direction
            ):
                direction = None
            parsed.append(
                (
                    match.start(),
                    match.end(),
                    {
                        "value": abs(raw_value),
                        "unit": unit,
                        "direction": direction,
                        "metric": _nearest_change_metric(question, match.span()),
                    },
                )
            )
    return [item for _, _, item in sorted(parsed, key=lambda entry: entry[0])]


def _nearest_change_metric(question: str, change_span: tuple[int, int]) -> str | None:
    lowered = question.casefold()
    metric_terms = {
        "utilization": ("utilization", "util_percent", "util", "가동률"),
        "cycle_time": ("cycle time", "cycleavg", "ct", "사이클 타임", "사이클타임", "사이클"),
        "queue_time": ("queue time", "queue_time", "대기시간", "대기 시간", "큐타임"),
        "down": ("down", "down_percent", "다운", "비가동"),
    }
    candidates: list[tuple[int, str]] = []
    for metric, terms in metric_terms.items():
        for term in terms:
            pattern = (r"(?<!비)가동률" if term == "가동률" else
                       rf"(?<![a-z0-9_]){term}(?![a-z0-9_])" if term in {"ct", "util"} else re.escape(term))
            for match in re.finditer(pattern, lowered):
                distance = min(
                    abs(match.start() - change_span[1]),
                    abs(change_span[0] - match.end()),
                )
                candidates.append((distance, metric))
    if not candidates:
        return None
    nearest_distance = min(distance for distance, _ in candidates)
    nearest = {metric for distance, metric in candidates if distance == nearest_distance}
    return nearest.pop() if len(nearest) == 1 else None


def _nearest_direction(question: str, change_span: tuple[int, int]) -> int | None:
    lowered = question.casefold()
    candidates: list[tuple[int, int]] = []
    directions = {
        -1: ("떨어", "감소", "낮아", "낮추", "내려", "줄", "하락", "drop", "decrease", "reduce"),
        1: ("증가", "높아", "올라", "오르", "올리", "늘", "상승", "increase", "raise", "rise"),
    }
    for direction, terms in directions.items():
        for term in terms:
            for match in re.finditer(re.escape(term), lowered):
                distance = min(
                    abs(match.start() - change_span[1]),
                    abs(change_span[0] - match.end()),
                )
                between_start = min(match.end(), change_span[1])
                between_end = max(match.start(), change_span[0])
                between = lowered[between_start:between_end]
                if distance > 24 or any(
                    barrier in between
                    for barrier in (
                        "capacity",
                        "output",
                        "throughput",
                        "생산능력",
                        "생산 능력",
                        "납기",
                        "lot completion",
                    )
                ):
                    continue
                candidates.append((distance, direction))
    if not candidates:
        return None
    nearest_distance = min(distance for distance, _ in candidates)
    nearest = {direction for distance, direction in candidates if distance == nearest_distance}
    return nearest.pop() if len(nearest) == 1 else None


def _mentions(question: str, *terms: str) -> bool:
    lowered = question.casefold()
    return any(term in lowered for term in terms)


def _first_metric(metrics: dict[str, float], *names: str) -> float | None:
    return next((metrics[name] for name in names if name in metrics), None)


def _summarize(
    estimates: dict[str, float],
    inputs: dict[str, Any],
    formulae: list[str],
    assumptions: list[str],
    limitations: list[str],
) -> str:
    if not estimates:
        return limitations[0]
    input_values = ", ".join(f"{key}={value}" for key, value in inputs.items())
    estimate_values = ", ".join(f"{key}={value}" for key, value in estimates.items())
    sections = [
        f"입력 기준: {input_values}",
        f"1차 영향도 계산 결과: {estimate_values}",
        "계산식: " + "; ".join(formulae),
    ]
    if assumptions:
        sections.append("가정: " + " ".join(assumptions))
    if limitations:
        sections.append("한계: " + " ".join(limitations))
    return "\n".join(sections)


def _baseline_provenance(
    baseline: dict[str, Any], rows: list[dict[str, Any]]
) -> dict[str, Any]:
    query_plan = baseline.get("query_plan") or {}
    return {
        "aggregation": "arithmetic_mean_by_numeric_column",
        "input_row_count": len(rows),
        "input_columns": list(baseline.get("columns") or []),
        "source_tables": list(query_plan.get("source_tables") or []),
        "template_id": query_plan.get("template_id"),
    }
