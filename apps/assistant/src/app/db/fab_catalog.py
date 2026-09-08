"""Shared FAB resolution and physical table naming contracts.

Patterns are catalog identifiers, never SQL text. Bind them before SQL generation.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

ALLOWED_FABS = frozenset({"fab10", "fab11", "fab12", "fab13"})
IDENTIFIER = re.compile(r"[a-z][a-z0-9_]*\Z")
FAB_MENTION = re.compile(
    r"(?<![A-Za-z0-9])(?:fab|팹|패브|M)[\s_-]*(\d+)(?![A-Za-z0-9])"
    r"|(?<![A-Za-z0-9])(\d+)\s*번?\s*(?:팹|패브|fab)(?![A-Za-z0-9])",
    re.IGNORECASE,
)


def mentioned_fabs(text: str) -> list[str]:
    return list(dict.fromkeys(
        f"fab{match.group(1) or match.group(2)}" for match in FAB_MENTION.finditer(text)
        if not re.match(
            r"\s*(?:이|가|은|는|을|를|에서)?\s*(?:말고|아니라|아니고|아닌|제외|빼고)",
            text[match.end():],
        )
    ))


def normalize_fab(value: str | None) -> str | None:
    if not value:
        return None
    raw = value.strip().lower()
    if raw in {"10", "11", "12", "13"}:
        return f"fab{raw}"
    matches = mentioned_fabs(raw)
    if len(matches) == 1 and matches[0] in ALLOWED_FABS and FAB_MENTION.fullmatch(raw):
        return matches[0]
    return None


@dataclass(frozen=True)
class FabResolution:
    fab_id: str | None
    source: str
    raw_text: str
    clarification: str | None = None


def resolve_fab(
    question: str,
    request_fab: str | None = None,
    history: list[dict[str, Any]] | None = None,
) -> FabResolution:
    explicit = mentioned_fabs(question)
    if explicit or FAB_MENTION.search(question):
        if len(explicit) != 1 or explicit[0] not in ALLOWED_FABS:
            return FabResolution(None, "explicit_user", question,
                                 "조회할 FAB 하나를 fab10, fab11, fab12, fab13 중 지정해주세요.")
        return FabResolution(explicit[0], "explicit_user", question)
    if request_fab is not None:
        resolved = normalize_fab(request_fab)
        return FabResolution(resolved, "request_context", request_fab,
                             None if resolved else "요청 FAB을 fab10~fab13 중 하나로 지정해주세요.")
    # Only user turns can establish a scope; assistant examples must not select a FAB.
    for turn in reversed((history or [])[-12:]):
        if turn.get("role") != "user":
            continue
        content = str(turn.get("content") or "")
        candidates = mentioned_fabs(content)
        if candidates:
            prior = resolve_fab(content)
            return FabResolution(prior.fab_id, "conversation_context", content,
                                 prior.clarification)
        metadata = turn.get("metadata")
        if isinstance(metadata, dict) and metadata.get("fab"):
            prior = resolve_fab("", str(metadata["fab"]))
            return FabResolution(prior.fab_id, "conversation_context", str(metadata["fab"]),
                                 prior.clarification)
    return FabResolution(None, "missing", "", "어느 FAB을 조회할까요? fab10~fab13 중 지정해주세요.")


def table_pattern(logical_table: str) -> str:
    if not IDENTIFIER.fullmatch(logical_table) or re.search(r"_fab\d+$", logical_table):
        raise ValueError(f"Expected a logical table name: {logical_table!r}")
    return "{fab}." + logical_table + "_{fab}"


def bind_table_pattern(pattern: str, fab_id: str) -> str:
    if fab_id not in ALLOWED_FABS:
        raise ValueError(f"Unsupported FAB: {fab_id!r}")
    match = re.fullmatch(r"\{fab\}\.([a-z][a-z0-9_]*)_\{fab\}", pattern)
    if not match or table_pattern(match.group(1)) != pattern:
        raise ValueError("Invalid catalog table pattern")
    bound = pattern.replace("{fab}", fab_id)
    if len(bound.split(".")[1].encode()) > 63:
        raise ValueError("Physical table identifier exceeds PostgreSQL's 63-byte limit")
    return bound


def table_ref(fab_id: str, logical_table: str) -> str:
    return bind_table_pattern(table_pattern(logical_table), fab_id)


def physical_table_name(fab_id: str, logical_table: str) -> str:
    return table_ref(fab_id, logical_table).split(".")[1]
