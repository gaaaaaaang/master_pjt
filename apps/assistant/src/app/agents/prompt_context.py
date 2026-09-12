"""Omit duplicated audit payloads from model input, preserving factual evidence."""
from __future__ import annotations

from typing import Any

AUDIT_FIELDS = frozenset({
    "retrieval_trace", "generation_attempts", "prompt_contract", "prompt_version",
    "handoff",  # Historical copies; the current request/evidence are supplied separately.
})


def compact_prompt_data(value: Any, *, _parent_key: str | None = None) -> Any:
    if isinstance(value, dict):
        return {
            key: compact_prompt_data(item, _parent_key=key)
            for key, item in value.items()
            if key not in AUDIT_FIELDS
            # Impact preserves its incoming handoff under scenario for audit.
            # Current scope, evidence, numeric inputs and formulae are supplied
            # independently; the historical copy need not be sent again.
            and not (_parent_key == "scenario" and key == "execution_context")
        }
    if isinstance(value, list):
        return [compact_prompt_data(item, _parent_key=_parent_key) for item in value]
    if isinstance(value, tuple):
        return [compact_prompt_data(item, _parent_key=_parent_key) for item in value]
    return value
