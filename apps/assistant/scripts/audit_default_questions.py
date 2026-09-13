"""Independently aggregate captured raw snapshots for the UI default contracts."""
import argparse
import json
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from audit_final_demo_values import capture

KST = ZoneInfo("Asia/Seoul")
MEANS = ["avg_queue_minutes", "wip_lots", "utilization_percent", "bottleneck_score"]
SUMS = ["down_minutes", "pm_minutes", "lot_completions"]


def timestamp(value):
    return datetime.fromisoformat(str(value)).astimezone(KST)


def expected_rows(case, raw, captured_at):
    records = raw["fabs"][case["fabs"][0]]["snapshots"]
    latest = max(timestamp(r["interval_end"]) for r in records)
    if not case.get("diagnosis") and not case.get("chart"):
        return [{"area": r["area"], "wip_lots": r["wip_lots"]}
                for r in records if timestamp(r["interval_end"]) == latest], ["area"]
    groups = defaultdict(list)
    for row in records:
        observed = timestamp(row["interval_end"])
        if case.get("diagnosis"):
            if row["area"] != "etch" or not latest - timedelta(days=7) < observed <= latest:
                continue
        elif not captured_at.date() - timedelta(days=6) <= observed.date() <= captured_at.date():
            continue
        groups[(observed.date().isoformat(), row["area"])].append(row)
    output = []
    for (day, area), rows in groups.items():
        # The source contract has one row per area and interval. Do not silently
        # treat duplicated source records as distinct observations.
        assert len({r["interval_end"] for r in rows}) == len(rows)
        result = {"observed_at": day, "area": area}
        for metric in MEANS if case.get("diagnosis") else ["yield_percent"]:
            values = [Decimal(str(r[metric])) for r in rows if r[metric] is not None]
            result[metric] = sum(values) / len(values) if values else None
        if case.get("diagnosis"):
            for metric in SUMS:
                result[metric] = sum(Decimal(str(r[metric])) for r in rows)
        output.append(result)
    return output, ["observed_at", "area"]


def audit(report, raw):
    results = []
    for case in report["results"]:
        if not case.get("fabs"):
            continue
        expected, keys = expected_rows(case, raw, timestamp(raw["captured_at"]))
        actual = (case["response"].get("query_result") or {}).get("rows", [])
        def index(rows, keys=keys):
            return {tuple(str(row[k])[:10] if k == "observed_at" else row[k] for k in keys): row for row in rows}
        wanted, received = index(expected), index(actual)
        errors = []
        if wanted.keys() != received.keys() or len(actual) != len(expected):
            errors.append("row_keys_or_count_mismatch")
        for key in wanted.keys() & received.keys():
            for metric, value in wanted[key].items():
                if metric in keys:
                    continue
                got = received[key].get(metric)
                if value is None or got is None:
                    equal = value is got
                else:
                    equal = abs(Decimal(str(value)) - Decimal(str(got))) < Decimal("0.00000001")
                if not equal:
                    errors.append(f"{key}:{metric}")
        results.append({"id": case["id"], "passed": not errors, "errors": errors,
                        "expected_rows": len(expected)})
    return {"passed": len(results) == 12 and all(r["passed"] for r in results), "results": results}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--capture", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.capture:
        if args.reference.exists():
            raise ValueError("Choose a new reference path")
        capture(args.reference)
    result = audit(json.loads(args.report.read_text()), json.loads(args.reference.read_text()))
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(0 if result["passed"] else 1)
