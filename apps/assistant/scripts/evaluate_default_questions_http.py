"""Run the exact UI defaults against the running server, preserving every attempt."""
import argparse
import hashlib
import json
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from evaluate_demo_conversations import read_stream
from evaluate_fab_followup_live import check

DEFAULTS = Path(__file__).resolve().parents[2] / "web/src/default-questions.json"


def cases():
    for index, card in enumerate(json.loads(DEFAULTS.read_text())):
        for fab in (["fab10", "fab11", "fab12", "fab13"] if "{fab}" in card["prompt"] else [None]):
            case = {"id": f"default_{index}_{fab or 'knowledge'}",
                    "question": card["prompt"].replace("{fab}", (fab or "").upper())}
            if fab:
                case["fabs"] = [fab]
            if card["icon"] == "chart":
                case["chart"] = True
                case["expected"] = {"row_count": 42, "metrics": ["yield_percent"]}
            elif card["icon"] == "search":
                case["diagnosis"] = True
                case["expected"] = {"area": "etch", "metrics": ["avg_queue_minutes", "wip_lots"]}
            elif card["icon"] == "layers":
                case["expected"] = {"row_count": 6, "metrics": ["wip_lots"]}
            else:
                case["knowledge"] = True
            case.setdefault("expected", {})["approved"] = True
            yield case


def validate(case, result):
    errors = check(case, result)
    if case.get("diagnosis"):
        if not any(e["source_type"] == "rag_chunk" for e in result.get("evidence", [])):
            errors.append("missing_diagnosis_document")
        if len((result.get("query_result") or {}).get("rows", [])) < 2:
            errors.append("insufficient_observations_for_change")
    return errors


def run(args):
    assert urlsplit(args.url).hostname in {"localhost", "127.0.0.1", "::1"}
    if args.output.exists():
        raise ValueError("Choose a new output file to preserve earlier attempts")
    report = {"transport": "actual_HTTP_SSE", "defaults": str(DEFAULTS), "results": []}
    source = Path(__file__).resolve().parents[1] / "src"
    def fingerprint():
        return hashlib.sha256(b"".join(p.read_bytes() for p in sorted(source.rglob("*.py")))).hexdigest()
    report["source_sha256"] = fingerprint()
    report["defaults_sha256"] = hashlib.sha256(DEFAULTS.read_bytes()).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=180) as client:
        readiness = client.get(args.url + "/health/ready")
        report["readiness"] = readiness.json()
        if readiness.status_code != 200:
            report["passed"] = False
            args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
            return False
        for index, case in enumerate(cases()):
            if index:
                time.sleep(args.pause)
            started = time.monotonic()
            record = dict(case)
            try:
                result, events = read_stream(client, args.url, {"message": case["question"]})
                errors = validate(case, result)
                record.update(response=result, errors=errors, passed=not errors, event_count=len(events))
            except Exception as exc:  # noqa: BLE001 - preserve each failure and finish the bounded suite
                record.update(passed=False, error=f"{type(exc).__name__}: {exc}")
            record["seconds"] = round(time.monotonic() - started, 3)
            report["results"].append(record)
            args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
            print(json.dumps({k: record.get(k) for k in ["id", "passed", "errors", "error", "seconds"]}), flush=True)
    report["source_unchanged"] = fingerprint() == report["source_sha256"]
    report["passed"] = report["source_unchanged"] and all(r["passed"] for r in report["results"])
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return report["passed"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--pause", type=float, default=25)
    parser.add_argument("--output", type=Path, required=True)
    raise SystemExit(0 if run(parser.parse_args()) else 1)
