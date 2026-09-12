"""Real loopback HTTP/SSE smoke using two already evaluated document questions."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from urllib.parse import urlsplit

import httpx

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT / "src"))
from app.rag.manifest import atomic_write


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--base-url", default="http://127.0.0.1:8007")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.live:
        parser.error("--live is required; the local server invokes the configured approved API.")
    url = urlsplit(args.base_url)
    if url.scheme != "http" or url.hostname not in {"127.0.0.1", "localhost"}:
        parser.error("This smoke is restricted to loopback HTTP.")
    cases = {
        row["id"]: row
        for row in json.loads((APP_ROOT / "tests/fixtures/rag_extension_eval.json").read_text())
    }
    report = {
        "started_at_utc": datetime.now(UTC).isoformat(),
        "transport": "real_loopback_tcp_http",
        "base_url": args.base_url,
        "events": [],
    }
    with httpx.Client(base_url=args.base_url, timeout=120) as client:
        start = perf_counter()
        with client.stream(
            "POST", "/api/chat/stream", json={"message": cases["ext_pm_approval"]["query"]}
        ) as response:
            response.raise_for_status()
            report["stream_content_type"] = response.headers.get("content-type")
            for line in response.iter_lines():
                if not line.startswith("data: "):
                    continue
                event = json.loads(line[6:])
                report.setdefault("first_event_seconds", round(perf_counter() - start, 3))
                if event["node"] == "planner" and "health_during_run_seconds" not in report:
                    before = perf_counter()
                    health = client.get("/health", timeout=2)
                    report["health_during_run_seconds"] = round(perf_counter() - before, 3)
                    report["health_during_run_status"] = health.status_code
                if event["type"] == "run_completed":
                    report["stream_final"] = event["data"]
                else:
                    report["events"].append(event)
        report["stream_seconds"] = round(perf_counter() - start, 3)
        if "stream_final" not in report:
            atomic_write(args.output, json.dumps(report, ensure_ascii=False, indent=2))
            raise RuntimeError("SSE did not produce a final event.")
        start = perf_counter()
        response = client.post("/api/chat", json={"message": cases["ext_unknown_euv"]["query"]})
        report["chat_http_status"] = response.status_code
        response.raise_for_status()
        report["chat_final"] = response.json()
        report["chat_seconds"] = round(perf_counter() - start, 3)
    report["passed"] = (
        report["stream_final"]["status"] == "succeeded"
        and bool(report["stream_final"].get("citations"))
        and report["chat_final"]["status"] == "data_unavailable"
        and not report["chat_final"].get("citations")
        and report.get("health_during_run_status") == 200
    )
    atomic_write(args.output, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                key: report[key]
                for key in (
                    "passed",
                    "first_event_seconds",
                    "health_during_run_seconds",
                    "stream_seconds",
                    "chat_seconds",
                )
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
