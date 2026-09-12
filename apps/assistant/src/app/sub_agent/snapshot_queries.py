"""Small, schema-grounded query contracts for the shared simulated process data.

Unsupported scopes fall through to Text2SQL; these contracts never erase an
entity or substitute a metric just to produce rows.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.db.fab_catalog import table_ref

AREA_ALIASES = {
    "cmp": "cmp", "평탄화": "cmp", "deposition": "deposition", "증착": "deposition",
    "etch": "etch", "식각": "etch", "에치": "etch", "implant": "implant",
    "이온주입": "implant", "metrology": "metrology", "계측": "metrology",
    "photo": "photo", "포토": "photo", "노광": "photo",
}
METRICS = {
    "wip_lots": (r"(?<![a-z_])wip(?:_lots)?(?![a-z_])|재공", "stock"),
    "queue_lots": (r"queue_lots|대기\s*(?:lot|로트|건수|개수)", "stock"),
    "avg_queue_minutes": (r"avg_queue_minutes|queue\s*time|대기\s*시간|큐\s*타임", "mean"),
    "avg_cycle_hours": (r"avg_cycle_hours|cycle\s*time|사이클\s*타임|리드\s*타임", "mean"),
    "yield_percent": (r"yield(?:_percent)?|수율", "mean"),
    "utilization_percent": (r"utilization(?:_percent)?|util_percent|(?<!비)가동률", "mean"),
    "lot_completions": (r"lot_completions|lotcomps|처리량|생산량|완료\s*(?:lot|로트)|output|throughput|capacity", "flow"),
    "lot_starts": (r"lot_starts|lotstarts|투입량|투입\s*(?:lot|로트)", "flow"),
    "down_minutes": (r"down_minutes|(?:다운|비가동)\s*시간|downtime|(?<![a-z_])down(?![a-z_])", "flow"),
    "pm_minutes": (r"pm_minutes|pm\s*시간|정비\s*시간", "flow"),
    "bottleneck_score": (r"bottleneck_score|병목\s*점수", "mean"),
    "temperature_c": (r"temperature_c|온도", "mean"),
    "humidity_percent": (r"humidity_percent|습도", "mean"),
    "defect_ppm": (r"defect_ppm|불량\s*ppm", "mean"),
}
SLOT_METRICS = {"wiplotavg": "wip_lots", "wiplotcur": "wip_lots",
                "queue_time": "avg_queue_minutes",
                "util_percent": "utilization_percent", "cycleavg": "avg_cycle_hours",
                "lotcomps": "lot_completions", "lotstarts": "lot_starts"}
INTERVAL_COLUMNS = ["observation_minutes_min", "observation_minutes_max"]
INTERVAL_INNER = (
    "MIN(EXTRACT(EPOCH FROM (interval_end - interval_start)) / 60.0) AS observation_minutes_min, "
    "MAX(EXTRACT(EPOCH FROM (interval_end - interval_start)) / 60.0) AS observation_minutes_max"
)
INTERVAL_OUTER = "MIN(observation_minutes_min) AS observation_minutes_min, MAX(observation_minutes_max) AS observation_minutes_max"


def period_aggregates(question: str, metrics: list[str]) -> dict[str, str] | None:
    """Bind explicit adjacent operators to each flow, preserving mixed requests.

    Stocks and ratios retain their period means. Ambiguous mixed operators on
    one flow are left for general Text2SQL rather than picking one silently.
    """
    average = r"평균|average|\bmean\b"
    total = r"합계|누적|총합|\btotal\b|\bsum\b"
    operator = rf"(?:{average}|{total})"
    default_average = bool(re.search(average, question)) and not bool(re.search(total, question))
    result = {}
    for metric in metrics:
        if METRICS[metric][1] != "flow":
            result[metric] = "AVG"
            continue
        explicit = set()
        for mention in re.finditer(METRICS[metric][0], question):
            before = re.search(rf"({operator})\s*$", question[:mention.start()])
            after = re.match(rf"\s*(?:의\s*)?({operator})", question[mention.end():])
            for match in (before, after):
                if match:
                    explicit.add("AVG" if re.fullmatch(average, match[1]) else "SUM")
        if len(explicit) > 1:
            return None
        result[metric] = next(iter(explicit), "AVG" if default_average else "SUM")
    return result


def flow_aggregation_note(aggregates: dict[str, str]) -> str:
    labels = {"lot_completions":"완료 LOT", "lot_starts":"투입 LOT", "down_minutes":"비가동 시간", "pm_minutes":"정비 시간"}
    means = [labels[m] for m, op in aggregates.items() if m in labels and op == "AVG"]
    totals = [labels[m] for m, op in aggregates.items() if m in labels and op == "SUM"]
    parts = []
    if means:
        parts.append(f"{'·'.join(means)}은 관측 구간당 비가중 산술평균이며, 시간당 처리량이나 기간 합계가 아닙니다.")
    if totals:
        parts.append(f"{'·'.join(totals)}은 선택한 구간의 합계입니다.")
    return " ".join(parts)


def simulation_areas(question: str) -> list[str]:
    q = question.casefold()
    # Dry/Wet Etch and model area IDs are not synonyms for the simulated etch area.
    if re.search(r"dry[ _-]?etch|wet[ _-]?etch|드라이|웻에치|건식|습식", q):
        return []
    return list(dict.fromkeys(value for alias, value in AREA_ALIASES.items()
                             if re.search(rf"(?<![a-z0-9_]){re.escape(alias)}(?![a-z0-9_])", q)))


def ambiguous_stock_total(question: str, slots: dict[str, str]) -> bool:
    q = question.casefold()
    temporal = bool(slots.get("date_start") or re.fullmatch(r"last_\d+_hours", slots.get("relative_period", "")))
    if not temporal or not re.search(r"누적|합계|총합|\bsum\b|\btotal\b", q):
        return False
    stock = [(m, re.search(pattern, q)) for m, (pattern, kind) in METRICS.items() if kind == "stock"]
    if not any(match for _, match in stock):
        return False
    if re.search(r"누적|cumulative", q):
        return True
    if re.search(r"추세|추이|일별|시간별|trend|daily|hourly", q):
        return False  # Sum across areas at each time, then display period means.
    # A mixed request can explicitly assign the total to a flow metric.
    flow = [re.search(pattern, q) for pattern, kind in METRICS.values() if kind == "flow"]
    if any(match for match in flow):
        return any(re.search(r"합계|총합|\bsum\b|\btotal\b", q[match.end():min(
            [other.start() for other in flow if other and other.start() > match.end()] or [len(q)])])
                   for _, match in stock if match)
    return True


def product_grain_unavailable(question: str, slots: dict[str, str], catalog: dict[str, Any]) -> bool:
    """Area aggregates cannot answer product metrics by simply dropping the product."""
    if not (slots.get("product") or slots.get("products") or re.search(r"제품|\bproduct\b|\bpart\b", question, re.IGNORECASE)):
        return False
    metrics = {column for column, (pattern, _) in METRICS.items() if re.search(pattern, question, re.IGNORECASE)}
    originals = set(slots.get("metrics", slots.get("metric", "")).split(","))
    metrics.update(SLOT_METRICS.get(metric, metric) for metric in originals)
    candidates = [entry for entry in catalog.values() if metrics.union(originals).intersection(c["name"] for c in entry.get("columns", []))]
    return bool(candidates) and all(entry.get("logical_table") == "live_process_snapshots"
        and not {"product", "product_id", "part"}.intersection(c["name"] for c in entry.get("columns", []))
        for entry in candidates)


def requires_flexible_aggregation(question: str) -> bool:
    return bool(re.search(r"가중|weighted|중앙값|median|표준편차|분산|variance|분위|percentile|상관|correlation|회귀|regression|최댓값|최솟값|최대|최소|최고|최저|\b(?:min|max|minimum|maximum)\b", question, re.IGNORECASE))


@dataclass(frozen=True)
class SnapshotQuery:
    sql: str
    columns: list[str]
    metrics: list[str]
    chart: dict[str, Any] | None
    limitations: list[str]
    shape: str
    area: str | None


def build_snapshot_query(question: str, query_type: str, fab: str,
                         slots: dict[str, str], catalog: dict[str, Any], *, row_limit: int = 200) -> SnapshotQuery | None:
    ref = table_ref(fab, "live_process_snapshots")
    entry = catalog.get(ref)
    if not entry or query_type not in {"status", "trend"}:
        return None
    q = question.casefold()
    if requires_flexible_aggregation(q):
        return None
    if re.search(r"비가동률|down_percent|pm_percent", q):
        return None  # Snapshot durations are not precomputed downtime ratios.
    if re.search(r"autosched|report_time|period_\d|보고서|model|마스터|toolgroups|lotrelease|raw.event|fab_incidents|live_process_events", q):
        return None
    if any(slots.get(key) for key in ("product", "products", "route", "toolgroup", "toolgroups", "lot_id", "type_prefix", "line", "periods")):
        return None
    if re.search(r"제품|\bproduct\b|\bpart\b|설비별|장비별|로트별|lot별|[a-z0-9가-힣]+\s*라인|ontime|납기|불량률", q):
        return None
    areas = simulation_areas(q)
    if not areas and slots.get("areas"):
        areas = list(dict.fromkeys(slots["areas"].split(",")))
        if any(area not in set(AREA_ALIASES.values()) for area in areas):
            return None
    if not areas and slots.get("area"):
        area = slots["area"].casefold()
        if area not in set(AREA_ALIASES.values()):
            return None
        areas = [area]
    metrics = [column for column, (pattern, _) in METRICS.items() if re.search(pattern, q)]
    for metric in slots.get("metrics", slots.get("metric", "")).split(","):
        if not metric:
            continue
        column = SLOT_METRICS.get(metric, metric)
        if column not in METRICS:
            return None  # Never answer only the supported part of a metric list.
        metrics.append(column)
    metrics = list(dict.fromkeys(metrics))
    diagnosis = bool(re.search(r"왜|원인|이유|진단|병목|why|diagnos", q))
    impact = bool(re.search(r"영향|늘면|줄면|증가하면|감소하면|떨어지면|impact|what.if", q))
    if re.search(r"capacity|생산\s*능력|캐파", q) and not impact:
        return None  # Observed completions do not directly measure capacity.
    if diagnosis:
        metrics = list(dict.fromkeys([*metrics, "avg_queue_minutes", "wip_lots", "utilization_percent", "down_minutes", "pm_minutes", "lot_completions", "bottleneck_score"]))
    if impact:
        metrics = list(dict.fromkeys([*metrics, "utilization_percent", "lot_completions", "avg_cycle_hours"]))
    if not metrics:
        return None
    known_columns = {c["name"] for c in entry["columns"]}
    if not {*metrics, "interval_end", "interval_start", "fab_id", "area"} <= known_columns:
        return None
    aggregates = period_aggregates(q, metrics)
    if aggregates is None:
        return None
    if slots.get("metric_aggregations"):
        try:
            saved_aggregates = json.loads(slots["metric_aggregations"])
        except (TypeError, ValueError):
            return None
        if (not isinstance(saved_aggregates, dict)
                or any(metric not in metrics or METRICS[metric][1] != "flow" or not isinstance(operator, str) or operator not in {"SUM", "AVG"}
                       for metric, operator in saved_aggregates.items())):
            return None
        aggregates.update(saved_aggregates)
    comparison = comparison_ranges(q, slots)
    if comparison:
        if any(slots.get(key) for key in ("threshold_metric", "ranking_direction", "top_n")) or re.search(r"가장|제일", q):
            return None
        return _period_comparison(ref, fab, metrics, areas, comparison, aggregates=aggregates,
                                  per_area=bool(areas or slots.get("group_by_area") == "true" or re.search(r"공정별|영역별|by\s+area|per\s+area", q)))
    comparing = bool(re.search(r"비교|대비|versus|compare|\bvs\b", q))
    if comparing and len(areas) < 2:
        return None
    def literal(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    filters = [f"fab_id = {literal(fab)}"]
    if areas:
        filters.append("area IN (" + ", ".join(literal(a) for a in areas) + ")")
    limitations = ["시각·날짜는 Asia/Seoul(KST), 집계 기준은 interval_end(구간 종료 시각)입니다."]
    inherited_hours = re.fullmatch(r"last_(\d+)_hours", slots.get("relative_period", ""))
    temporal = (query_type == "trend" and not comparing) or diagnosis or bool(slots.get("date_start") or inherited_hours)
    single_day = bool(slots.get("date_start") and slots.get("date_end") and
                      date.fromisoformat(slots["date_end"]) - date.fromisoformat(slots["date_start"]) == timedelta(days=1))
    explicit_trend = bool(re.search(r"추세|추이|일별|주별|월별|시간별|시간대별|trend|hourly", q))
    latest_in_day = single_day and bool(re.search(r"지금|현재|최신|\bnow\b|\bcurrent\b|\blatest\b", q)) and not bool(re.search(r"평균|누적|추세|추이|시간별|일별|average|trend", q))
    if latest_in_day:
        temporal = False
    if temporal:
        if slots.get("date_start") and slots.get("date_end"):
            filters.extend([f"interval_end >= TIMESTAMPTZ {literal(slots['date_start'] + ' 00:00:00+09:00')}",
                            f"interval_end < TIMESTAMPTZ {literal(slots['date_end'] + ' 00:00:00+09:00')}"])
            limitations.append(f"조회 기간은 KST {slots['date_start']} 이상 {slots['date_end']} 미만입니다.")
        else:
            hours = re.search(r"최근\s*(\d+)\s*시간", q)
            n_hours = int(hours[1]) if hours else int(inherited_hours[1]) if inherited_hours else 168
            if not 1 <= n_hours <= 24 * 366:
                return None
            filters.extend([f"interval_end > (SELECT MAX(interval_end) FROM {ref}) - INTERVAL '{n_hours} hours'",
                            f"interval_end <= (SELECT MAX(interval_end) FROM {ref})"])
            limitations.append(f"기간 미지정 시 최신 적재 시각까지 {n_hours}시간을 조회합니다. 실제 오늘의 실시간 상태와 구분합니다.")
    else:
        day_filter = ""
        if latest_in_day:
            day_filter = (f" WHERE interval_end >= TIMESTAMPTZ {literal(slots['date_start'] + ' 00:00:00+09:00')}"
                          f" AND interval_end < TIMESTAMPTZ {literal(slots['date_end'] + ' 00:00:00+09:00')}")
            limitations.append(f"KST {slots['date_start']} 날짜 안에서 가장 최근에 적재된 구간을 조회했습니다.")
        filters.append(f"interval_end = (SELECT MAX(interval_end) FROM {ref}{day_filter})")
        limitations.append("현재 값은 해당 FAB 전체에서 가장 최근에 적재된 구간 기준입니다.")
    threshold = slots.get("threshold_metric")
    per_area = bool(areas or slots.get("group_by_area") == "true" or re.search(r"공정별|영역별|각\s*(?:공정|영역)|by\s+area|per\s+area|상위|하위|가장|제일", q)
                    or (threshold and re.search(r"공정|영역|\barea", q)))
    having = ""
    if threshold:
        mapped = SLOT_METRICS.get(threshold, threshold)
        if mapped not in metrics or slots.get("threshold_operator") not in {">", "<", ">=", "<="}:
            return None
        value = slots.get("threshold_value", "")
        if not re.fullmatch(r"\d+(?:\.\d+)?", value):
            return None
        if temporal:
            aggregate = aggregates[mapped]
            having = f" HAVING {aggregate}({mapped}) {slots['threshold_operator']} {value}"
            limitations.append("임계값 조건은 기간별 평균·합계를 계산한 뒤 적용합니다.")
        elif per_area:
            filters.append(f"{mapped} {slots['threshold_operator']} {value}")
        else:
            aggregate = "SUM" if METRICS[mapped][1] in {"stock", "flow"} else "AVG"
            having = f" HAVING {aggregate}({mapped}) {slots['threshold_operator']} {value}"
            limitations.append("임계값 조건은 같은 시각의 FAB 전체 합계·평균을 계산한 뒤 적용합니다.")
    where = " AND ".join(filters)
    rank = slots.get("ranking_direction")
    top_n = slots.get("top_n")
    count = re.search(r"(?:공정|영역)\s*(\d+)\s*개|(?:상위|하위|top)\s*(\d+)", q)
    if count:
        top_n = next(value for value in count.groups() if value is not None)
    if not rank and re.search(r"(?:제일|가장)\s*(?:높|큰|많|낮|작|적|심|나쁜)", q):
        low = bool(re.search(r"(?:제일|가장)\s*(?:낮|작|적)", q))
        if re.search(r"(?:제일|가장)\s*(?:심|나쁜)", q):
            if len(metrics) != 1 or metrics[0] not in {"yield_percent", "wip_lots", "avg_queue_minutes", "bottleneck_score"}:
                return None
            low = metrics[0] == "yield_percent"
        rank, top_n = ("ASC" if low else "DESC"), top_n or "1"
    if rank and (not per_area or len(metrics) != 1):
        return None
    if rank and temporal and re.search(r"일별|주별|월별|시간별|날짜별", q):
        return None  # Per-bucket top-N requires a window rank, not a global limit.
    if temporal:
        short_hours = re.search(r"최근\s*(\d+)\s*시간", q)
        hour_count = int(short_hours[1]) if short_hours else int(inherited_hours[1]) if inherited_hours else None
        grain = slots.get("date_grain") or ("hour" if (hour_count and hour_count <= 72) or (single_day and explicit_trend) else "day")
        area_count = len(areas) if areas else len(set(AREA_ALIASES.values())) if per_area else 1
        if (grain == "hour" and not slots.get("date_grain") and not re.search(r"시간별|시간대별|hourly", q)
                and (hour_count or 24) * area_count + area_count > row_limit):
            grain = "day"
            limitations.append("요청한 시간 범위와 공정을 모두 포함하도록 일 단위로 집계했습니다. 시간별 상세는 공정 또는 기간을 좁혀 조회할 수 있습니다.")
        if grain not in {"day", "week", "month", "hour"}:
            return None
        bucket = f"date_trunc('{grain}', interval_end AT TIME ZONE 'Asia/Seoul')"
        area_group = ", area" if per_area else ""
        # First combine disjoint areas at ONE timestamp. Then aggregate across time.
        interval_values = [f"{'SUM' if METRICS[m][1] in {'stock', 'flow'} else 'AVG'}({m}) AS {m}" for m in metrics]
        interval_sql = (f"SELECT interval_end{area_group}, {', '.join(interval_values)}, {INTERVAL_INNER} FROM {ref} "
                        f"WHERE {where} GROUP BY interval_end{area_group}")
        values = [f"{aggregates[m]}({m}) AS {m}" for m in metrics]
        flow_note = flow_aggregation_note(aggregates)
        if flow_note:
            limitations.append(flow_note)
        explicit_period_average = not single_day and not diagnosis and not explicit_trend and bool(re.search(r"평균|average|\bmean\b", q) or slots.get("metric_aggregations"))
        explicit_period_sum = not diagnosis and not explicit_trend and bool(re.search(r"합계|누적|total|sum", q)) and all(METRICS[m][1] == "flow" for m in metrics)
        if (explicit_period_average or explicit_period_sum) and not rank and not comparing:
            label = f"{slots['date_start']} 이상 {slots['date_end']} 미만" if slots.get("date_start") else f"최신 적재 시각까지 {hour_count or 168}시간"
            dimension = "area" if per_area else "period_label"
            selection = "area" if per_area else f"{literal(label)} AS period_label"
            grouping = " GROUP BY area" if per_area else ""
            sql = (f"WITH observations AS ({interval_sql}) SELECT {selection}, {', '.join(values)}, "
                   f"COUNT(*) AS observation_count, MIN(interval_end) AS first_observed_at, "
                   f"MAX(interval_end) AS last_observed_at, {INTERVAL_OUTER} FROM observations{grouping}{having} ORDER BY {dimension}")
            columns = [dimension, *metrics, "observation_count", "first_observed_at", "last_observed_at", *INTERVAL_COLUMNS]
            limitations.append("요청한 전체 기간을 하나로 집계했습니다. 평균은 관측 구간의 비가중 산술평균입니다.")
            chart = {"type":"bar", "x":dimension, "y":metrics[0] if len(metrics)==1 else metrics,
                     "series":None, "x_title":"공정 영역" if per_area else "조회 기간", "y_title":" / ".join(metrics)}
            return SnapshotQuery(sql, columns, metrics, chart, limitations, "period_summary", areas[0] if len(areas)==1 else None)
        if rank or (comparing and not explicit_trend):
            if top_n and (not top_n.isdigit() or not 1 <= int(top_n) <= 200):
                return None
            sql = (f"WITH observations AS ({interval_sql}) SELECT area, {', '.join(values)}, "
                   f"COUNT(*) AS observation_count, MIN(interval_end) AS first_observed_at, "
                   f"MAX(interval_end) AS last_observed_at, {INTERVAL_OUTER} FROM observations GROUP BY area{having} ")
            sql += f"ORDER BY {metrics[0]} {'DESC' if rank.upper() == 'DESC' else 'ASC'}, area ASC" if rank else "ORDER BY area ASC"
            if top_n:
                sql += f" LIMIT {int(top_n)}"
            limitations.append("선택한 전체 기간의 공정별 집계값으로 비교했습니다. 각 지표의 평균·합계 기준을 함께 확인하세요.")
            chart = {"type":"bar" if rank else "grouped_bar", "x":"area", "y":metrics[0] if len(metrics)==1 else metrics, "series":None,
                     "x_title":"공정 영역", "y_title":metrics[0]}
            return SnapshotQuery(sql, ["area", *metrics, "observation_count", "first_observed_at", "last_observed_at", *INTERVAL_COLUMNS],
                                 metrics, chart, limitations, "ranking", areas[0] if len(areas)==1 else None)
        sql = (f"WITH observations AS ({interval_sql}) SELECT {bucket} AS observed_at{area_group}, "
               f"{', '.join(values)}, COUNT(*) AS observation_count, MIN(interval_end) AS first_observed_at, "
               f"MAX(interval_end) AS last_observed_at, {INTERVAL_OUTER} FROM observations GROUP BY {bucket}{area_group}{having} "
               f"ORDER BY observed_at ASC{area_group}")
        columns = ["observed_at", *(["area"] if per_area else []), *metrics, "observation_count", "first_observed_at", "last_observed_at", *INTERVAL_COLUMNS]
        limitations.append("기간별 WIP·대기시간·수율·가동률은 관측 구간의 비가중 산술평균입니다. LOT 가중평균이나 기간 누적 WIP가 아닙니다.")
        chart = {"type": "line", "x": "observed_at", "y": metrics if len(metrics) > 1 else metrics[0],
                 "series": "area" if per_area else None, "x_title": "KST 관측일", "y_title": " / ".join(metrics),
                 "y_zero": len(metrics) > 1}
        shape = "time_series"
        if single_day and per_area and grain == "day" and not explicit_trend:
            chart.update(type="grouped_bar", x="area", series=None, x_title="공정 영역", y_zero=True)
    else:
        if per_area:
            columns = ["interval_start", "interval_end", "area", *metrics]
            order = f"{metrics[0]} {'DESC' if rank.upper() == 'DESC' else 'ASC'}, area ASC" if rank else "area ASC"
            sql = f"SELECT {', '.join(columns)} FROM {ref} WHERE {where} ORDER BY {order}"
            if top_n:
                if not top_n.isdigit() or not 1 <= int(top_n) <= 200:
                    return None
                sql += f" LIMIT {int(top_n)}"
        else:
            columns = ["interval_start", "interval_end", *metrics, "area_count"]
            values = [f"{'SUM' if METRICS[m][1] in {'stock', 'flow'} else 'AVG'}({m}) AS {m}" for m in metrics]
            sql = (f"SELECT MIN(interval_start) AS interval_start, MAX(interval_end) AS interval_end, "
                   f"{', '.join(values)}, COUNT(*) AS area_count FROM {ref} WHERE {where}{having}")
            limitations.append("전체 WIP/LOT 수는 같은 시각의 공정 영역 합계이며, 평균·비율 지표는 공정 영역 비가중 평균입니다.")
        chart = ({"type": "grouped_bar", "x": "area", "y": metrics if len(metrics) > 1 else metrics[0],
                  "series": None, "x_title": "공정 영역", "y_title": " / ".join(metrics)} if comparing else None)
        shape = "rows"
    return SnapshotQuery(sql, columns, metrics, chart, limitations, shape, areas[0] if len(areas) == 1 else None)


def comparison_ranges(question: str, slots: dict[str, str], *, today: date | None = None) -> list[tuple[date, date]]:
    """Return two nonoverlapping inclusive-start/exclusive-end KST windows."""
    if not re.search(r"비교|대비|versus|compare|\bvs\b", question):
        return []
    if slots.get("comparison_date_ranges"):
        try:
            ranges = [tuple(date.fromisoformat(d) for d in part.split("/"))
                      for part in slots["comparison_date_ranges"].split("|")]
        except ValueError:
            return []
        if len(ranges) == 2 and all(len(r) == 2 and r[0] < r[1] for r in ranges) and ranges[0][1] <= ranges[1][0]:
            return ranges
        return []
    today = today or datetime.now(ZoneInfo("Asia/Seoul")).date()
    if re.search(r"지난\s*주", question) and (re.search(r"이번\s*주", question) or re.search(r"지난\s*주\s*(?:대비|보다)", question)):
        monday = today - timedelta(days=today.weekday())
        return [(monday - timedelta(days=7), monday), (monday, today + timedelta(days=1))]
    if "어제" in question and ("오늘" in question or "대비" in question):
        return [(today - timedelta(days=1), today), (today, today + timedelta(days=1))]
    return []


def _period_comparison(ref: str, fab: str, metrics: list[str], areas: list[str], ranges: list[tuple[date, date]], *, per_area: bool, aggregates: dict[str, str]) -> SnapshotQuery:
    def literal(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"
    predicates = []
    cases = []
    labels = []
    for start, end in ranges:
        predicate = (f"interval_end >= TIMESTAMPTZ '{start.isoformat()} 00:00:00+09:00' AND "
                     f"interval_end < TIMESTAMPTZ '{end.isoformat()} 00:00:00+09:00'")
        label = f"{start.isoformat()} ~ {(end - timedelta(days=1)).isoformat()}"
        labels.append(label)
        predicates.append(f"({predicate})")
        cases.append(f"WHEN {predicate} THEN {literal(label)}")
    period = "CASE " + " ".join(cases) + " END"
    area_filter = " AND area IN (" + ", ".join(literal(a) for a in areas) + ")" if areas else ""
    area_group = ", area" if per_area else ""
    values = [f"{'SUM' if METRICS[m][1] in {'stock', 'flow'} else 'AVG'}({m}) AS {m}" for m in metrics]
    inner = (f"SELECT interval_end{area_group}, {', '.join(values)}, {INTERVAL_INNER} FROM {ref} "
             f"WHERE fab_id = {literal(fab)}{area_filter} AND ({' OR '.join(predicates)}) GROUP BY interval_end{area_group}")
    values = [f"{aggregates[m]}({m}) AS {m}" for m in metrics]
    sql = (f"WITH observations AS ({inner}) SELECT {period} AS comparison_period{area_group}, "
           f"{', '.join(values)}, COUNT(*) AS observation_count, MIN(interval_end) AS first_observed_at, "
           f"MAX(interval_end) AS last_observed_at, {INTERVAL_OUTER} FROM observations GROUP BY {period}{area_group} ORDER BY comparison_period{area_group}")
    columns = ["comparison_period", *(["area"] if per_area else []), *metrics, "observation_count", "first_observed_at", "last_observed_at", *INTERVAL_COLUMNS]
    limitations = [f"KST interval_end 기준 두 기간을 비교합니다: {' / '.join(labels)}.",
                   "WIP·수율·대기시간·가동률은 관측 구간의 비가중 산술평균입니다. LOT 가중평균은 아닙니다.",
                   "기간별 실제 관측 수와 첫/마지막 관측 시각을 함께 반환합니다. 이번주/오늘 및 적재가 덜 된 기간은 부분 관측이며, 구간 길이가 다른 기간의 완료/투입 LOT 합계를 같은 조건의 처리량으로 비교하면 안 됩니다."]
    flow_note = flow_aggregation_note(aggregates)
    if flow_note:
        limitations.append(flow_note)
    chart = {"type": "grouped_bar", "x": "comparison_period", "y": metrics if len(metrics) > 1 else metrics[0],
             "series": "area" if per_area else None, "x_title": "KST 비교 기간", "y_title": " / ".join(metrics)}
    return SnapshotQuery(sql, columns, metrics, chart, limitations, "comparison", areas[0] if len(areas) == 1 else None)
