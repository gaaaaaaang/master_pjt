"""Shared issue-intent guards for retrieval agents."""

from __future__ import annotations

import re


def negated_issue_types(
    query: str,
    issue_terms: dict[str, set[str]],
) -> set[str]:
    """Return issue types whose every explicit mention is locally negated."""
    normalized = query.casefold().replace("_", " ")
    negated: set[str] = set()
    for issue_type, terms in issue_terms.items():
        mentions = [
            match.span()
            for term in sorted(terms, key=len, reverse=True)
            for match in _term_matches(normalized, term.casefold().replace("_", " "))
        ]
        if mentions and all(_mention_is_negated(normalized, span) for span in mentions):
            negated.add(issue_type)
    return negated


def _term_matches(text: str, term: str) -> list[re.Match[str]]:
    if re.fullmatch(r"[a-z0-9 ]+", term):
        pattern = rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])"
    else:
        pattern = re.escape(term)
    return list(re.finditer(pattern, text))


def _mention_is_negated(text: str, span: tuple[int, int]) -> bool:
    before = text[max(0, span[0] - 28) : span[0]]
    after = text[span[1] : min(len(text), span[1] + 24)]
    english_before = re.search(r"\b(?:no|not|without)\b[^,.!?;]{0,22}$", before)
    english_after = re.match(r"\s+(?:is|was|are|were)?\s*not\b", after)
    korean_after = re.match(
        r"\s*(?:상태|문제|원인|이슈)?\s*(?:은|는|이|가|도)?\s*(?:아니|없)",
        after,
    )
    return bool(english_before or english_after or korean_after)
