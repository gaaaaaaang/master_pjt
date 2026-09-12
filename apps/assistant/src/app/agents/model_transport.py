"""Reuse model HTTP connections within one request and close them on completion."""
from __future__ import annotations

import logging
from contextlib import contextmanager
from threading import Lock
from weakref import finalize

import httpx


class ModelTransportUnavailable(RuntimeError):
    """A prior network failure stopped further calls to this request's endpoint."""


def _close_clients(clients):
    for client in clients:
        try:
            client.close()
        except Exception as exc:  # noqa: BLE001 - cleanup must not mask the request outcome
            logging.getLogger(__name__).warning("Model connection cleanup failed: %s", type(exc).__name__)


class RequestModelTransport:
    """A pool belongs to one graph run; credentials stay on individual requests.

    Cancellation prevents new leases, while an already running sync HTTP call
    retains its client until it finishes. This matches the graph's cancellation
    contract and avoids closing a connection under a running worker thread.
    """

    def __init__(self):
        self._clients = {}
        self._lock = Lock()
        self._active = 0
        self._closed = False
        self._failed_endpoints: set[str] = set()
        self._rate_limited_endpoints: set[str] = set()
        # Direct graph consumers also release idle connections when the state is
        # discarded. The service/API explicitly close on success and failure.
        finalize(self, lambda clients: _close_clients(list(clients.values())), self._clients)

    @contextmanager
    def lease(self, timeout_seconds: float, *, endpoint: str | None = None):
        with self._lock:
            if self._closed:
                raise RuntimeError("Request model calls were cancelled.")
            if endpoint and endpoint in self._rate_limited_endpoints:
                raise ModelTransportUnavailable(
                    "Model API rate limit (HTTP 429) reached earlier in this request; repeated calls were skipped."
                )
            if endpoint and endpoint in self._failed_endpoints:
                raise ModelTransportUnavailable(
                    "Model connection failed earlier in this request; further calls to the same endpoint were skipped."
                )
            client = self._clients.get(timeout_seconds)
            if client is None:
                client = httpx.Client(timeout=timeout_seconds)
                self._clients[timeout_seconds] = client
            self._active += 1
        try:
            yield client
        except (httpx.TimeoutException, httpx.NetworkError):
            # Do not repeatedly wait through the same transport failure in each
            # review/composition stage. HTTP responses and content/schema errors
            # still take their normal recovery paths. New requests start fresh.
            if endpoint:
                with self._lock:
                    self._failed_endpoints.add(endpoint)
            raise
        except (httpx.HTTPStatusError, RuntimeError) as exc:
            # Text2SQL wraps HTTPStatusError; preserve its original response.
            error = exc if isinstance(exc, httpx.HTTPStatusError) else exc.__cause__
            if endpoint and isinstance(error, httpx.HTTPStatusError) and error.response.status_code == 429:
                with self._lock:
                    self._rate_limited_endpoints.add(endpoint)
            raise
        finally:
            with self._lock:
                self._active -= 1
                closing = self._drain_if_idle()
            _close_clients(closing)

    def close(self):
        with self._lock:
            self._closed = True
            closing = self._drain_if_idle()
        _close_clients(closing)

    def _drain_if_idle(self):
        if not self._closed or self._active:
            return []
        clients = list(self._clients.values())
        self._clients.clear()
        return clients
