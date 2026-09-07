from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.rag.embeddings import AzureEmbeddingClient, RequestEmbeddingCache
from app.rag.ingest import load_chunks
from app.rag.manifest import load_manifest, verify_manifest
from app.rag.milvus_store import search_chunks
from app.rag.rerank import AzureReranker
from app.rag.search import SearchResult, search
from app.schemas.chat import Evidence

INCIDENT_PLAYBOOK = "incident_playbook"
PROCESS_BASICS = "process_basics"


@dataclass
class EvidenceResult:
    evidence: list[Evidence]
    trace: dict[str, Any]
    limitations: list[str]


def retrieve_knowledge(
    query: str,
    top_k: int = 5,
    *,
    knowledge_base: str | None = None,
    fab_id: str | None = None,
    store_path: Path | None = None,
) -> list[Evidence]:
    """Route a general RAG request to the relevant FAB knowledge base."""
    return retrieve_evidence(
        query, top_k, knowledge_base=knowledge_base, fab_id=fab_id, store_path=store_path
    ).evidence


def retrieve_evidence(
    query: str,
    top_k: int = 5,
    *,
    knowledge_base: str | None = None,
    fab_id: str | None = None,
    store_path: Path | None = None,
) -> EvidenceResult:
    """Keep trace and limitations available even when retrieval returns no evidence."""
    result = retrieve_with_trace(
        query, top_k, knowledge_base=knowledge_base, fab_id=fab_id, store_path=store_path
    )
    evidence = [_to_evidence(chunk, chunk["metadata"]["score"]) for chunk in result.chunks]
    for item in evidence:
        item.metadata["retrieval_trace"] = result.trace
        item.metadata["retrieval_limitations"] = result.limitations
    return EvidenceResult(evidence, result.trace, result.limitations)


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
    return retrieve_knowledge(query, top_k, knowledge_base=knowledge_base, store_path=store_path)


def retrieve_with_trace(
    query: str,
    top_k: int = 5,
    *,
    knowledge_base: str | None = None,
    fab_id: str | None = None,
    store_path: Path | None = None,
) -> SearchResult:
    settings = get_settings()
    selected_path = store_path or Path(settings.rag_local_store_path)
    chunks = _load_local_chunks(selected_path)
    use_dense = bool(settings.vector_db_url) and store_path is None
    if not chunks and not use_dense and top_k > 0:
        raise NotImplementedError(f"RAG store has no chunks: {selected_path}")

    local_by_id = {chunk["chunk_id"]: chunk for chunk in chunks}
    embedding_client = RequestEmbeddingCache(AzureEmbeddingClient()) if use_dense else None

    def dense(query: str, base: str, limit: int):
        if not settings.rag_index_manifest_path:
            raise RuntimeError("A serving index manifest is required for hybrid search.")
        manifest = load_manifest(Path(settings.rag_index_manifest_path))
        verify_manifest(
            manifest,
            chunks,
            embedding_model=settings.embedding_model,
            embedding_revision=settings.embedding_revision,
            dimension=settings.embedding_dimension,
        )
        candidates = search_chunks(
            query,
            knowledge_base=base,
            top_k=limit,
            uri=settings.vector_db_url,
            collection_name=settings.vector_db_collection,
            index_version=manifest.index_version,
            dimension=settings.embedding_dimension,
            embedding_client=embedding_client,
        )
        result = []
        for candidate in candidates:
            original = local_by_id.get(candidate["chunk_id"])
            if original is None or original["knowledge_base"] != base:
                continue
            if candidate["content"] != original["content"]:
                continue
            result.append(
                {
                    **original,
                    "metadata": {
                        **original.get("metadata", {}),
                        "dense_score": candidate["metadata"].get("score"),
                    },
                }
            )
        return result

    return search(
        query,
        chunks,
        knowledge_base=knowledge_base,
        fab_id=fab_id,
        top_k=top_k,
        candidate_k=settings.rag_candidate_k,
        context_chars=settings.rag_context_chars,
        dense_search=dense if use_dense else None,
        reranker=AzureReranker() if settings.rag_reranker == "llm" and store_path is None else None,
        rerank_limit=settings.rag_rerank_limit,
    )


def _load_local_chunks(store_path: Path) -> list[dict[str, Any]]:
    return load_chunks(store_path) if store_path.exists() else []


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
