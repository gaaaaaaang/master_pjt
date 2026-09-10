"""Repeated live generation from frozen evidence; keep every attempt and model verdict.

Run baseline and changed source trees separately against the same evidence report.
This isolates generation/review from retrieval and is not an end-to-end accuracy score.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import asdict
from pathlib import Path
from time import perf_counter


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--evidence-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    if not args.live or not 1 <= args.repeats <= 5:
        parser.error("--live and repeats between 1 and 5 are required.")
    app = args.source_root.resolve() / "apps/assistant"
    sys.path.insert(0, str(app / "src"))
    from app.agents.llm import AzureAgentClient
    from app.config import get_settings
    from app.rag.grounding import compose_grounded, normalized

    settings = get_settings()
    if settings.openai_endpoint.rstrip("/") != "https://skax.ai-talentlab.com":
        parser.error("This replay is restricted to the previously authorized API destination.")
    selected = {"ext_pm_approval", "ext_hold_deadline", "ext_equipment_record",
                "ext_alternate", "ext_knowledge_update", "ext_unknown_threshold"}
    cases = [r for r in json.loads(args.evidence_report.read_text())["results"]
             if r["id"] in selected]
    if len(cases) != len(selected):
        parser.error("Missing required replay case.")
    report = {
        "source_root": str(args.source_root),
        "source_sha256": hashlib.sha256(b"".join(
            str(p.relative_to(app)).encode() + b"\0" + p.read_bytes()
            for p in sorted((app / "src").rglob("*.py"))
        )).hexdigest(),
        "evidence_report_sha256": hashlib.sha256(args.evidence_report.read_bytes()).hexdigest(),
        "model": settings.openai_model, "repeats": args.repeats,
        "retrieval": "frozen; not rerun", "results": [], "completed": False,
    }
    # Instrument the actual client rather than replacing its sampling settings.
    original = AzureAgentClient._complete_json
    calls = []

    def recorded(client, **kwargs):
        started = perf_counter()
        output = original(client, **kwargs)
        calls.append({"schema": kwargs["schema_name"],
                      "temperature": getattr(client, "temperature", None),
                      "input": kwargs["input_data"], "output": output,
                      "seconds": perf_counter() - started})
        return output

    AzureAgentClient._complete_json = recorded
    args.output.parent.mkdir(parents=True, exist_ok=True)
    for repeat in range(args.repeats):
        for case in cases:
            calls.clear()
            started = perf_counter()
            result = compose_grounded(case["question"], case["evidence"])
            sources = {e["metadata"]["chunk_id"]: e["content"] for e in case["evidence"]}
            quotes_valid = all(
                c["chunk_id"] in sources
                and normalized(c["quote"]) in normalized(sources[c["chunk_id"]])
                for c in result.citations
            )
            row = {"id": case["id"], "repeat": repeat + 1, "question": case["question"],
                   "seconds": perf_counter() - started, "answer": asdict(result),
                   "quotes_valid": quotes_valid, "model_calls": list(calls)}
            report["results"].append(row)
            args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
            print(json.dumps({"id": row["id"], "repeat": repeat + 1,
                              "status": result.status, "calls": len(calls)}, ensure_ascii=False),
                  flush=True)
    report["completed"] = True
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
