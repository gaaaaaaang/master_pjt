"""Audit requested result types on saved responses without DB or model calls.

Completion status alone is insufficient: a procedure needs reviewed document
evidence, a trend needs a chart, and an impact request needs its calculation.
This structural contract audit does not judge every narrative claim.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def audit_case(case):
    response = case.get("response") or {}
    expected = case.get("expected_status", "succeeded")
    actual = case.get("status", response.get("status", "error"))
    errors = [] if actual == expected else [f"status: expected {expected}, got {actual}"]
    if expected != "succeeded":
        return {"id":case["id"], "passed":not errors, "errors":errors}
    evidence = {item.get("source_type") for item in response.get("evidence", [])}
    succeeded = {run.get("agent") for run in response.get("agent_runs", []) if run.get("status") == "succeeded"}
    kind = case.get("kind")
    required = set()
    if kind in {"status", "area_status", "master", "comparison", "trend", "diagnosis", "impact"}:
        required.add("text2sql")
        if "text2sql_plan" not in evidence or not response.get("sql"):
            errors.append("missing executed SQL evidence")
    if kind in {"comparison", "trend"}:
        required.add("visualization")
        if not response.get("chart"):
            errors.append("missing requested chart")
    if kind == "diagnosis":
        required.add("rag")
        if not {"diagnosis_synthesis", "rag_chunk"} <= evidence:
            errors.append("diagnosis lacks document evidence or synthesis")
    if kind == "impact":
        required.add("impact")
        if "impact_calculation" not in evidence:
            errors.append("missing impact calculation")
    if kind in {"knowledge", "action"}:
        required.add("rag")
        if "rag_chunk" not in evidence:
            errors.append("document question lacks retrieved document evidence")
        grounding = response.get("grounding") or {}
        if grounding.get("status") not in {"supported", "partial"}:
            errors.append("document answer was not grounded")
        if grounding.get("validation") != "verified_quotes_and_model_review":
            errors.append("document claims lack quote and model review")
        if not response.get("citations"):
            errors.append("missing document citations")
    if missing := required - succeeded:
        errors.append("required agents did not succeed: " + ", ".join(sorted(missing)))
    return {"id":case["id"], "passed":not errors, "errors":errors}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    original = json.loads(args.report.read_text())
    checks = [audit_case(case) for case in original["results"]]
    report = {"source_sha256":original.get("source_sha256"),
              "report_sha256":hashlib.sha256(args.report.read_bytes()).hexdigest(),
              "cases":len(checks), "passed":sum(check["passed"] for check in checks),
              "checks":checks,
              "interpretation":"Status plus required tool/artifact/document-review contracts; not a numeric or narrative-accuracy audit."}
    output = args.output or args.report.with_name(args.report.stem + "_contract_audit.json")
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({key:value for key,value in report.items() if key != "checks"}))
    for check in checks:
        if not check["passed"]:
            print(check["id"], check["errors"])


if __name__ == "__main__":
    main()
