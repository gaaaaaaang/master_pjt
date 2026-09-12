"""Arithmetic facts from returned observations, independent of answer generation."""
from collections import defaultdict
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from itertools import pairwise
from zoneinfo import ZoneInfo

from app.sub_agent.snapshot_queries import METRICS


def observation_trends(rows):
    if not rows or "observed_at" not in rows[0]:
        return []
    grouped = defaultdict(list)
    for row in rows:
        try:
            when = datetime.fromisoformat(str(row["observed_at"]))
            when = when.replace(tzinfo=ZoneInfo("Asia/Seoul")) if when.tzinfo is None else when.astimezone(ZoneInfo("Asia/Seoul"))
        except (KeyError, ValueError, TypeError):
            return []
        dimension = str(row.get("area") or "all")
        if row.get("fab"):
            dimension = f"{row['fab']} / {dimension}"
        grouped[dimension].append((when, row))
    summaries = []
    for area, points in grouped.items():
        points.sort(key=lambda item: item[0])
        if len(points) < 2 or len({point[0] for point in points}) != len(points):
            continue
        for metric in METRICS:
            if not all(metric in row for _, row in points):
                continue
            try:
                values = [Decimal(str(row[metric])) for _, row in points]
            except InvalidOperation:
                continue
            if not all(value.is_finite() for value in values):
                continue
            delta = values[-1] - values[0]
            rises = any(right > left for left, right in pairwise(values))
            falls = any(right < left for left, right in pairwise(values))
            numeric = {"start_value": values[0], "end_value": values[-1],
                       "minimum": min(values), "maximum": max(values), "absolute_delta": delta,
                       "percent_delta": delta/values[0]*100 if values[0] else None}
            summaries.append({"area": area, "metric": metric,
                              "start_at": points[0][1]["observed_at"], "end_at": points[-1][1]["observed_at"],
                              "point_count": len(points),
                              **{key: str(value) if value is not None else None for key, value in numeric.items()},
                              "display_2dp": {key: str(value.quantize(Decimal(".01"), rounding=ROUND_HALF_UP)) if value is not None else None for key, value in numeric.items()},
                              "movement": "fluctuating" if rises and falls else "increasing" if rises else "decreasing" if falls else "constant",
                              "basis": "returned query aggregates; endpoints and extrema do not establish a pre-window baseline, causal effect, or equal sampling"})
    return summaries
