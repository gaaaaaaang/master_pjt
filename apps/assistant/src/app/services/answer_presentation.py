"""Presentation metadata without replacing the model-authored answer."""
from __future__ import annotations

import re

METRIC_LABELS = {
    "wip_lots": ("WIP", "LOT"), "queue_lots": ("대기 LOT", "LOT"),
    "yield_percent": ("수율", "%"), "utilization_percent": ("가동률", "%"),
    "avg_queue_minutes": ("평균 대기 시간", "분"), "avg_cycle_hours": ("평균 Cycle Time", "시간"),
    "lot_completions": ("완료 LOT", "LOT"), "lot_starts": ("투입 LOT", "LOT"),
    "down_minutes": ("비가동 시간", "분"), "pm_minutes": ("정비 시간", "분"),
    "bottleneck_score": ("병목 점수", ""), "temperature_c": ("온도", "℃"),
    "humidity_percent": ("습도", "%"), "defect_ppm": ("불량", "ppm"),
}
PROVENANCE = re.compile(r"시뮬레이션|시묬레이션|simulation|실제\s*(?:공장|FAB|사내).*아닙|실측.*아닙|실시간.*아닙", re.IGNORECASE)


def normalize_answer_lines(text):
    # Some structured model responses double-escape Markdown paragraph breaks.
    # Decode prose only; preserve literal backslashes inside inline/fenced code.
    if "\\n\\n" not in text:
        return text
    parts = re.split(r"(```[\s\S]*?```|`[^`]*`)", text)
    return "".join(part if index % 2 else part.replace("\\r\\n", "\n").replace("\\n", "\n")
                   for index, part in enumerate(parts))


def present_response(payload):
    """Keep model wording and raw evidence intact; expose provenance separately."""
    payload["answer"] = normalize_answer_lines(payload.get("answer") or "")
    evidence = payload.get("evidence") or []
    evidence = [item.model_dump() if hasattr(item, "model_dump") else item for item in evidence]
    plans = [item.get("metadata", {}).get("query_plan") or {} for item in evidence if item.get("source_type") == "text2sql_plan"]
    generated = any(plan.get("data_source_type") == "simulation_snapshot" for plan in plans)
    provenance = [note for note in payload.get("limitations", []) if PROVENANCE.search(note)]
    provenance.extend(part for part in re.split(r"(?<=[.!?])\s+|\n", payload.get("answer") or "") if PROVENANCE.search(part))
    sources = list(payload.get("data_sources") or [])
    if generated:
        sources.append({"kind": "demo_observations", "label": "공정 데이터 출처", "description": "PoC용 생성 데이터", "details": list(dict.fromkeys(provenance))})
    elif provenance:
        sources.append({"kind": "reference", "label": "참고자료 출처", "details": list(dict.fromkeys(provenance))})
    payload["data_sources"] = sources
    payload["limitations"] = list(dict.fromkeys(payload.get("limitations", [])))
    return payload
