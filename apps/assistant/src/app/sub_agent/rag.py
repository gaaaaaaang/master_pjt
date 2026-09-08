from __future__ import annotations

import re
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
from app.sub_agent.issue_intent import negated_issue_types

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
    issue_intents = _query_issue_intents(_expand_query_terms(_tokenize(query)), query=query)
    issue_intents.update(result.trace.get("plan", {}).get("concepts", []))
    evidence = [
        _to_evidence(chunk, chunk["metadata"]["score"], issue_intents=issue_intents, strict_issue_alignment=True)
        for chunk in result.chunks
    ]
    if not result.trace.get("plan", {}).get("exact_ids"):
        evidence = [item for item in evidence if item.metadata.get("knowledge_base") != INCIDENT_PLAYBOOK or item.metadata["issue_aligned"]]
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


def _to_evidence(
    chunk: dict[str, Any],
    score: float,
    *,
    issue_intents: set[str] | None = None,
    strict_issue_alignment: bool = False,
) -> Evidence:
    metadata = dict(chunk.get("metadata") or {})
    declared_issue_types = _declared_issue_types(chunk)
    matched_issue_types = sorted(
        issue_type
        for issue_type in declared_issue_types
        if any(
            _issue_type_matches(intent, issue_type)
            for intent in (issue_intents or set())
        )
    )
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
            "declared_issue_types": sorted(declared_issue_types),
            "query_issue_intents": sorted(issue_intents or set()),
            "matched_issue_types": matched_issue_types,
            "issue_aligned": _issue_alignment(
                chunk, issue_intents or set(), strict_issue_alignment
            ),
        }
    )
    return Evidence(
        source_type="rag_chunk",
        title=str(chunk.get("title") or "Knowledge chunk"),
        content=str(chunk.get("content") or ""),
        metadata=metadata,
    )


def _declared_issue_types(chunk: dict[str, Any]) -> set[str]:
    metadata = chunk.get("metadata") or {}
    issue_types = {
        value.strip()
        for key in ("issue_type", "issue_types")
        for value in str(metadata.get(key) or "").split(",")
        if value.strip()
    }
    issue_types.update(
        match.group(1).casefold()
        for match in re.finditer(
            r"\bissue_type\s+([a-z][a-z0-9_]*)",
            str(chunk.get("content") or ""),
            flags=re.IGNORECASE,
        )
    )
    return {issue_type.casefold() for issue_type in issue_types}


def _issue_alignment(
    chunk: dict[str, Any],
    issue_intents: set[str],
    strict: bool,
) -> bool:
    if not strict or not issue_intents:
        return True
    declared_issue_types = _declared_issue_types(chunk)
    specific_issue_types = {
        issue_type for issue_type in declared_issue_types if issue_type.casefold() != "all"
    }
    return not specific_issue_types or any(
        _issue_type_matches(intent, issue_type)
        for intent in issue_intents
        for issue_type in specific_issue_types
    )


def _tokenize(text: str) -> set[str]:
    normalized = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", text)
    normalized = normalized.casefold().replace("_", " ")
    tokens = {_normalize_token(token) for token in re.findall(r"[0-9a-z가-힣]{2,}", normalized)}
    return {token for token in tokens if len(token) >= 2 and token not in _STOPWORDS}


def _normalize_token(token: str) -> str:
    if token.endswith("s") and re.fullmatch(r"[a-z]{4,}", token):
        token = token[:-1]
    for suffix in ("에서", "으로", "에게", "까지", "부터", "처럼", "과", "와", "은", "는", "이", "가", "을", "를", "의", "로"):
        if token.endswith(suffix) and len(token) - len(suffix) >= 2:
            return token[: -len(suffix)]
    return token


def _expand_query_terms(terms: set[str]) -> set[str]:
    expanded = set(terms)
    for aliases in _TERM_ALIASES:
        if terms & aliases:
            expanded.update(aliases)
    return expanded


def _query_issue_intents(query_terms: set[str], *, query: str = "") -> set[str]:
    intents = {
        issue_type
        for issue_type, terms in _ISSUE_QUERY_TERMS.items()
        if query_terms & terms
    }
    if query:
        intents -= negated_issue_types(query, _ISSUE_QUERY_TERMS)
    if "pm" in intents:
        intents.discard("equipment_down")
        intents.discard("breakdown")
    return intents


def _issue_type_matches(intent: str, issue_type: str) -> bool:
    normalized = issue_type.casefold()
    return normalized == intent or normalized.startswith(f"{intent}_")


_TERM_ALIASES = (
    {
        "queue", "timelink", "대기", "대기시간", "대기열", "체류", "체류시간",
        "기다림", "웨이팅",
    },
    {"bottleneck", "병목", "starvation", "막힘", "막혀서", "막힌", "포화"},
    {
        "down",
        "breakdown",
        "shutdown",
        "고장",
        "장애",
        "비가동",
        "멈추고",
        "멈춤",
        "정지",
        "중단",
        "멈춘",
        "멈춰서",
        "멈췄어",
        "작동불능",
        "가동불능",
        "가용성",
        "alarm",
        "알람",
        "경보",
    },
    {
        "pm", "maintenance", "정비", "예방정비", "보전", "정기점검",
        "유지보수", "점검일정",
    },
    {
        "delay", "delayed", "지연", "밀림", "밀린", "늦어진", "늦어져",
        "지체",
    },
    {"wip", "재공", "정체", "적체"},
    {"ontime", "납기", "준수율", "납기준수율", "납기성능", "정시"},
    {"cycle", "cycleavg", "사이클", "사이클타임", "리드타임"},
    {"yield", "수율"},
    {"hold", "보류", "격리"},
    {"part", "product", "제품"},
    {"route", "routing", "경로"},
    {"column", "컬럼", "필드"},
    {"report", "보고서"},
)

_ISSUE_QUERY_TERMS = {
    "queue_time": {
        "queue", "timelink", "대기", "대기시간", "대기열", "체류", "체류시간",
        "기다림", "웨이팅",
    },
    "bottleneck": {"bottleneck", "병목", "starvation", "막힘", "막혀서", "막힌", "포화"},
    "equipment_down": {
        "down",
        "shutdown",
        "고장",
        "장애",
        "비가동",
        "멈추고",
        "멈춤",
        "정지",
        "중단",
        "멈춘",
        "멈춰서",
        "멈췄어",
        "작동불능",
        "가동불능",
        "가용성",
        "alarm",
        "알람",
        "경보",
    },
    "breakdown": {"breakdown", "고장", "장애"},
    "pm": {
        "pm", "maintenance", "정비", "예방정비", "보전", "정기점검",
        "유지보수", "점검일정",
    },
    "yield": {"yield", "수율"},
    "lot_hold": {"hold", "보류", "격리"},
}


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
