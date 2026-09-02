"""Similar-case search sub-agent placeholder."""

from app.schemas.chat import Evidence


def find_similar_cases(query: str, top_k: int = 5) -> list[Evidence]:
    """Find previous FAB incidents that resemble the current issue."""
    return []

