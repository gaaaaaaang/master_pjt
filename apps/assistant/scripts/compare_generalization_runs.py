"""Compare complete frozen live runs without cherry-picking successful retries."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from statistics import median


def canonical_rows(case):
    rows = ((case.get("response") or {}).get("query_result") or {}).get("rows") or []
    return sorted(json.dumps(row, sort_keys=True, ensure_ascii=False) for row in rows)


def metrics(report):
    stages = defaultdict(lambda: {"calls": 0, "input_tokens": 0, "output_tokens": 0, "seconds": 0})
    results = report["results"]
    for case in results:
        for call in (case.get("response") or {}).get("model_usage", {}).get("calls", []):
            stage = stages[call["kind"]]
            stage["calls"] += 1
            for key in ("input_tokens", "output_tokens", "seconds"):
                stage[key] += call.get(key) or 0
    return {"cases": len(results), "first_attempt_passed": sum(case["passed"] for case in results),
            "input_tokens": sum(stage["input_tokens"] for stage in stages.values()),
            "output_tokens": sum(stage["output_tokens"] for stage in stages.values()),
            "model_calls": sum(stage["calls"] for stage in stages.values()),
            "median_seconds": median(case["seconds"] for case in results),
            "total_seconds_excluding_pacing": sum(case["seconds"] for case in results),
            "rate_limit_responses": sum(call["status_code"] == 429 for call in report.get("network_calls", [])),
            "stages": dict(stages)}


def compare(before, after, fixture):
    if not before.get("finished_at") or not after.get("finished_at"):
        raise ValueError("Both runs must be complete; partial runs cannot establish a pass rate")
    if not before.get("fixture_sha256") or before["fixture_sha256"] != after.get("fixture_sha256"):
        raise ValueError("The frozen fixture must match")
    if [c["id"] for c in before["results"]] != [c["id"] for c in after["results"]]:
        raise ValueError("The full ordered case list must match")
    expected_ids = [case["id"] for case in fixture["cases"]]
    if [case["id"] for case in before["results"]] != expected_ids or len(set(expected_ids)) != len(expected_ids):
        raise ValueError("Every frozen case must occur exactly once in its original order")
    checks = [{"id": old["id"], "before_passed": old["passed"], "after_passed": new["passed"],
               "query_rows_equal": canonical_rows(old) == canonical_rows(new),
               "before_errors": old.get("errors", []), "after_errors": new.get("errors", [])}
              for old, new in zip(before["results"], after["results"], strict=True)]
    old, new = metrics(before), metrics(after)
    return {"scope": "Paired frozen first-attempt runs; latency is descriptive, not a statistical guarantee; row equality is regression evidence, not an independent oracle.",
            "fixture_sha256": before["fixture_sha256"], "before": old, "after": new, "checks": checks,
            "quality_non_regression": all(c["after_passed"] or not c["before_passed"] for c in checks),
            "query_rows_preserved": all(c["query_rows_equal"] for c in checks),
            "input_token_reduction_percent": round((1 - new["input_tokens"] / old["input_tokens"]) * 100, 2) if old["input_tokens"] else None,
            "median_latency_reduction_percent": round((1 - new["median_seconds"] / old["median_seconds"]) * 100, 2)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", required=True, type=Path)
    parser.add_argument("--after", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--fixture", required=True, type=Path)
    args = parser.parse_args()
    before, after = json.loads(args.before.read_text()), json.loads(args.after.read_text())
    if hashlib.sha256(args.fixture.read_bytes()).hexdigest() != before.get("fixture_sha256"):
        raise ValueError("Fixture content changed after the baseline run")
    result = compare(before, after, json.loads(args.fixture.read_text()))
    result["run_sha256"] = {key: hashlib.sha256(path.read_bytes()).hexdigest()
                            for key, path in {"before": args.before, "after": args.after}.items()}
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({key: result[key] for key in ["quality_non_regression", "query_rows_preserved", "input_token_reduction_percent", "median_latency_reduction_percent"]}))
    return result["quality_non_regression"] and result["query_rows_preserved"]


if __name__ == "__main__":
    raise SystemExit(0 if main() else 1)
