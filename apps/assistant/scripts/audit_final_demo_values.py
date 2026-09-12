"""Independently aggregate raw DB rows to audit the six-per-FAB SQL demo probes.

No model or generated SQL is used by this oracle. --capture reads only the local
snapshot/model tables; subsequent audits are entirely offline and reproducible.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
FABS = ("fab10", "fab11", "fab12", "fab13")
MEANS = ("avg_queue_minutes", "wip_lots", "utilization_percent", "bottleneck_score")
SUMS = ("down_minutes", "pm_minutes", "lot_completions")


def timestamp(value):
    return datetime.fromisoformat(str(value))


def capture(path):
    from app.config import get_settings
    from app.db.read_only import ReadOnlyQueryExecutor
    if urlsplit(get_settings().postgres_dsn or "").hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Raw capture permits only a localhost DB.")
    executor = ReadOnlyQueryExecutor(max_rows=20000)
    data = {"captured_at": datetime.now(UTC).isoformat(), "fabs": {}}
    for fab in FABS:
        data["fabs"][fab] = {}
        for name, query in (
            ("snapshots", f"SELECT * FROM {fab}.live_process_snapshots_{fab}"),
            ("tools", f"SELECT area, number_of_tools FROM {fab}.toolgroups_{fab}"),
        ):
            result = executor.execute(query)
            if result.row_count >= result.limit:
                raise ValueError(f"Raw capture hit its row limit for {fab}.{name}; a truncated reference cannot establish correctness.")
            data["fabs"][fab][name] = result.rows
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, default=str))


def reference(case, raw, today):
    records = raw["snapshots"]
    latest = max(timestamp(r["interval_end"]) for r in records)
    current = [r for r in records if timestamp(r["interval_end"]) == latest]
    special = special_reference(case, records, current, latest, today)
    if special is not None:
        return special
    kind = case["kind"]
    if kind == "status":
        return [{"wip_lots": sum(Decimal(str(r["wip_lots"])) for r in current),
                 "interval_end": latest.isoformat(), "area_count": len(current)}], []
    if kind in {"area_status", "impact"}:
        fields = ("wip_lots", "avg_queue_minutes") if kind == "area_status" else ("utilization_percent", "lot_completions", "avg_cycle_hours")
        return [{"area": r["area"], "interval_end": r["interval_end"], **{m:r[m] for m in fields}}
                for r in current if r["area"] == "etch"], ["area"]
    if kind == "master":
        groups = defaultdict(Decimal)
        for r in raw["tools"]:
            groups[r["area"]] += Decimal(str(r["number_of_tools"]))
        return [{"area": a, "sum_number_of_tools": v} for a, v in groups.items()], ["area"]
    groups = defaultdict(list)
    for row in records:
        when = timestamp(row["interval_end"])
        if kind == "trend":
            keep = today - timedelta(days=6) <= when.astimezone(KST).date() <= today
        elif kind == "diagnosis":
            keep = row["area"] == "etch" and latest - timedelta(hours=168) < when <= latest
        else:
            raise ValueError(f"Unknown oracle kind: {kind}")
        if keep:
            groups[(str(when.astimezone(KST).date()), row["area"])].append(row)
    result = []
    for (day, area), rows in groups.items():
        row = {"observed_at": day, "area": area}
        for metric in (("yield_percent",) if kind == "trend" else MEANS):
            row[metric] = sum(Decimal(str(r[metric])) for r in rows) / len(rows)
        if kind == "diagnosis":
            row.update({m:sum(Decimal(str(r[m])) for r in rows) for m in SUMS})
        result.append(row)
    return result, ["observed_at", "area"]


def special_reference(case, records, current, latest, today):
    """Independent aggregations for the expanded date/rank/comparison fixture."""
    name = case["id"]
    if name == "fab13_area_compare":
        return [{"area":r["area"], "yield_percent":r["yield_percent"]} for r in current if r["area"] in {"etch", "photo"}], ["area"]
    if name == "fab12_korean_area":
        return [{"area":r["area"], "yield_percent":r["yield_percent"], "utilization_percent":r["utilization_percent"]} for r in current if r["area"] == "etch"], ["area"]
    if name == "fab13_rank":
        rows = sorted(current, key=lambda r: (-Decimal(str(r["wip_lots"])), r["area"]))[:3]
        return [{"area":r["area"], "wip_lots":r["wip_lots"]} for r in rows], ["area"]
    if name not in {"fab12_week_compare", "fab11_yesterday", "fab11_multi_metric"}:
        return None
    groups = defaultdict(list)
    monday = today - timedelta(days=today.weekday())
    for row in records:
        when = timestamp(row["interval_end"]).astimezone(KST)
        if name == "fab12_week_compare":
            if monday - timedelta(days=7) <= when.date() < monday:
                label = f"{monday-timedelta(days=7)} ~ {monday-timedelta(days=1)}"
            elif monday <= when.date() <= today:
                label = f"{monday} ~ {today}"
            else:
                continue
            group = (label, row["area"])
        elif name == "fab11_yesterday":
            if when.date() != today - timedelta(days=1):
                continue
            group = (str(when.date()), row["area"])
        else:
            if row["area"] != "etch" or not latest-timedelta(hours=24) < when <= latest:
                continue
            group = (when.replace(minute=0, second=0, microsecond=0, tzinfo=None).isoformat(), row["area"])
        groups[group].append(row)
    fields = ["comparison_period", "area"] if name == "fab12_week_compare" else ["observed_at", "area"]
    metrics = ["wip_lots"] if name == "fab12_week_compare" else ["avg_queue_minutes"] if name == "fab11_yesterday" else ["wip_lots", "avg_queue_minutes"]
    result = []
    for group, rows in sorted(groups.items()):
        item = dict(zip(fields, group))
        item.update({m:sum(Decimal(str(r[m])) for r in rows)/len(rows) for m in metrics})
        item["observation_count"] = len(rows)
        result.append(item)
    return result, fields


def key(row, fields):
    return tuple(timestamp(row[f]).replace(tzinfo=None).isoformat() if f == "observed_at" and row.get(f) else str(row.get(f, "")) for f in fields)


def equal(expected, actual, field):
    if field == "observed_at":
        return timestamp(expected).replace(tzinfo=None) == timestamp(actual).replace(tzinfo=None)
    if field.endswith("_end"):
        return timestamp(expected) == timestamp(actual)
    try:
        return abs(Decimal(str(expected)) - Decimal(str(actual))) <= Decimal("0.0000001")
    except InvalidOperation:
        return expected == actual


def audit_impact_calculations(report, raw):
    """Check arithmetic for the explicitly specified etch 5%p-drop fixture.

    This verifies the stated first-order formula, not its causal validity in a
    real FAB. No production calculator or generated SQL is used by the oracle.
    """
    checks = []
    for case in report["results"]:
        if case.get("kind") != "impact" or "evidence" not in case.get("response", {}):
            continue
        if not re.search(r"etch.*가동률.*5\s*%p.*떨어", case["question"], re.IGNORECASE):
            continue
        fab = case["id"].split("_", 1)[0]
        rows = raw["fabs"][fab]["snapshots"]
        latest = max(timestamp(r["interval_end"]) for r in rows)
        baseline = next(r for r in rows if r["area"] == "etch" and timestamp(r["interval_end"]) == latest)
        utilization = Decimal(str(baseline["utilization_percent"]))
        completed = Decimal(str(baseline["lot_completions"]))
        projected = utilization - Decimal(5)
        delta = (projected / utilization - 1) * 100
        expected = {"baseline_util_percent":utilization, "projected_util_percent":projected,
                    "capacity_delta_percent":delta, "estimated_capacity_change_percent":delta,
                    "estimated_lotcomps_delta":completed * delta / 100,
                    "projected_lotcomps":completed * projected / utilization}
        calculation = next((item["metadata"] for item in case["response"]["evidence"]
                            if item.get("source_type") == "impact_calculation"), {})
        errors = []
        actual = calculation.get("estimates", {})
        for field, value in expected.items():
            try:
                matches = abs(Decimal(str(actual[field]))-value) <= Decimal("0.000051")
            except (InvalidOperation, KeyError, TypeError):
                matches = False
            if not matches:
                errors.append(f"{field}: expected={value} actual={actual.get(field)}")
        if not calculation.get("assumptions") or not calculation.get("formulae"):
            errors.append("Missing explicit assumptions or formulae")
        checks.append({"id":case["id"], "passed":not errors, "errors":errors,
                       "expected":{key:str(value) for key,value in expected.items()}, "actual":actual})
    return {"passed":sum(check["passed"] for check in checks), "total":len(checks), "results":checks,
            "scope":"Arithmetic under the declared utilization-proportional capacity assumption; not an empirical causal-model validation."}


def audit(report, raw):
    today = timestamp(report["started_at"]).astimezone(KST).date()
    results = []
    skipped = []
    for case in report["results"]:
        if case.get("kind") in {"knowledge", "action", "unsupported"}:
            skipped.append({"id":case["id"], "reason":"Not a numeric SQL result; assess source/status separately."})
            continue
        fab = case["id"].split("_", 1)[0]
        expected, fields = reference(case, raw["fabs"][fab], today)
        response = case.get("response", {})
        actual = response.get("rows")
        if actual is None:
            actual = (response.get("query_result") or {}).get("rows")
        if actual is None:
            actual = (response.get("chart") or {}).get("source_rows") or (response.get("chart") or {}).get("rows")
        if actual is None:
            sql_items = [e for e in response.get("evidence", []) if isinstance(e,dict) and e.get("source_type") == "text2sql_plan"]
            actual = sql_items[-1].get("metadata", {}).get("sample_rows", []) if sql_items else []
        if case["kind"] == "master" and actual:
            value_fields = set(actual[0]) - {"area"}
            if len(value_fields) == 1 and "sum_number_of_tools" not in value_fields:
                field = value_fields.pop()
                actual = [{"area":r["area"], "sum_number_of_tools":r[field]} for r in actual]
        errors = []
        if len(actual) != len(expected):
            errors.append(f"row_count expected={len(expected)} actual={len(actual)}")
        by_key = {key(r, fields):r for r in actual}
        for row in expected:
            target = by_key.get(key(row, fields))
            if target is None:
                errors.append(f"missing target={key(row, fields)}")
                continue
            for field, value in row.items():
                if field not in target or not equal(value, target[field], field):
                    errors.append(f"{key(row, fields)} {field}: expected={value} actual={target.get(field)}")
        values_passed = not errors
        if response.get("status") != "succeeded":
            errors.append(f"answer_status={response.get('status', case.get('status'))}")
        results.append({"id":case["id"], "passed":not errors, "values_passed":values_passed, "expected_rows":len(expected),
                        "actual_rows":len(actual), "errors":errors})
    return {"passed":sum(c["passed"] for c in results), "values_passed":sum(c["values_passed"] for c in results),
            "total":len(results), "results":results, "skipped":skipped,
            "impact_calculations":audit_impact_calculations(report, raw)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--capture", action="store_true")
    parser.add_argument("--report", type=Path, nargs="*")
    args = parser.parse_args()
    if args.capture:
        capture(args.raw)
    raw = json.loads(args.raw.read_text())
    for path in args.report or []:
        result = audit(json.loads(path.read_text()), raw)
        result.update(raw_sha256=hashlib.sha256(args.raw.read_bytes()).hexdigest(),
                      report_sha256=hashlib.sha256(path.read_bytes()).hexdigest(), raw_file=str(args.raw))
        output = path.with_name(path.stem + "_value_audit.json")
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
        print(json.dumps({"report":str(path),"passed":result["passed"],"total":result["total"]}))


if __name__ == "__main__":
    main()
