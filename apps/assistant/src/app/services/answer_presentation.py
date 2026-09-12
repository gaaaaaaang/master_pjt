"""Operational wording with provenance available separately from the answer."""
from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal

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


def operational_text(text):
    # Remove only source disclaimers, never whole sentences containing useful
    # scope, units, or causal caveats merely because they mention provenance.
    text = re.sub(r"(?:이며[,，]?\s*|이며\s*)?(?:실제\s*(?:공장|FAB|사내)\s*)?(?:실측값?|실시간\s*(?:데이터|정보))(?:이나|\s*또는)\s*(?:실시간\s*)?(?:데이터|정보)(?:이|가|은|는)?\s*아닙니다[.]?", "", text)
    text = re.sub(r"(?:실제\s*(?:공장|FAB)\s*)?(?:실측값?|실시간\s*(?:데이터|정보))(?:이|가|은|는)?\s*아닙니다[.]?", "", text)
    text = re.sub(r"실제\s*(?:FAB|공장)\s*(?:실적|실측값?)(?:이|가|은|는)?\s*(?:아니며[,，]?\s*|아닙니다[.]?\s*)", "", text)
    text = re.sub(r"실제\s*(?:FAB|공장)\s*실측값?과(?:는)?\s*다를\s*수\s*있습니다[.]?", "", text)
    text = re.sub(r"[(（]실시간\s*데이터\s*아님[)）]", "", text)
    text = re.sub(r"시뮬레이션\s*(?:기반\s*)?최신\s*스냅샷(?:\s*(?:데이터|값))?", "최신 공정 데이터", text)
    text = re.sub(r"(?:생성된\s*)?시[뮬묬]레이션\s*(?:기반\s*)?(?:또는\s*공정\s*|공정\s*)?(?:스냅샷\s*데이터|스냅샷|관측값|데이터)", "공정 데이터", text)
    text = re.sub(r"시뮬레이션/스냅샷(?:\s*데이터)?", "공정 데이터", text)
    text = text.replace("시뮬레이션/RAG 참고 문서", "참고 문서").replace("시뮬레이션 참고 사례", "참고 사례")
    return text.strip(" ,\n")


def present_response(payload):
    """Keep raw evidence intact; move repetitive provenance out of daily prose."""
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
    # A mixed note can carry a material limitation; retain its operational
    # content instead of hiding the whole note with its source description.
    limitations = []
    for note in payload.get("limitations", []):
        clean = operational_text(note) if PROVENANCE.search(note) else note
        if PROVENANCE.search(note) and not re.search(r"시각|구간|단위|LOT|집계|원인|가정|한정|범위|비가중|기준일|시간대", clean, re.IGNORECASE):
            continue
        if clean:
            limitations.append(clean)
    payload["limitations"] = list(dict.fromkeys(limitations))
    if generated or provenance:
        payload["answer"] = operational_text(payload.get("answer") or "") or payload.get("answer", "")
    rows = (payload.get("query_result") or {}).get("rows") or []
    if payload.get("status") == "succeeded" and payload.get("query_type") == "status" and len(rows) == 1 and generated:
        row = rows[0]
        values = [(key, row[key]) for key in METRIC_LABELS if row.get(key) is not None]
        observed = row.get("interval_end")
        if observed and values and plans:
            fab = str(plans[-1].get("fab_id") or "").upper()
            area = row.get("area") or "전체"
            def value_text(key, value):
                label, unit = METRIC_LABELS[key]
                number = Decimal(str(value))
                display = str(int(number)) if number == number.to_integral_value() else str(number.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
                return f"{label} {display}{unit}"
            payload["answer"] = f"{observed} 기준, {fab} {area}의 " + ", ".join(value_text(key, value) for key, value in values) + "입니다."
    return payload
