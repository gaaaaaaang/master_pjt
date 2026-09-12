"""Replay saved chat evidence through the actual grounded Composer without re-retrieval."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path
from time import perf_counter

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT / "src"))
from app.agents.llm import AzureAgentClient
from app.agents.usage import UsageLedger, usage_scope
from app.config import Settings, get_settings
from app.rag.grounding import compose_grounded
from app.rag.manifest import atomic_write


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--case-id", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.live:
        parser.error("--live is required for document transmission.")
    settings = Settings(_env_file=args.env_file)
    if settings.openai_endpoint.rstrip("/") != "https://skax.ai-talentlab.com":
        parser.error("This evaluation is authorized only for https://skax.ai-talentlab.com.")
    for key in ("openai_api_key", "openai_model", "openai_endpoint", "openai_api_version"):
        os.environ[key.upper()] = str(getattr(settings, key))
    get_settings.cache_clear()
    by_id = {c["id"]: c for c in json.loads(args.report.read_text())["cases"]}
    output = {"retrieval_replayed_from": str(args.report), "cases": []}
    for cid in args.case_id:
        case = by_id[cid]
        calls = []

        class RecordingClient:
            def __init__(self, recorded):
                self.recorded = recorded

            def complete_json(self, **kwargs):
                value = AzureAgentClient().complete_json(**kwargs)
                self.recorded.append({"schema": kwargs["schema_name"], "output": value})
                return value

        ledger = UsageLedger()
        start = perf_counter()
        with usage_scope(ledger):
            result = compose_grounded(
                case["query"], case["final"]["evidence"], client=RecordingClient(calls)
            )
        row = {
            "id": cid,
            "seconds": round(perf_counter() - start, 3),
            "result": asdict(result),
            "model_usage": ledger.snapshot(),
            "model_outputs": calls,
        }
        output["cases"].append(row)
        atomic_write(args.output, json.dumps(output, ensure_ascii=False, indent=2) + "\n")
        print(
            json.dumps(
                {
                    "id": cid,
                    "validation": result.validation,
                    "review": result.review,
                    "seconds": row["seconds"],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )


if __name__ == "__main__":
    main()
