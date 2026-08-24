"""Knowledge retrieval sub-agent placeholder."""

from app.schemas.chat import Evidence


def retrieve_knowledge(query: str, top_k: int = 5) -> list[Evidence]:
    """Retrieve FAB process knowledge, manuals, and historical playbook chunks."""
    raise NotImplementedError("RAG retrieval is not implemented yet.")

