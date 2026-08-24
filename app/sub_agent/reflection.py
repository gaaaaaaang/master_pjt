"""Self-check sub-agent placeholder."""

from typing import Any


def verify_response(answer: str, evidence: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Check whether an answer is supported by evidence and within safety boundaries."""
    return {
        "is_supported": bool(answer),
        "warnings": [],
        "evidence_count": len(evidence or []),
    }

