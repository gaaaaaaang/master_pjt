"""Check calculator arithmetic against captured raw baselines without DB/model calls.

This validates the declared first-order formulas and parsing, not causal accuracy
or the application's full planning/composition path.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from app.sub_agent.impact import estimate_output_delta


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    raw = json.loads(args.raw.read_text())
    checks = []
    with (
        patch("httpx.Client.send", side_effect=AssertionError("External HTTP forbidden")),
        patch("httpx.AsyncClient.send", side_effect=AssertionError("External HTTP forbidden")),
    ):
        for fab, source in raw["fabs"].items():
            latest = max(datetime.fromisoformat(row["interval_end"]) for row in source["snapshots"])
            baseline = next(row for row in source["snapshots"] if row["area"] == "etch" and datetime.fromisoformat(row["interval_end"]) == latest)
            util = Decimal(str(baseline["utilization_percent"]))
            completions = Decimal(str(baseline["lot_completions"]))
            cycle = Decimal(str(baseline["avg_cycle_hours"]))
            cases = []
            for direction, verb in ((1, "오르면"), (-1, "낮추면")):
                for unit in ("%p", "% 포인트", "퍼센트포인트", "percentage points", "pp", "%"):
                    projected = util * (1 + Decimal(direction) / 20) if unit == "%" else util + 5 * direction
                    cases.append((f"{fab} etch 가동률이 5{unit} {verb} 처리량 영향은?", {
                        "projected_util_percent": projected,
                        "capacity_delta_percent": (projected / util - 1) * 100,
                        "projected_lotcomps": completions * projected / util,
                        "estimated_lotcomps_delta": completions * (projected / util - 1),
                    }, "succeeded"))
            cases.append((f"{fab} etch CT가 10% 줄면 영향은?", {"projected_cycle_time":cycle * Decimal(".9")}, "succeeded"))
            for question in ("가동률이 120%p 오르면 영향은?", "비가동률이 5%p 오르면 처리량 영향은?",
                             "가동률이 5%p 증가한 뒤 3%p 감소하면 영향은?"):
                cases.append((f"{fab} etch {question}", {}, "data_unavailable"))
            for index, (question, expected, status) in enumerate(cases):
                result = estimate_output_delta({"rows":[baseline]}, {"question":question, "fab":fab})
                issues = [] if result["status"] == status else [f"status={result['status']}"]
                for metric, value in expected.items():
                    actual = result["estimates"].get(metric)
                    if actual is None or abs(Decimal(str(actual)) - value) > Decimal(".000051"):
                        issues.append(f"{metric}: expected={value}, actual={actual}")
                if expected and not result["formulae"]:
                    issues.append("missing calculation formula")
                if "projected_util_percent" in expected and not result["assumptions"]:
                    issues.append("missing first-order assumption")
                if not expected and result["estimates"]:
                    issues.append("unsupported case returned a numerical estimate")
                checks.append({"id":f"{fab}_{index}", "question":question, "expected":expected,
                               "status":result["status"], "estimates":result["estimates"],
                               "issues":issues, "passed":not issues})
    source = Path(__file__).resolve().parents[1] / "src"
    report = {"checked_at":datetime.now(UTC).isoformat(), "scope":__doc__, "db_calls":0, "model_calls":0,
              "source_sha256":hashlib.sha256(b"".join(path.read_bytes() for path in sorted(source.rglob("*.py")))).hexdigest(),
              "raw_sha256":hashlib.sha256(args.raw.read_bytes()).hexdigest(),
              "cases":len(checks), "passed":sum(check["passed"] for check in checks), "checks":checks}
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    print(json.dumps({key:report[key] for key in ("cases", "passed", "db_calls", "model_calls")}))
    for check in checks:
        if check["issues"]:
            print(check["id"], check["issues"])


if __name__ == "__main__":
    main()
