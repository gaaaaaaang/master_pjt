"""Exercise follow-ups through the real SSE API, using a fresh test conversation."""
from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

TURNS = [
    {"id":"area_status", "question":"FAB11 etch 공정의 현재 WIP을 알려줘", "fab":"fab11", "rows":1, "areas":["etch"]},
    {"id":"area_impact", "question":"그 공정 가동률이 5%p 떨어지면 처리량에 얼마나 영향이 있어?", "fab":"fab11", "rows":1, "areas":["etch"], "kind":"impact"},
    {"id":"widen_and_switch", "question":"FAB12 최근 7일 공정별 수율 추세를 그래프로 보여줘", "fab":"fab12", "rows":42, "areas":["cmp","deposition","etch","implant","metrology","photo"], "kind":"trend"},
    {"id":"period_rank", "question":"그 기간 평균 수율이 가장 낮은 공정을 알려줘", "fab":"fab12", "rows":1, "inherits_period":True},
    {"id":"switch_to_now", "question":"그럼 지금 공정별 WIP 상위 3개를 보여줘", "fab":"fab12", "rows":3, "latest":True},
    {"id":"whole_fab", "question":"FAB13 전체 WIP은 몇 개야?", "fab":"fab13", "rows":1, "wip":159},
    {"id":"compare_areas", "question":"같은 FAB에서 etch와 photo의 현재 수율을 비교해줘", "fab":"fab13", "rows":2, "areas":["etch","photo"]},
]


def read_stream(client, url, payload):
    events = []
    final = None
    with client.stream("POST", url + "/api/chat/stream", json=payload) as response:
        response.raise_for_status()
        lines = []
        for line in response.iter_lines():
            if line.startswith("data:"):
                lines.append(line[5:].lstrip())
            elif not line and lines:
                event = json.loads("\n".join(lines))
                lines = []
                events.append(event)
                if event.get("type") == "run_completed":
                    final = event["data"]
                elif event.get("type") in {"run_failed", "run_error", "run_timeout", "error"}:
                    raise RuntimeError(str(event.get("message")))
        if lines:
            raise RuntimeError("SSE stream ended before event separator")
    if final is None:
        raise RuntimeError("SSE stream ended without a final result")
    return final, events


def verify(turn, result, previous_period):
    errors = []
    expected_status = turn.get("expected_status", "succeeded")
    if result.get("status") != expected_status:
        errors.append(f"status={result.get('status')}")
    if expected_status != "succeeded":
        if result.get("sql") or result.get("chart"):
            errors.append("unresolved request exposed a SQL query or chart")
        if turn.get("answer_contains") and turn["answer_contains"] not in result.get("answer", ""):
            errors.append("clarification omitted the required question")
        return errors, previous_period
    if turn.get("expects_sql") is False:
        if not any(e.get("source_type") == "rag_chunk" for e in result.get("evidence", [])):
            errors.append("missing document evidence")
        if result.get("sql") or result.get("chart"):
            errors.append("document-only request was replaced by SQL or a chart")
        return errors, previous_period
    sql_evidence = [e for e in result.get("evidence", []) if e.get("source_type") == "text2sql_plan"]
    if not sql_evidence:
        return [*errors,"missing SQL evidence"], previous_period
    metadata = sql_evidence[-1]["metadata"]
    plan = metadata.get("query_plan") or {}
    if plan.get("fab_id") != turn["fab"]:
        errors.append(f"fab expected={turn['fab']} actual={plan.get('fab_id')}")
    if metadata.get("row_count") != turn["rows"]:
        errors.append(f"rows expected={turn['rows']} actual={metadata.get('row_count')}")
    rows = metadata.get("sample_rows", [])
    if "metrics" in turn:
        measurement_columns = {"wip_lots", "queue_lots", "avg_queue_minutes", "avg_cycle_hours", "yield_percent",
                               "utilization_percent", "lot_completions", "lot_starts", "down_minutes", "pm_minutes",
                               "bottleneck_score", "temperature_c", "humidity_percent", "defect_ppm"}
        returned = {key for row in rows for key in row if key in measurement_columns}
        if returned != set(turn["metrics"]):
            errors.append(f"metric scope expected={turn['metrics']} actual={sorted(returned)}")
    if "areas" in turn and {r.get("area") for r in rows} != set(turn["areas"]):
        errors.append("area scope mismatch")
    if "wip" in turn and (not rows or float(rows[0].get("wip_lots", -1)) != turn["wip"]):
        errors.append("whole-FAB WIP mismatch against captured demo dataset")
    slots = plan.get("slots", {})
    period = tuple(slots.get(k, {}).get("value") for k in ("date_start", "date_end"))
    if turn.get("inherits_period") and period != previous_period:
        errors.append(f"follow-up lost period: expected={previous_period} actual={period}")
    if turn.get("latest") and (any(period) or "MAX(interval_end)" not in str(result.get("sql"))):
        errors.append("current follow-up still uses historical window")
    if turn.get("kind") == "impact" and not any(e.get("source_type") == "impact_calculation" for e in result.get("evidence", [])):
        errors.append("missing impact estimate")
    if turn.get("kind") == "trend" and not result.get("chart"):
        errors.append("missing chart")
    return errors, period


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--turns", type=int, default=len(TURNS))
    args = parser.parse_args()
    if not args.live:
        parser.error("--live is required for the actual demo API and configured model")
    report = {"started_at":datetime.now(UTC).isoformat(), "transport":"sse", "results":[]}
    conversation = None
    period = (None, None)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=httpx.Timeout(150, connect=5)) as client:
        for turn in TURNS[:args.turns]:
            started = time.monotonic()
            record = dict(turn)
            try:
                result, events = read_stream(client, args.url, {"message":turn["question"], "conversation_id":conversation})
                conversation = result["conversation_id"]
                errors, period = verify(turn, result, period)
                record.update(response=result, errors=errors, passed=not errors,
                              event_count=len(events), event_nodes=[e.get("node") for e in events])
            except Exception as exc:  # noqa: BLE001 - preserve transport/model failures per conversation turn
                record.update(passed=False, error=f"{type(exc).__name__}: {exc}")
            record["seconds"] = round(time.monotonic()-started,3)
            report["results"].append(record)
            report["conversation_id"] = conversation
            args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str))
            print(json.dumps({k:record.get(k) for k in ("id","passed","seconds","errors","error")},ensure_ascii=False),flush=True)


if __name__ == "__main__":
    main()
