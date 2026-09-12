"""Audit new query expressions against captured raw rows. Model calls are disabled.

Without --execute this only checks planning. --execute runs SELECTs against the
configured local DB, then compares them with independent Decimal aggregations.
The reference semantics are explicit fixture fields, not parsed generated SQL.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from app.sub_agent.text2sql import plan_text2sql

KST = ZoneInfo("Asia/Seoul")
STOCK = {"wip_lots", "queue_lots"}
FLOW = {"lot_starts", "lot_completions", "down_minutes", "pm_minutes"}


def ts(value):
    return datetime.fromisoformat(str(value)).astimezone(KST)


def expected_rows(case, source, today):
    rows = source["snapshots"]
    latest = max(ts(row["interval_end"]) for row in rows)
    kind = case["kind"]
    if case.get("date_start") and case.get("date_end"):
        begin = datetime.fromisoformat(case["date_start"]).date()
        end = datetime.fromisoformat(case["date_end"]).date()
        rows = [r for r in rows if begin <= ts(r["interval_end"]).date() < end]
    elif case.get("hours"):
        rows = [r for r in rows if latest-timedelta(hours=case["hours"]) < ts(r["interval_end"]) <= latest]
    elif case.get("days"):
        begin, end = today-timedelta(days=case["days"]-1), today+timedelta(days=1)
        rows = [r for r in rows if begin <= ts(r["interval_end"]).date() < end]
    elif kind == "yesterday":
        rows = [r for r in rows if ts(r["interval_end"]).date() == today-timedelta(days=1)]
    else:
        if kind == "current_today":
            rows = [r for r in rows if ts(r["interval_end"]).date() == today]
            latest = max(ts(r["interval_end"]) for r in rows)
        rows = [r for r in rows if ts(r["interval_end"]) == latest]
    areas = case.get("areas", [])
    if areas and areas != "all":
        rows = [r for r in rows if r["area"] in areas]
    metrics = case["metrics"]
    per_area = bool(areas)
    intervals = defaultdict(list)
    for row in rows:
        intervals[(ts(row["interval_end"]), row["area"] if per_area else None)].append(row)
    observations = []
    for (stamp, area), records in intervals.items():
        observation = {"stamp":stamp, "area":area,
                       "minutes_min":min((ts(r["interval_end"])-ts(r["interval_start"])).total_seconds()/60 for r in records),
                       "minutes_max":max((ts(r["interval_end"])-ts(r["interval_start"])).total_seconds()/60 for r in records)}
        for metric in metrics:
            values = [Decimal(str(r[metric])) for r in records]
            observation[metric] = sum(values) if metric in STOCK | FLOW else sum(values)/len(values)
        observations.append(observation)
    groups = defaultdict(list)
    for observation in observations:
        hour = observation["stamp"].replace(minute=0, second=0, microsecond=0) if kind == "hourly" else None
        if kind == "daily":
            hour = observation["stamp"].replace(hour=0, minute=0, second=0, microsecond=0)
        groups[(hour, observation["area"])].append(observation)
    output = []
    temporal = kind not in {"current", "current_today", "rank", "threshold"}
    for (hour, area), records in groups.items():
        row = {"area":area} if per_area else {}
        if hour:
            row["observed_at"] = hour.replace(tzinfo=None).isoformat()
        for metric in metrics:
            values = [r[metric] for r in records]
            total = case.get("aggregations", {}).get(metric, "sum" if metric in FLOW and kind != "period_mean" else "mean") == "sum"
            row[metric] = sum(values) if total else sum(values)/len(values)
        if temporal:
            row.update(observation_count=len(records),
                       observation_minutes_min=min(r["minutes_min"] for r in records),
                       observation_minutes_max=max(r["minutes_max"] for r in records))
        if kind == "threshold":
            value, cutoff = row[metrics[0]], Decimal(str(case["threshold"]))
            matches = {">=":value >= cutoff, ">":value > cutoff,
                       "<=":value <= cutoff, "<":value < cutoff}
            if not matches[case.get("operator", ">=")]:
                continue
        output.append(row)
    if case.get("limit"):
        output.sort(key=lambda r: ((-1 if case["direction"]=="desc" else 1)*r[metrics[0]], r.get("area", "")))
        output = output[:case["limit"]]
    return output, [*(["observed_at"] if kind in {"hourly", "daily"} else []), *(["area"] if per_area else [])]


def compare(actual, expected, keys, ordered=False):
    def key(row):
        return tuple(str(row.get(k, "")).replace(" ", "T") for k in keys)
    issues = []
    if len(actual) != len(expected):
        issues.append(f"row_count: expected {len(expected)}, actual {len(actual)}")
    mapped = {key(row):row for row in actual}
    for row in expected:
        target = mapped.get(key(row))
        if target is None:
            issues.append(f"missing key: {key(row)}")
            continue
        for column, value in row.items():
            if column in keys:
                continue
            try:
                equal = abs(Decimal(str(value))-Decimal(str(target.get(column)))) <= Decimal("0.000001")
            except (InvalidOperation, TypeError, ValueError):
                equal = value == target.get(column)
            if not equal:
                issues.append(f"{key(row)}.{column}: expected {value}, actual {target.get(column)}")
    if ordered and [key(r) for r in actual] != [key(r) for r in expected]:
        issues.append("ranking order differs")
    return issues


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=Path("apps/assistant/tests/fixtures/final_demo_variants.json"))
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    catalog = json.loads(args.catalog.read_text())
    raw = json.loads(args.raw.read_text())
    today = datetime.now(KST).date()
    if args.execute:
        from app.config import get_settings
        from app.db.read_only import ReadOnlyQueryExecutor
        if urlsplit(get_settings().postgres_dsn or "").hostname not in {"localhost", "127.0.0.1", "::1"}:
            parser.error("This audit permits only a localhost DB.")
        executor = ReadOnlyQueryExecutor(max_rows=1000)
    checks = []
    with (
        patch("app.agents.llm.AzureAgentClient.complete_json", side_effect=RuntimeError("External model calls disabled for this audit")) as disabled_agent,
        patch("app.sub_agent.text2sql.OpenAIText2SQLClient.create_sql", side_effect=RuntimeError("External Text2SQL calls disabled for this audit")) as disabled_model,
        patch("httpx.Client.send", side_effect=AssertionError("HTTP is forbidden in this local-only audit")),
        patch("httpx.AsyncClient.send", side_effect=AssertionError("HTTP is forbidden in this local-only audit")),
    ):
        for case in json.loads(args.fixture.read_text()):
            entries = {entry["table"]:{"logical_table":entry["table"].split(".")[1].removesuffix("_"+case["fab"]),
                       "data_source_type":entry["source_type"], "table_pattern":entry["table"],
                       "columns":[{"name":c} for c in entry["columns"]]} for entry in catalog[case["fab"]]}
            check = {**case}
            try:
                calls_before = disabled_model.call_count + disabled_agent.call_count
                plan = plan_text2sql(case["question"], database_catalog=entries)
                check.update(status=plan.status, sql=plan.sql, plan=asdict(plan.plan) if plan.plan else None)
                check["issues"] = [] if plan.status == case.get("expected_status", "succeeded") else [f"unexpected status: {plan.status}"]
                check["model_calls_attempted"] = disabled_model.call_count + disabled_agent.call_count - calls_before
                if check["model_calls_attempted"]:
                    check["issues"].append("model_required: disabled model fallback is not evidence of unavailable data")
                if args.execute and plan.status == "succeeded" and plan.sql:
                    result = executor.execute(plan.sql)
                    check["rows"] = result.rows
                    if "metrics" in case:
                        expected, keys = expected_rows(case, raw["fabs"][case["fab"]], today)
                        check["expected_rows"] = expected
                        check["issues"].extend(compare(result.rows, expected, keys, ordered=bool(case.get("limit"))))
            except Exception as exc:  # noqa: BLE001 - record each failed probe and continue the audit
                check.update(status="error", issues=[f"{type(exc).__name__}: {exc}"])
            checks.append(check)
    source = Path(__file__).resolve().parents[1] / "src"
    report = {"checked_at":datetime.now(UTC).isoformat(), "model_calls_disabled":True,
              "source_sha256":hashlib.sha256(b"".join(path.read_bytes() for path in sorted(source.rglob("*.py")))).hexdigest(),
              "fixture_sha256":hashlib.sha256(args.fixture.read_bytes()).hexdigest(),
              "raw_sha256":hashlib.sha256(args.raw.read_bytes()).hexdigest(),
              "executed":args.execute, "cases":len(checks), "passed":sum(not c["issues"] for c in checks),
              "model_required":sum(bool(c.get("model_calls_attempted")) for c in checks), "checks":checks}
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    print(json.dumps({k:v for k,v in report.items() if k!="checks"},ensure_ascii=False))
    for check in checks:
        if check["issues"]:
            print(check["id"], check["question"], check["issues"][:3])


if __name__ == "__main__":
    main()
