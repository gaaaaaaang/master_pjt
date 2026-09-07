"""Request-scoped model-call accounting; never stores prompts, document text or keys."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from threading import Event, Lock
from time import perf_counter
from typing import Any


@dataclass
class UsageLedger:
    calls: list[dict[str, Any]] = field(default_factory=list)
    _lock: Lock = field(default_factory=Lock, repr=False)
    _cancelled: Event = field(default_factory=Event, repr=False)

    def cancel(self) -> None:
        """Prevent subsequent model calls; an already sent request may still finish."""
        self._cancelled.set()

    def ensure_active(self) -> None:
        if self._cancelled.is_set():
            raise RuntimeError("Request model calls were cancelled.")

    def append(self, record: dict[str, Any]) -> None:
        with self._lock:
            self.calls.append(dict(record))

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            calls = [dict(call) for call in self.calls]
        return {
            "call_count": len(calls),
            "failed_calls": sum(not c["succeeded"] for c in calls),
            "reported_total_tokens": sum(c.get("total_tokens") or 0 for c in calls),
            "unreported_usage_calls": sum(c.get("total_tokens") is None for c in calls),
            "calls": calls,
        }


_ledger: ContextVar[UsageLedger | None] = ContextVar("fab_usage_ledger", default=None)
_record: ContextVar[dict[str, Any] | None] = ContextVar("fab_usage_call", default=None)


@contextmanager
def usage_scope(ledger: UsageLedger):
    token = _ledger.set(ledger)
    try:
        yield
    finally:
        _ledger.reset(token)


@contextmanager
def model_call(kind: str, model: str):
    ledger = _ledger.get()
    if ledger is None:
        yield
        return
    ledger.ensure_active()
    record = {
        "kind": kind,
        "model": model,
        "succeeded": False,
        "input_tokens": None,
        "output_tokens": None,
        "total_tokens": None,
    }
    token = _record.set(record)
    start = perf_counter()
    try:
        yield
        record["succeeded"] = True
    except Exception as exc:
        record["error_type"] = type(exc).__name__
        raise
    finally:
        record["seconds"] = round(perf_counter() - start, 3)
        ledger.append(record)
        _record.reset(token)


def reported_usage(usage: Any) -> None:
    record = _record.get()
    if record is None or not isinstance(usage, dict):
        return
    for source, target in (
        ("prompt_tokens", "input_tokens"),
        ("completion_tokens", "output_tokens"),
        ("total_tokens", "total_tokens"),
    ):
        value = usage.get(source)
        if type(value) is int and value >= 0:
            record[target] = value
