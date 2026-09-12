"""Read-only preflight for the four-FAB demo; never modifies or refreshes data."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--probe-model", action="store_true", help="One minimal configured-model call, containing no DB rows")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.live:
        parser.error("--live is required for configured database access")

    from app.config import get_settings
    from app.db.read_only import ReadOnlyQueryExecutor
    settings = get_settings()
    report = {"checked_at": datetime.now(UTC).isoformat(), "checks": []}
    checks = report["checks"]
    checks.append({"name": "runtime", "passed": not settings.mock_mode,
                   "mode": "mock" if settings.mock_mode else "live_tools",
                   "model": settings.openai_model,
                   "model_host": urlsplit(settings.openai_endpoint).hostname,
                   "db_host": urlsplit(settings.postgres_dsn or "").hostname})
    store = Path(settings.rag_local_store_path)
    try:
        chunks = [json.loads(line) for line in store.read_text().splitlines() if line.strip()]
        counts = Counter(c.get("metadata", {}).get("knowledge_base", c.get("knowledge_base")) for c in chunks)
        has_flow_reference = any("process_flow_reference_ko" in json.dumps(c, ensure_ascii=False) for c in chunks)
        checks.append({"name": "rag", "passed": bool(chunks) and has_flow_reference,
                       "path": str(store), "chunk_count": len(chunks),
                       "sha256": hashlib.sha256(store.read_bytes()).hexdigest(),
                       "knowledge_bases": dict(counts), "flow_reference_available": has_flow_reference})
    except (OSError, ValueError) as exc:
        checks.append({"name": "rag", "passed": False, "error_type": type(exc).__name__})

    executor = ReadOnlyQueryExecutor()
    for fab in ("fab10", "fab11", "fab12", "fab13"):
        table = f"{fab}.live_process_snapshots_{fab}"
        try:
            rows = executor.execute(
                f"SELECT area, interval_start, interval_end, wip_lots, yield_percent, utilization_percent "
                f"FROM {table} WHERE interval_end = (SELECT MAX(interval_end) FROM {table}) ORDER BY area"
            ).rows
            areas = {r["area"] for r in rows}
            valid = len(rows) == 6 and areas == {"cmp", "deposition", "etch", "implant", "metrology", "photo"}
            valid = valid and all(0 <= float(r["yield_percent"]) <= 100 and 0 <= float(r["utilization_percent"]) <= 100 and float(r["wip_lots"]) >= 0 for r in rows)
            last = max((r["interval_end"] for r in rows), default=None)
            checks.append({"name": fab, "passed": valid, "area_count": len(areas),
                           "latest_observation": last, "current_wip_lots": sum(float(r["wip_lots"]) for r in rows),
                           "age_hours": round((datetime.now(UTC)-last).total_seconds()/3600, 2) if last else None,
                           "data_basis": "generated simulation snapshots; latest observation is not the current wall-clock state",
                           "rows": rows})
        except Exception as exc:  # noqa: BLE001 - probe failure must not suppress the other FAB checks
            checks.append({"name": fab, "passed": False, "error_type": type(exc).__name__})
    if args.probe_model:
        try:
            from app.agents.llm import AzureAgentClient
            value = AzureAgentClient().complete_json(
                system_prompt="Connectivity check. Return ok=true.", input_data={"task": "demo readiness"},
                output_schema={"type": "object", "additionalProperties": False,
                               "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]},
                schema_name="demo_readiness",
            )
            checks.append({"name": "model_connectivity", "passed": value.get("ok") is True})
        except Exception as exc:  # noqa: BLE001 - report an optional connectivity probe failure
            checks.append({"name": "model_connectivity", "passed": False, "error_type": type(exc).__name__})
    report["ready"] = all(c["passed"] for c in checks)
    report["scope"] = "Runtime, retrieval corpus, six latest process areas per FAB. This is not an end-to-end answer-quality evaluation."
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    print(json.dumps({"ready": report["ready"], "checks": [{"name": c["name"], "passed": c["passed"]} for c in checks], "report": str(args.output)}, ensure_ascii=False))
    return 0 if report["ready"] else 1


if __name__ == "__main__":
    sys.exit(main())
