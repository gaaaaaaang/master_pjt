"""Retrieval pipeline: query analysis, BM25/dense candidates, RRF and evidence selection.

Local BM25 is an explicit offline mode. Dense retrieval is injected by the adapter;
no synthetic embeddings or scores are substituted for real vector search.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
from time import perf_counter
from typing import Any

import httpx

from app.rag.query import QueryPlan, analyze_query, apply_fab_scope, concepts, tokenize
from app.rag.rerank import Reranker

Chunk = dict[str, Any]
DenseSearch = Callable[[str, str, int], list[Chunk]]


@dataclass
class SearchResult:
    chunks: list[Chunk]
    trace: dict[str, Any]
    limitations: list[str] = field(default_factory=list)


def eligible(chunk: Chunk, plan: QueryPlan) -> bool:
    if chunk.get("knowledge_base") not in plan.knowledge_bases:
        return False
    metadata = chunk.get("metadata") or {}
    if plan.require_verified and metadata.get("reliability") != "verified_sop":
        return False
    # A document explicitly scoped to another FAB is never a valid result.
    scope = metadata.get("fab_ids") or ([metadata["fab_id"]] if metadata.get("fab_id") else [])
    if plan.fab_ids and scope and not set(plan.fab_ids) & set(scope):
        return False
    return metadata.get("status") not in {"withdrawn", "superseded"}


class BM25Index:
    def __init__(self, chunks: list[Chunk]):
        self.chunks = chunks
        self.tokens = [
            Counter(tokenize(str(c.get("title", "")) + " " + str(c.get("content", ""))))
            for c in chunks
        ]
        self.lengths = [sum(t.values()) for t in self.tokens]
        self.average_length = sum(self.lengths) / max(1, len(chunks))
        self.df = Counter(token for tokens in self.tokens for token in tokens)

    def search(self, plan: QueryPlan, limit: int) -> list[tuple[float, Chunk]]:
        query = set(plan.terms)
        scored = []
        for chunk, tokens, length in zip(self.chunks, self.tokens, self.lengths, strict=True):
            if not eligible(chunk, plan):
                continue
            score = 0.0
            for term in query & tokens.keys():
                frequency = tokens[term]
                idf = math.log(1 + (len(self.chunks) - self.df[term] + 0.5) / (self.df[term] + 0.5))
                score += (
                    idf
                    * frequency
                    * 2.2
                    / (frequency + 1.2 * (0.25 + 0.75 * length / max(1, self.average_length)))
                )
            if score > 0:
                scored.append((score, chunk))
        return sorted(scored, key=lambda item: (-item[0], item[1]["chunk_id"]))[:limit]


def rrf(
    rankings: list[list[Chunk]], *, k: int = 60, weights: list[float] | None = None
) -> list[tuple[float, Chunk, dict]]:
    if weights is not None and len(weights) != len(rankings):
        raise ValueError("Each ranking must have one fusion weight.")
    weights = weights or [1.0] * len(rankings)
    if k <= 0 or any(not math.isfinite(w) or w < 0 for w in weights):
        raise ValueError("RRF requires a positive rank constant and finite nonnegative weights.")
    scores: dict[str, float] = {}
    records: dict[str, Chunk] = {}
    ranks: dict[str, dict] = {}
    for list_index, ranking in enumerate(rankings):
        seen = set()
        for rank, chunk in enumerate(ranking, 1):
            cid = chunk["chunk_id"]
            if cid in seen:
                continue
            seen.add(cid)
            records[cid] = chunk
            scores[cid] = scores.get(cid, 0) + weights[list_index] / (k + rank)
            ranks.setdefault(cid, {})[str(list_index)] = rank
    return [
        (scores[cid], records[cid], ranks[cid])
        for cid in sorted(scores, key=lambda key: (-scores[key], key))
    ]


def _rerank(plan: QueryPlan, chunk: Chunk, fusion_score: float) -> tuple[float, dict]:
    content = str(chunk.get("content") or "")
    title = str(chunk.get("title") or "")
    tokens = set(tokenize(title + " " + content))
    wanted = set(plan.terms)
    overlap = wanted & tokens
    document_concepts = concepts(title + " " + content)
    matched = set(plan.concepts) & document_concepts
    focus_text = title + " " + str((chunk.get("metadata") or {}).get("issue_type", ""))
    focus_matches = set(plan.concepts) & concepts(focus_text)
    excluded = set(plan.excluded_concepts) & document_concepts
    exact = bool(plan.exact_ids) and any(cid in content.upper() for cid in plan.exact_ids)
    coverage = len(overlap) / max(1, len(wanted))
    title_overlap = len(wanted & set(tokenize(title))) / max(1, len(wanted))
    noise = any(
        word in title.casefold()
        for word in ("목차", "contents", "예시 레코드", "문서 스키마", "문서 목적", "데이터 소스")
    )
    # This is a transparent feature reranker, not a trained neural relevance model.
    score = (
        fusion_score
        + coverage
        + title_overlap * 0.5
        + len(matched) * 0.2
        + len(focus_matches) * 0.4
        + int(exact) * 3
    )
    score -= len(excluded) * 0.4 + int(noise) * 0.7
    acceptable = bool(overlap) and (not plan.exact_ids or exact)
    if not plan.concepts and not exact and coverage < 0.35:
        acceptable = False
    if plan.concepts and not matched and not exact:
        acceptable = False
    return (score if acceptable else -1), {
        "term_coverage": round(coverage, 4),
        "concept_matches": sorted(matched),
        "focus_matches": sorted(focus_matches),
        "excluded_matches": sorted(excluded),
        "exact_id_match": exact,
        "structural_noise": noise,
        "fusion_score": round(fusion_score, 6),
    }


def search(
    query: str,
    chunks: list[Chunk],
    *,
    knowledge_base: str | None = None,
    fab_id: str | None = None,
    top_k: int = 5,
    candidate_k: int = 30,
    dense_search: DenseSearch | None = None,
    context_chars: int = 14000,
    reranker: Reranker | None = None,
    rerank_limit: int = 12,
) -> SearchResult:
    start = perf_counter()
    if candidate_k < 1 or candidate_k > 200:
        raise ValueError("candidate_k must be between 1 and 200.")
    if context_chars < 1:
        raise ValueError("context_chars must be positive.")
    if not 1 <= rerank_limit <= 40:
        raise ValueError("rerank_limit must be between 1 and 40.")
    plan = apply_fab_scope(analyze_query(query, knowledge_base), fab_id)
    trace: dict[str, Any] = {
        "pipeline_version": "hybrid.v2",
        "plan": asdict(plan),
        "retrieval_mode": "hybrid" if dense_search else "bm25",
        "reranker": "feature.v1",
        "candidate_limit": candidate_k,
    }
    if top_k <= 0 or not query.strip():
        return SearchResult([], trace)
    top_k = min(top_k, 20)
    if re.fullmatch(r"(?:PB-[A-Z]+-\d+[\s,;]*)+", query.strip(), re.IGNORECASE):
        return _exact_lookup(chunks, plan, top_k, context_chars, trace, start)
    index = BM25Index(chunks)
    lexical = index.search(plan, candidate_k)
    rankings = [[chunk for _, chunk in lexical]]
    weights = [1.0]
    channels = ["bm25:original"]
    for number, variant in enumerate(plan.subqueries[1:], 1):
        variant_plan = replace(plan, terms=tuple(tokenize(variant)))
        rankings.append([chunk for _, chunk in index.search(variant_plan, candidate_k)])
        weights.append(0.5)
        channels.append(f"bm25:subquery{number}")
    limitations = []
    dense_count = 0
    if dense_search:
        for base in plan.knowledge_bases:
            try:
                candidates = [
                    c for c in dense_search(query, base, candidate_k) if eligible(c, plan)
                ]
                rankings.append(candidates)
                weights.append(1.0)
                channels.append(f"dense:{base}")
                dense_count += len(candidates)
            except (RuntimeError, OSError, ValueError, TypeError, httpx.HTTPError) as exc:
                trace.setdefault("dense_errors", []).append(type(exc).__name__)
                limitations.append("벡터 검색 실패로 로컬 키워드 검색 결과만 사용했습니다.")
        if trace.get("dense_errors"):
            trace["retrieval_mode"] = "hybrid_degraded"
    fused = rrf(rankings, weights=weights)[:candidate_k]
    trace["retrieval_channels"] = channels
    scored = []
    for fusion_score, chunk, ranks in fused:
        score, features = _rerank(plan, chunk, fusion_score)
        scored.append((score, chunk, features, ranks))
    scored.sort(key=lambda item: (-item[0], -item[2]["fusion_score"], item[1]["chunk_id"]))
    reranked = [item for item in scored if item[0] > 0]
    if reranker and scored:
        # Dense-only candidates remain eligible for model judgement even without lexical overlap.
        candidates = scored[:rerank_limit]
        try:
            judged = reranker.rank(query, [item[1] for item in candidates])
            grades = {item.chunk_id: item for item in judged}
            if set(grades) != {item[1]["chunk_id"] for item in candidates}:
                raise ValueError("Reranker returned incomplete candidate grades.")
            reranked = []
            for score, chunk, features, ranks in candidates:
                judgement = grades[chunk["chunk_id"]]
                features.update(
                    {"relevance_grade": judgement.grade, "relevance_reason": judgement.reason}
                )
                if judgement.grade >= 2:
                    reranked.append((judgement.grade + max(0, score) * 0.1, chunk, features, ranks))
            trace["reranker"] = "llm.v1"
            trace["reranked_count"] = len(candidates)
        except (RuntimeError, OSError, ValueError, TypeError, httpx.HTTPError) as exc:
            trace["reranker"] = "feature.v1_fallback"
            trace["reranker_error"] = type(exc).__name__
            limitations.append("리랭커 호출 실패로 규칙 기반 정렬 결과를 사용했습니다.")
    reranked.sort(key=lambda item: (-item[0], item[1]["chunk_id"]))
    selected = []
    seen_content = set()
    covered = set()
    used_chars = 0
    budget_dropped = 0
    # Greedy coverage makes complementary evidence competitive with repeated matches.
    while reranked and len(selected) < top_k:
        best = max(
            range(len(reranked)),
            key=lambda i: (
                reranked[i][0]
                + 0.35
                * len(
                    set(reranked[i][2]["focus_matches"] or reranked[i][2]["concept_matches"])
                    - covered
                )
            ),
        )
        score, chunk, features, ranks = reranked.pop(best)
        content = str(chunk.get("content") or "")
        fingerprint = " ".join(content.split()).casefold()
        if fingerprint in seen_content:
            continue
        if used_chars + len(content) > context_chars:
            budget_dropped += 1
            continue
        seen_content.add(fingerprint)
        used_chars += len(content)
        # Focus controls diversity, but supporting body text can cover another requested topic.
        covered.update(features["concept_matches"])
        metadata = dict(chunk.get("metadata") or {})
        metadata.update(
            {
                "score": round(score, 6),
                "retrieval_features": features,
                "retrieval_ranks": ranks,
                "retrieval_mode": trace["retrieval_mode"],
            }
        )
        selected.append({**chunk, "metadata": metadata})
    trace.update(
        {
            "lexical_candidates": len(lexical),
            "dense_candidates": dense_count,
            "fused_candidates": len(fused),
            "selected_count": len(selected),
            "context_chars": used_chars,
            "budget_dropped_count": budget_dropped,
            "covered_concepts": sorted(covered),
            "uncovered_concepts": sorted(set(plan.concepts) - covered),
            "latency_ms": round((perf_counter() - start) * 1000, 3),
        }
    )
    if not selected:
        limitations.append("질문을 뒷받침하는 문서 근거를 찾지 못했습니다.")
    if budget_dropped:
        limitations.append("검색된 일부 원문이 길어 답변 근거에 모두 포함하지 못했습니다.")
    if trace["uncovered_concepts"]:
        limitations.append(
            "질문의 일부 주제에 대한 문서 근거가 부족합니다: "
            + ", ".join(trace["uncovered_concepts"])
        )
    return SearchResult(selected, trace, list(dict.fromkeys(limitations)))


def _exact_lookup(chunks, plan, top_k, context_chars, trace, start):
    """Pure procedure IDs need an identifier lookup, not embedding or LLM judgement."""
    selected, found, used = [], set(), 0
    for wanted in plan.exact_ids:
        for chunk in chunks:
            primary = re.findall(
                r"playbook_id\s+(PB-[A-Z]+-\d+)", chunk.get("content", ""), re.IGNORECASE
            )
            if wanted not in {p.upper() for p in primary} or not eligible(chunk, plan):
                continue
            if len(selected) >= top_k or used + len(chunk["content"]) > context_chars:
                continue
            if chunk["chunk_id"] in {c["chunk_id"] for c in selected}:
                continue
            found.add(wanted)
            used += len(chunk["content"])
            selected.append(
                {
                    **chunk,
                    "metadata": {
                        **(chunk.get("metadata") or {}),
                        "score": 1.0,
                        "retrieval_mode": "exact_id",
                        "retrieval_features": {"exact_id_match": True},
                        "retrieval_ranks": {},
                    },
                }
            )
    missing = sorted(set(plan.exact_ids) - found)
    trace.update(
        retrieval_mode="exact_id",
        reranker="none",
        retrieval_channels=["primary_playbook_id"],
        selected_count=len(selected),
        context_chars=used,
        missing_exact_ids=missing,
        latency_ms=round((perf_counter() - start) * 1000, 3),
    )
    limitations = (
        ["요청한 절차 ID의 원문 근거를 제공하지 못했습니다: " + ", ".join(missing)]
        if missing
        else []
    )
    return SearchResult(selected, trace, limitations)
