"""Explainable metadata ranking; retrieval is a hint, never a DB permission policy."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.db.semantic_metadata import enrich


@dataclass(frozen=True)
class RankedTable:
    table_ref: str
    score: float
    reasons: tuple[str, ...]
    explicit: bool


def _match(term: str, question: str) -> bool:
    term = term.casefold().strip()
    if not term:
        return False
    if re.fullmatch(r"[a-z0-9_.]+", term):
        return bool(re.search(r"(?<![a-z0-9_])" + re.escape(term) + r"(?![a-z0-9_])", question))
    return term in question


def mentions_table(question: str, table_ref: str, logical_table: str) -> bool:
    """Match table identifiers, never fragments such as PM inside equipment_id."""
    q = question.casefold()
    return any(_match(term, q) for term in (logical_table, table_ref, table_ref.rsplit('.', 1)[-1]))


def rank_tables(question: str, catalog: dict[str, dict[str, Any]], *,
                primary_refs: list[str] | None = None) -> list[RankedTable]:
    q = question.casefold()
    primary = set(primary_refs or [])
    tokens = {token for token in re.findall(r"[a-z][a-z0-9_]+|[가-힣]{2,}", q)
              if token not in {"보여줘", "알려줘", "조회", "목록", "fab10", "fab11", "fab12", "fab13"}}
    ranked = []
    for ref, raw in sorted(catalog.items()):
        entry = enrich(raw)
        logical = entry["logical_table"]
        reasons = []
        score = 0.0
        explicit = mentions_table(q, ref, logical)
        if explicit:
            score += 100
            reasons.append("explicit_table")
        alias_hits = [alias for alias in entry.get("aliases", []) if _match(alias, q)]
        if alias_hits:
            score += min(len(alias_hits), 3) * 3
            reasons.append("aliases:" + ",".join(alias_hits))
        column_hits = [column["name"] for column in entry["columns"] if _match(column["name"], q)]
        if column_hits:
            score += min(len(column_hits), 4) * 2
            reasons.append("columns:" + ",".join(column_hits))
        description = entry.get("description", "").casefold()
        description_hits = sorted(token for token in tokens if token in description)
        if description_hits:
            score += min(len(description_hits), 3)
            reasons.append("description:" + ",".join(description_hits))
        if ref in primary:
            score += 12
            reasons.append("request_slots")
        # Code-grounded metadata is only a modest preference, not a claim of SSOT.
        weight = entry.get("semantics", {}).get("trust_weight", 1)
        weight = min(max(float(weight) if isinstance(weight, (int, float)) else 1, 1), 4)
        score *= weight
        ranked.append(RankedTable(ref, score, tuple(reasons), explicit))
    return sorted(ranked, key=lambda item: (-item.explicit, -item.score, item.table_ref))


def select_catalog(question: str, catalog: dict[str, dict[str, Any]], *,
                   primary_refs: list[str] | None = None, max_tables: int = 6
                   ) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    if max_tables < 1:
        raise ValueError("max_tables must be positive")
    ranked = rank_tables(question, catalog, primary_refs=primary_refs)
    positive = [entry for entry in ranked if entry.score > 0]
    # No evidence of relevance: broaden instead of silently guessing an arbitrary subset.
    chosen = ranked if not positive else positive[:max_tables]
    must_keep = {entry.table_ref for entry in ranked if entry.explicit} | set(primary_refs or [])
    refs = {entry.table_ref for entry in chosen} | (must_keep & catalog.keys())
    for ref in list(refs):
        for relation in catalog[ref].get("relationships", []):
            target = relation.get("target_table_ref")
            if target in catalog:
                refs.add(target)
    selected = {entry.table_ref: enrich(catalog[entry.table_ref]) for entry in ranked if entry.table_ref in refs}
    details = {
        "version": "metadata-ranking-v2",
        "total_tables": len(catalog), "selected_tables": len(selected),
        "total_columns": sum(len(entry["columns"]) for entry in catalog.values()),
        "selected_columns": sum(len(entry["columns"]) for entry in selected.values()),
        "broadened_no_match": not positive,
        "ranking": [{"table_ref": entry.table_ref, "score": entry.score,
                     "reasons": list(entry.reasons), "selected": entry.table_ref in refs}
                    for entry in ranked],
    }
    return selected, details
