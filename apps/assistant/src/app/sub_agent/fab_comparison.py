"""Compare catalog-backed FAB observations at a shared observation boundary."""
from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal

from app.config import get_settings
from app.db.fab_catalog import ALLOWED_FABS, table_ref
from app.sub_agent.snapshot_queries import METRICS, build_snapshot_query, simulation_areas


def plan_comparison(question, fabs, slots, catalog):
    from app.sub_agent.text2sql import QuerySlot, Text2SQLResult, _deterministic_result

    def unavailable(message):
        return Text2SQLResult(status="data_unavailable", query_type="status", answer=message, limitations=[message])

    if not 2 <= len(fabs) <= 4 or any(fab not in ALLOWED_FABS for fab in fabs):
        return unavailable("비교 가능한 FAB은 FAB10~13입니다. 비교할 대상을 확인해 주세요.")
    if re.search(r"영향|늘면|줄면|떨어지면|감소하면|증가하면|what.if", question, re.IGNORECASE):
        return unavailable("FAB별 현재 지표는 비교할 수 있습니다. 변화량에 따른 영향 계산은 대상 FAB과 변화 조건을 각각 지정해 주세요.")
    values = {key: slot.value for key, slot in slots.items()}
    if any(values.get(key) for key in ("threshold_metric", "ranking_direction", "top_n")):
        return unavailable("먼저 FAB별 전체 지표를 비교한 뒤, 공정 순위나 임계값 조건을 별도로 지정해 주세요.")
    values["group_by_area"] = "true"
    if not simulation_areas(question) and not re.search(r"(?:그|같은|동일)\s*공정", question):
        for key in ("area", "areas", "process"):
            values.pop(key, None)
    temporal = bool(re.search(r"추세|추이|일별|시간별|주별|월별|trend|hourly", question, re.IGNORECASE) or values.get("relative_period") or values.get("date_start"))
    if temporal and not simulation_areas(question) and not re.search(r"공정별|영역별|각\s*공정|by\s+area|per\s+area", question, re.IGNORECASE):
        values["group_by_area"] = "false"
    diagnosis = bool(re.search(r"왜|원인|이유|진단|why", question, re.IGNORECASE))
    if diagnosis:
        values["metrics"] = ",".join(dict.fromkeys([
            *values.get("metrics", values.get("metric", "wiplotavg")).split(","),
            "avg_queue_minutes", "wip_lots", "utilization_percent", "down_minutes", "pm_minutes", "lot_completions",
        ]))
    # FAB comparison is handled here; the shared builder handles each FAB's
    # metric/date contracts. A "why" question without dates compares current values.
    local_question = re.sub(r"비교|차이|대비|왜|원인|이유|진단|versus|compare|\bvs\b|why", " ", question, flags=re.IGNORECASE)
    tables = [table_ref(fab, "live_process_snapshots") for fab in fabs]
    common = f"(SELECT MAX(interval_end) FROM {tables[0]} WHERE " + " AND ".join(
        f"interval_end IN (SELECT interval_end FROM {table})" for table in tables[1:]
    ) + ")"
    queries = []
    contracts = []
    for fab, table in zip(fabs, tables, strict=True):
        contract = build_snapshot_query(local_question, "trend" if temporal else "status", fab, values, catalog,
                                        row_limit=max(1, get_settings().db_max_rows // len(fabs)))
        if contract is None:
            return unavailable("요청한 FAB·지표·세부 구분을 같은 기준으로 비교할 자료가 부족합니다. 공정별 WIP·수율·가동률 등 조회 가능한 지표와 기간을 지정해 주세요.")
        sql = contract.sql.replace(f"(SELECT MAX(interval_end) FROM {table})", common)
        queries.append(f"SELECT '{fab.upper()}' AS fab, comparison_rows.* FROM ({sql}) AS comparison_rows")
        contracts.append(contract)
    columns = ["fab", *contracts[0].columns]
    if any(contract.columns != contracts[0].columns for contract in contracts):
        return unavailable("FAB별 조회 열이 달라 동일한 기준의 비교를 완료하지 못했습니다.")
    combined = " UNION ALL ".join(queries)
    chart = {"type": "grouped_bar", "x": "area", "y": contracts[0].metrics[0], "series": "fab", "x_title": "공정", "y_title": contracts[0].metrics[0]}
    if temporal:
        # Keep each FAB/process series separate, including when dates coincide.
        if "observed_at" not in columns:
            return unavailable("기간 집계와 공정 순위는 각각 비교해 주세요. 시간 추세는 일별·시간별 단위를 지정할 수 있습니다.")
        series_expression = "fab || ' / ' || area" if "area" in columns else "fab"
        combined = f"SELECT joined.*, {series_expression} AS comparison_series FROM ({combined}) AS joined"
        columns.append("comparison_series")
        chart = {"type": "line", "x": "observed_at", "y": contracts[0].metrics[0], "series": "comparison_series", "y_zero": False}
    from app.services.answer_presentation import METRIC_LABELS
    chart["title"] = " · ".join(fab.upper() for fab in fabs) + (" 공정별 " if "area" in columns else " 전체 ") + METRIC_LABELS[contracts[0].metrics[0]][0] + (" 추세" if temporal else " 비교")
    combined += " ORDER BY fab, " + ("observed_at" + (", area" if "area" in columns else "") if temporal else "area")
    bound_slots = {key: slot for key, slot in slots.items() if key not in {"area", "areas", "process"} or key in values}
    bound_slots.update(fab_id=QuerySlot(fabs[0], "explicit_user", 1, fabs[0]), fab_ids=QuerySlot(",".join(fabs), "explicit_user", 1, question))
    return _deterministic_result(
        query_type="trend" if temporal else "status", fab_id=fabs[0], slots=bound_slots,
        template_id="fab_observation_comparison", table=tables[0], additional_tables=tuple(tables[1:]),
        sql=combined, columns=columns, filters=[], order_by=[], expected_result_shape="fab_comparison",
        chart_intent=chart, data_source_type="simulation_snapshot",
        additional_limitations=[*contracts[0].limitations, "FAB 간 비교는 공통 관측 시각을 기준으로 합니다. 공정별 차이는 전체 차이의 구성 요인이며, 원인을 확정하려면 투입·완료 이력과 설비 상태를 함께 확인해야 합니다."],
    )


def comparison_facts(rows):
    if not rows or "fab" not in rows[0] or "observed_at" in rows[0]:
        return {}
    if len({str(row.get("interval_end")) for row in rows}) != 1:
        return {}
    by_fab = {}
    for row in rows:
        by_fab.setdefault(row["fab"], []).append(row)
    if len(by_fab) < 2:
        return {}
    area_sets = [{row.get("area") for row in group} for group in by_fab.values()]
    if any(areas != area_sets[0] for areas in area_sets) or any(len(group) != len(area_sets[0]) for group in by_fab.values()):
        return {}
    metrics = [metric for metric in METRICS if all(row.get(metric) is not None for row in rows)]
    totals = {}
    for fab, values in by_fab.items():
        totals[fab] = {}
        for metric in metrics:
            total = sum(Decimal(str(row[metric])) for row in values)
            if METRICS[metric][1] == "mean":
                total /= len(values)
            totals[fab][metric] = str(total)
    baseline = next(iter(totals))
    comparisons = []
    contributions = []
    for fab in list(totals)[1:]:
        for metric in metrics:
            left, right = Decimal(totals[baseline][metric]), Decimal(totals[fab][metric])
            comparisons.append({"metric": metric, "left_fab": baseline, "right_fab": fab,
                                "left_value": str(left), "right_value": str(right),
                                "left_minus_right": str(left-right),
                                "relative_to_right_percent": str((left-right)/right*100) if right else None})
            left_areas = {row["area"]: row for row in by_fab[baseline]}
            for row in by_fab[fab]:
                if row["area"] in left_areas:
                    contributions.append({"metric": metric, "area": row["area"], "left_fab": baseline, "right_fab": fab,
                                          "left_minus_right": str(Decimal(str(left_areas[row["area"]][metric])) - Decimal(str(row[metric])))})
    return {"totals": totals, "comparisons": comparisons, "area_differences": contributions,
            "basis": "same observation time; sum of disjoint process areas for counts, unweighted area mean for rates"}


def comparison_summary(rows, metrics=None):
    facts = comparison_facts(rows)
    if not facts:
        return ""
    from app.services.answer_presentation import METRIC_LABELS

    def display(value):
        number = Decimal(value)
        return str(int(number)) if number == number.to_integral_value() else str(number.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

    parts = [f"{rows[0].get('interval_end')} 기준 FAB 비교입니다."]
    for item in facts["comparisons"]:
        metric = item["metric"]
        if metrics and metric not in metrics:
            continue
        label, unit = METRIC_LABELS[metric]
        delta = Decimal(item["left_minus_right"])
        diff_unit = "%p" if unit == "%" else unit
        direction = "높습니다" if delta > 0 else "낮습니다"
        scope = " (공정 영역 비가중 평균)" if METRICS[metric][1] == "mean" else ""
        parts.append(f"{item['left_fab']} {label}은 {display(item['left_value'])}{unit}, "
                     f"{item['right_fab']}은 {display(item['right_value'])}{unit}입니다{scope}. "
                     + (f"{item['left_fab']}이 {display(abs(delta))}{diff_unit} {direction}." if delta else "두 FAB의 값이 같습니다."))
        if metric != "wip_lots":
            continue
        percent = item["relative_to_right_percent"]
        if percent is not None and delta:
            parts[-1] += f" {item['right_fab']} 대비 상대 차이는 {abs(Decimal(percent)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)}%입니다."
        differences = sorted((row for row in facts["area_differences"] if row["metric"] == metric and row["right_fab"] == item["right_fab"]),
                             key=lambda row: abs(Decimal(row["left_minus_right"])), reverse=True)
        parts.append("공정별 WIP 차이(" + item["left_fab"] + " − " + item["right_fab"] + "): "
                     + ", ".join(f"{row['area']} {Decimal(row['left_minus_right']):+f}LOT" for row in differences) + ".")
    if "wip_lots" in rows[0] and "avg_queue_minutes" in rows[0]:
        wip_differences = sorted((item for item in facts["area_differences"] if item["metric"] == "wip_lots"),
                                 key=lambda item: abs(Decimal(item["left_minus_right"])), reverse=True)
        if wip_differences:
            focus = wip_differences[0]
            observed = [row for row in rows if row["area"] == focus["area"] and row["fab"] in {focus["left_fab"], focus["right_fab"]}]
            details = []
            for row in observed:
                values = [f"{METRIC_LABELS[key][0]} {display(str(row[key]))}{METRIC_LABELS[key][1]}"
                          for key in ("avg_queue_minutes", "utilization_percent", "pm_minutes") if row.get(key) is not None]
                details.append(row["fab"] + " " + ", ".join(values))
            parts.append(f"차이가 가장 큰 {focus['area']} 공정을 먼저 확인할 수 있습니다: " + "; ".join(details)
                         + ". 이 값들은 같은 구간의 관측 상태이며, 대기·정비가 재공 차이를 일으켰는지는 시간 순서의 이력 확인이 필요합니다.")
    return "\n".join(parts)


def comparison_trend_summary(rows):
    if not rows or "fab" not in rows[0] or "observed_at" not in rows[0]:
        return ""
    from app.services.answer_presentation import METRIC_LABELS
    fabs = list(dict.fromkeys(row["fab"] for row in rows))
    metrics = [key for key in METRICS if key in rows[0]]
    labels = "·".join(METRIC_LABELS[key][0] for key in metrics)
    start = min(str(row["first_observed_at"]) for row in rows)
    end = max(str(row["last_observed_at"]) for row in rows)
    scope = "공정별" if "area" in rows[0] else "전체"
    return (f"{' · '.join(fabs)} {scope} {labels} 추세입니다.\n"
            f"관측 범위는 {start}부터 {end}까지입니다.\n"
            "차트의 FAB별 선과 결과 표에서 같은 시각의 값을 비교할 수 있습니다. "
            "지표별 단위와 집계 기준은 결과 표와 조회 근거에 표시합니다. "
            "첫·마지막 구간이나 중간에 관측 누락이 있는 구간은 전체 시간대를 대표하지 않을 수 있습니다. "
            "표의 관측 구간 수와 최초·최종 관측 시각을 함께 확인해 주세요.")
