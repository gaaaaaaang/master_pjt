from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.rag.milvus_store import search_chunks
from app.schemas.chat import Evidence

INCIDENT_PLAYBOOK = "incident_playbook"
PROCESS_BASICS = "process_basics"


def retrieve_knowledge(
    query: str,
    top_k: int = 5,
    *,
    knowledge_base: str | None = None,
    store_path: Path | None = None,
) -> list[Evidence]:
    """Route a general RAG request to the relevant FAB knowledge base."""
    selected_base = knowledge_base or _select_knowledge_base(query)
    return _retrieve_from_store(query, top_k, knowledge_base=selected_base, store_path=store_path)


def retrieve_incident_playbook(
    query: str,
    top_k: int = 5,
    *,
    store_path: Path | None = None,
) -> list[Evidence]:
    """RAG agent for incident response manuals and operational playbooks."""
    return _retrieve_from_store(
        query,
        top_k,
        knowledge_base=INCIDENT_PLAYBOOK,
        store_path=store_path,
    )


def retrieve_process_basics(
    query: str,
    top_k: int = 5,
    *,
    store_path: Path | None = None,
) -> list[Evidence]:
    """RAG agent for semiconductor process basics and general reference material."""
    return _retrieve_from_store(
        query,
        top_k,
        knowledge_base=PROCESS_BASICS,
        store_path=store_path,
    )


def _retrieve_from_store(
    query: str,
    top_k: int,
    *,
    knowledge_base: str,
    store_path: Path | None = None,
) -> list[Evidence]:
    if top_k <= 0:
        return []

    settings = get_settings()
    if settings.vector_db_url and store_path is None:
        evidence = [
            _to_evidence(chunk, float((chunk.get("metadata") or {}).get("score", 0.0)))
            for chunk in search_chunks(
                query,
                knowledge_base=knowledge_base,
                top_k=top_k,
                uri=settings.vector_db_url,
                collection_name=settings.vector_db_collection,
            )
        ]
        if not evidence:
            raise NotImplementedError(
                f"Milvus RAG store has no chunks for knowledge_base={knowledge_base}: "
                f"{settings.vector_db_collection}"
            )
        return evidence

    store_path = store_path or Path(settings.rag_local_store_path)
    chunks = [
        chunk
        for chunk in _load_local_chunks(store_path)
        if str(chunk.get("knowledge_base") or "") == knowledge_base
    ]
    if not chunks:
        raise NotImplementedError(
            f"RAG store has no chunks for knowledge_base={knowledge_base}: {store_path}"
        )

    query_terms = _tokenize(query)
    ranked = sorted(
        ((_score_chunk(query_terms, chunk), chunk) for chunk in chunks),
        key=lambda item: item[0],
        reverse=True,
    )
    evidence = [_to_evidence(chunk, score) for score, chunk in ranked[:top_k] if score > 0]
    if not evidence and ranked:
        evidence = [_to_evidence(ranked[0][1], ranked[0][0])]
    return evidence


def _select_knowledge_base(query: str) -> str:
    terms = _tokenize(query)
    incident_terms = {
        "alarm",
        "breakdown",
        "down",
        "hold",
        "impact",
        "queue",
        "rca",
        "time",
        "wip",
        "고장",
        "대응",
        "병목",
        "영향",
        "위기",
        "장애",
        "조치",
        "증가",
    }
    if terms & incident_terms:
        return INCIDENT_PLAYBOOK
    return PROCESS_BASICS


def _load_local_chunks(store_path: Path) -> list[dict[str, Any]]:
    if not store_path.exists():
        return []
    chunks: list[dict[str, Any]] = []
    with store_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                chunks.append(json.loads(line))
    return chunks


def _score_chunk(query_terms: set[str], chunk: dict[str, Any]) -> float:
    content = str(chunk.get("content") or "")
    title = str(chunk.get("title") or "")
    metadata = chunk.get("metadata") or {}
    haystack_terms = _tokenize(f"{title} {content} {' '.join(map(str, metadata.values()))}")
    if not query_terms or not haystack_terms:
        return 0.0

    overlap = query_terms & haystack_terms
    title_overlap = query_terms & _tokenize(title)
    metadata_overlap = query_terms & _tokenize(" ".join(map(str, metadata.values())))
    return (
        len(overlap) / math.sqrt(len(haystack_terms))
        + len(title_overlap) * 0.5
        + len(metadata_overlap) * 0.25
    )


def _to_evidence(chunk: dict[str, Any], score: float) -> Evidence:
    metadata = dict(chunk.get("metadata") or {})
    source = str(chunk.get("source") or "")
    source_document = str(metadata.get("source_document") or "")
    if not source_document and source:
        source_document = Path(source).name
    metadata.update(
        {
            "chunk_id": str(chunk.get("chunk_id") or ""),
            "collection": str(chunk.get("collection") or ""),
            "knowledge_base": str(chunk.get("knowledge_base") or ""),
            "source": source,
            "source_document": source_document,
            "score": round(score, 4),
        }
    )
    return Evidence(
        source_type="rag_chunk",
        title=str(chunk.get("title") or "Knowledge chunk"),
        content=str(chunk.get("content") or ""),
        metadata=metadata,
    )


def _tokenize(text: str) -> set[str]:
    normalized = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", text)
    normalized = normalized.casefold().replace("_", " ")
    return {token for token in re.findall(r"[0-9a-z가-힣]{2,}", normalized) if token not in _STOPWORDS}


_STOPWORDS = {
    "and",
    "for",
    "from",
    "that",
    "the",
    "this",
    "with",
    "공정",
    "관련",
    "기준",
    "라인",
    "질문",
    "확인",
}
