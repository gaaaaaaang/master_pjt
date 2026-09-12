"""Measure connection reuse against a temporary loopback fixture server.

No configured model endpoint, credentials, FAB rows or external host is used.
These timings are transport overhead only, not real model response latency.
"""
from __future__ import annotations

import argparse
import json
import socket
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from time import perf_counter
from unittest.mock import patch

import httpx
from app.agents.usage import UsageLedger, model_http_client, usage_scope


class FixtureHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        body = b'{"ok":true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        self.wfile.flush()

    def log_message(self, *args):
        pass


class FixtureServer(ThreadingHTTPServer):
    connections = 0

    def get_request(self):
        connection, address = super().get_request()
        connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.connections += 1
        return connection, address


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--calls", type=int, default=12)
    args = parser.parse_args()
    if not 2 <= args.calls <= 50:
        parser.error("--calls must be between 2 and 50")
    server = FixtureServer(("127.0.0.1", 0), FixtureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}/fixture"
    original = httpx.Client
    records = []
    try:
        # Explicitly disable environment proxies for both measurements. Every
        # request goes to the loopback server created by this process.
        with patch("httpx.Client", lambda **kwargs: original(trust_env=False, **kwargs)):
            for mode in ("per_call_client", "per_request_reuse"):
                count_before, started = server.connections, perf_counter()
                ledger = UsageLedger()
                if mode == "per_call_client":
                    for index in range(args.calls):
                        with httpx.Client(timeout=45) as client:
                            assert client.post(url, json={"fixture":index}).json() == {"ok":True}
                else:
                    try:
                        with usage_scope(ledger):
                            for index in range(args.calls):
                                with model_http_client(45) as client:
                                    assert client.post(url, json={"fixture":index}).json() == {"ok":True}
                    finally:
                        ledger.cancel()
                records.append({"mode":mode, "calls":args.calls,
                                "tcp_connections":server.connections-count_before,
                                "seconds":round(perf_counter()-started, 6)})
        report = {"checked_at":datetime.now(UTC).isoformat(), "scope":__doc__,
                  "model_calls":0, "db_calls":0, "host":"127.0.0.1", "results":records}
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps(records))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


if __name__ == "__main__":
    main()
