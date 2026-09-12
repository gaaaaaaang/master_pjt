"""Summarize saved live probes without treating a plausible answer as a value audit."""
from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path


def summarize(report):
    rows = report["results"]
    kinds = defaultdict(lambda: {"cases": 0, "expected_status_matches": 0})
    positive = negative = positive_ok = negative_ok = 0
    failures = []
    calls = defaultdict(lambda: {"calls": 0, "skipped_calls": 0, "seconds": 0, "input_tokens": 0, "output_tokens": 0})
    for row in rows:
        response = row.get("response") or {}
        expected = row.get("expected_status", "succeeded")
        actual = row.get("status", response.get("status", "error"))
        matched = expected == actual
        if expected == "succeeded":
            positive += 1
            positive_ok += matched
        else:
            negative += 1
            negative_ok += matched
        group = kinds[row.get("kind", "unknown")]
        group["cases"] += 1
        group["expected_status_matches"] += matched
        if not matched:
            failures.append({"id": row["id"], "expected": expected, "actual": actual,
                             "termination_reason": response.get("termination_reason"),
                             "issues": (response.get("answer_review") or {}).get("issues", []),
                             "error": row.get("error")})
        for call in (response.get("model_usage") or {}).get("calls", []):
            target = calls[call["kind"]]
            if call.get("skipped"):
                target["skipped_calls"] += 1
                continue
            target["calls"] += 1
            for key in ("seconds", "input_tokens", "output_tokens"):
                target[key] += call.get(key) or 0
    seconds = sorted(row["seconds"] for row in rows if isinstance(row.get("seconds"), (int, float)))
    return {
        "source_sha256": report.get("source_sha256"), "started_at": report.get("started_at"),
        "case_count": len(rows),
        "positive": {"cases": positive, "status_succeeded": positive_ok},
        "negative": {"cases": negative, "expected_status_matches": negative_ok},
        "latency_seconds": {"median": round(statistics.median(seconds), 3),
                            "p95_nearest_rank": seconds[max(0, math.ceil(len(seconds)*.95)-1)],
                            "max": max(seconds)} if seconds else {},
        "status_counts": dict(Counter(row.get("status", row.get("response", {}).get("status", "error")) for row in rows)),
        "by_kind": dict(kinds), "model_calls": dict(calls), "failures": failures,
        "interpretation": "Status-based completion on this fixed fixture. Numeric correctness requires the separate raw-row oracle; this is not a general accuracy estimate or an independent assessment of every narrative claim.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = summarize(json.loads(args.report.read_text()))
    target = args.output or args.report.with_name(args.report.stem + "_summary.json")
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({key: result[key] for key in ("case_count", "positive", "negative", "latency_seconds")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
