from __future__ import annotations

import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.rag.milvus_store import search_chunks
from app.schemas.chat import Evidence
from app.sub_agent.issue_intent import negated_issue_types

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

    query_terms = _expand_query_terms(_tokenize(query))
    issue_intents = _query_issue_intents(query_terms, query=query)
    strict_issue_alignment = knowledge_base == INCIDENT_PLAYBOOK
    settings = get_settings()
    if settings.vector_db_url and store_path is None:
        searched_chunks = search_chunks(
            query,
            knowledge_base=knowledge_base,
            top_k=top_k,
            uri=settings.vector_db_url,
            collection_name=settings.vector_db_collection,
        )
        if not searched_chunks:
            raise NotImplementedError(
                f"Milvus RAG store has no chunks for knowledge_base={knowledge_base}: "
                f"{settings.vector_db_collection}"
            )
        return [
            _to_evidence(
                chunk,
                float((chunk.get("metadata") or {}).get("score", 0.0)),
                issue_intents=issue_intents,
                strict_issue_alignment=strict_issue_alignment,
            )
            for chunk in searched_chunks
            if _issue_alignment(chunk, issue_intents, strict_issue_alignment)
        ]

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

    chunk_terms = [_chunk_terms(chunk) for chunk in chunks]
    document_frequency = Counter(
        term for terms in chunk_terms for term in terms
    )
    ranked = sorted(
        (
            (
                _score_chunk(
                    query_terms,
                    chunk,
                    terms,
                    document_frequency=document_frequency,
                    corpus_size=len(chunks),
                    issue_intents=issue_intents,
                    strict_issue_alignment=strict_issue_alignment,
                ),
                chunk,
            )
            for chunk, terms in zip(chunks, chunk_terms, strict=True)
        ),
        key=lambda item: (item[0], str(item[1].get("chunk_id") or "")),
        reverse=True,
    )
    evidence = _dedupe_evidence(
        [
            _to_evidence(
                chunk,
                score,
                issue_intents=issue_intents,
                strict_issue_alignment=strict_issue_alignment,
            )
            for score, chunk in ranked
            if score > 0
        ]
    )[:top_k]
    return evidence


def _select_knowledge_base(query: str) -> str:
    terms = _expand_query_terms(_tokenize(query))
    negated_issues = negated_issue_types(query, _ISSUE_QUERY_TERMS)
    negated_terms = set().union(
        *(_ISSUE_QUERY_TERMS[issue_type] for issue_type in negated_issues)
    ) if negated_issues else set()
    effective_terms = terms - negated_terms
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
        "ontime",
        "maintenance",
        "pm",
        "고장",
        "알람",
        "경보",
        "멈추고",
        "멈춤",
        "정지",
        "비가동",
        "대응",
        "병목",
        "영향",
        "위기",
        "장애",
        "조치",
        "증가",
        "악화",
        "예방정비",
        "정비",
        "납기",
    }
    if effective_terms & incident_terms:
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


def _score_chunk(
    query_terms: set[str],
    chunk: dict[str, Any],
    haystack_terms: set[str],
    *,
    document_frequency: Counter[str],
    corpus_size: int,
    issue_intents: set[str],
    strict_issue_alignment: bool,
) -> float:
    metadata = chunk.get("metadata") or {}
    if not query_terms or not haystack_terms:
        return 0.0

    overlap = query_terms & haystack_terms
    idf_total = sum(_idf(term, document_frequency, corpus_size) for term in query_terms)
    lexical_coverage = (
        sum(_idf(term, document_frequency, corpus_size) for term in overlap) / idf_total
        if idf_total
        else 0.0
    )
    metadata_terms = _expand_query_terms(_tokenize(" ".join(map(str, metadata.values()))))
    metadata_coverage = len(query_terms & metadata_terms) / len(query_terms)
    declared_issue_types = _declared_issue_types(chunk)
    issue_match = any(
        _issue_type_matches(intent, issue_type)
        for intent in issue_intents
        for issue_type in declared_issue_types
    )
    if not _issue_alignment(chunk, issue_intents, strict_issue_alignment):
        return 0.0
    identifier_terms = {term for term in query_terms if any(char.isdigit() for char in term)}
    semantic_overlap = overlap - identifier_terms
    if not semantic_overlap and not issue_match:
        return 0.0
    identifier_coverage = (
        len(identifier_terms & haystack_terms) / len(identifier_terms) if identifier_terms else 0.0
    )
    score = (
        lexical_coverage * 2.0
        + metadata_coverage * 0.5
        + identifier_coverage * 0.4
        + (1.25 if issue_match else 0.0)
    )
    if _looks_like_table_of_contents(str(chunk.get("content") or "")):
        score *= 0.45
    return score


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


def _chunk_terms(chunk: dict[str, Any]) -> set[str]:
    metadata = chunk.get("metadata") or {}
    return _tokenize(
        f"{chunk.get('title', '')} {chunk.get('content', '')} "
        f"{' '.join(map(str, metadata.values()))}"
    )


def _idf(term: str, document_frequency: Counter[str], corpus_size: int) -> float:
    return math.log((corpus_size + 1) / (document_frequency[term] + 1)) + 1.0


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


def _dedupe_evidence(items: list[Evidence]) -> list[Evidence]:
    seen = set()
    output = []
    for item in items:
        key = item.metadata.get("chunk_id") or (item.title, item.content)
        if key in seen:
            continue
        seen.add(key)
        output.append(item)
    return output


def _looks_like_table_of_contents(content: str) -> bool:
    dotted_lines = sum(1 for line in content.splitlines() if re.search(r"\.{8,}\s*\d+", line))
    return dotted_lines >= 3


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
