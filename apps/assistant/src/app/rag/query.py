"""Bounded, inspectable FAB query analysis shared by sparse and dense retrieval."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from functools import lru_cache

# Versioned domain vocabulary: aliases describe terminology, never causal relations.
VOCABULARY_VERSION = "fab.v2"
ALIASES = {
    "equipment_down": (
        "설비 고장",
        "장비 고장",
        "설비고장",
        "장비고장",
        "breakdown",
        "equipment down",
        "다운타임",
        "고장",
    ),
    "pm": (
        "정기 보전",
        "예방 보전",
        "예방정비",
        "예방 정비",
        "정기 점검",
        "preventive maintenance",
        "pm",
    ),
    "bottleneck": ("병목", "bottleneck"),
    "queue_time": ("대기시간", "대기 시간", "queue time", "queue_time", "q-time", "qtime"),
    "wip": ("재공", "재공품", "wip"),
    "yield": ("수율", "yield", "불량률", "pass/fail 비율"),
    "spc": ("관리도", "control chart", "관리한계", "관리 한계", "spc", "out-of-control"),
    "lot_hold": ("lot hold", "로트 보류", "로트보류", "격리", "보류"),
    "lot_release": (
        "로트를 해제",
        "release from hold",
        "hold 해제",
        "보류 해제",
        "로트 해제",
        "lot release",
        "release 판단",
    ),
    "material_shortage": ("자재 부족", "자재부족", "material shortage"),
    "hot_lot": ("핫랏", "긴급 오더", "긴급오더", "hot lot", "hotlot", "rush order"),
    "dispatch": ("dispatching", "dispatch", "디스패칭"),
    "alternate": ("대체 설비", "대체설비", "alternative tool", "alternate tool"),
    "rca": ("rca", "5 why", "5why", "근본 원인", "재발방지", "재발 방지", "root cause"),
    "recovery": ("복구", "recovery"),
    "escalation": ("escalation", "에스컬레이션", "상황 보고", "커뮤니케이션"),
    "kb_update": ("지식베이스", "지식 베이스", "knowledge base", "kb update", "kb_update"),
    "batching": ("batching", "배치 처리", "배치처리"),
    "rework": ("rework", "재작업"),
    "transport": ("transportation", "transport", "운반", "이송"),
    "setup": ("setup", "셋업", "세트업"),
    "lithography": ("lithography", "리소그래피", "포토", "photo"),
    "cmp": ("cmp", "평탄화"),
    "etch": ("etch", "etching", "식각"),
}
INCIDENT_CONCEPTS = set(ALIASES) - {
    "batching",
    "rework",
    "transport",
    "setup",
    "lithography",
    "cmp",
    "etch",
}
STOPWORDS = {
    "and",
    "for",
    "from",
    "that",
    "the",
    "this",
    "with",
    "what",
    "how",
    "when",
    "where",
    "are",
    "was",
    "does",
    "should",
    "of",
    "to",
    "in",
    "is",
    "on",
    "an",
    "as",
    "by",
    "or",
    "at",
    "be",
    "a",
    "공정",
    "관련",
    "기준",
    "라인",
    "질문",
    "확인",
    "알려줘",
    "설명",
    "설명해",
    "어떻게",
    "무엇",
    "뭐야",
    "어떤",
    "때",
    "방법",
    "절차",
    "내용",
    "함께",
    "그리고",
    "대한",
    "대해",
    "경우",
    "설정",
    "알려",
    "주세요",
    "알려주세요",
    "순서",
    "판단",
    "먼저",
    "있나요",
    "있어",
    "되어",
    "한다",
}
PARTICLES = (
    "에서는",
    "으로는",
    "이라고",
    "에서도",
    "에서",
    "으로",
    "이란",
    "인가요",
    "하면",
    "했을",
    "할때",
    "에는",
    "이랑",
    "하고",
    "보다",
    "까지",
    "부터",
    "처럼",
    "이",
    "가",
    "을",
    "를",
    "은",
    "는",
    "에",
    "와",
    "과",
    "도",
)


def normalize(text: str) -> str:
    text = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", text)
    return text.casefold().replace("_", " ")


@lru_cache(maxsize=8192)
def has_alias(text: str, alias: str) -> bool:
    escaped = re.escape(normalize(alias)).replace(r"\ ", r"\s*")
    if re.fullmatch(r"[a-z0-9 -]+", alias):
        escaped = r"(?<![a-z0-9])" + escaped + r"(?![a-z0-9])"
    return bool(re.search(escaped, normalize(text)))


@lru_cache(maxsize=2048)
def concepts(text: str) -> frozenset[str]:
    return frozenset(
        key for key, aliases in ALIASES.items() if any(has_alias(text, a) for a in aliases)
    )


def tokenize(text: str, *, expand: bool = True) -> list[str]:
    tokens = re.findall(r"[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*|[0-9]+|[가-힣]+", normalize(text))
    result = []
    for token in tokens:
        if re.fullmatch("[가-힣]+", token):
            for suffix in PARTICLES:
                if token.endswith(suffix) and len(token) > len(suffix) + 1:
                    token = token[: -len(suffix)]
                    break
        if len(token) > 1 and token not in STOPWORDS:
            result.append(token)
    if expand:
        result.extend("concept:" + key for key in sorted(concepts(text)))
    return result


@dataclass(frozen=True)
class QueryPlan:
    query: str
    knowledge_bases: tuple[str, ...]
    concepts: tuple[str, ...]
    excluded_concepts: tuple[str, ...]
    exact_ids: tuple[str, ...]
    fab_ids: tuple[str, ...]
    terms: tuple[str, ...]
    subqueries: tuple[str, ...]
    require_verified: bool = False


class QueryScopeError(ValueError):
    """The structured FAB scope conflicts with the user's stated document scope."""


def apply_fab_scope(plan: QueryPlan, fab_id: str | None) -> QueryPlan:
    if fab_id is None:
        return plan
    match = re.fullmatch(r"(?:fab|m|팹)\s*[-_]?\s*(\d+)", fab_id.strip(), re.IGNORECASE)
    if not match:
        raise QueryScopeError("검색할 FAB을 fab10과 같은 형식으로 지정해 주세요.")
    selected = "fab" + match.group(1)
    if plan.fab_ids and set(plan.fab_ids) != {selected}:
        raise QueryScopeError(
            "선택한 FAB과 질문에 적힌 FAB 범위가 다릅니다. 검색할 FAB을 확인해 주세요."
        )
    return replace(plan, fab_ids=(selected,))


def analyze_query(query: str, knowledge_base: str | None = None) -> QueryPlan:
    if knowledge_base is not None and knowledge_base not in {"process_basics", "incident_playbook"}:
        raise ValueError("Unknown RAG knowledge_base.")
    # Only local, explicit negation is interpreted. It must not invert the whole question.
    negatives = re.findall(r"([^,.;!?]+?)\s*(?:아니고|아니라|아님|아닌|말고|제외하고)", query)
    excluded = set().union(*(concepts(clause) for clause in negatives)) if negatives else set()
    positive = query
    for clause in negatives:
        positive = positive.replace(clause, " ", 1)
    active = concepts(positive)
    excluded -= active
    exact_ids = tuple(dict.fromkeys(re.findall(r"\bPB-[A-Z]+-\d+\b", positive.upper())))
    named_model_reference = bool(
        re.search(
            r"(?<![a-z0-9])(?:SMT\s*2020|AutoSched|CURSTEP|CALTYPE)(?![a-z0-9])",
            positive,
            re.IGNORECASE,
        )
    )
    named_manual_intent = bool(
        re.search(
            r"대응|조치|승인|돌발|(?<![a-z])playbook(?![a-z])|(?<![a-z])lot\s+hold(?![a-z])",
            positive,
            re.IGNORECASE,
        )
    )
    if knowledge_base:
        bases = (knowledge_base,)
    elif named_model_reference:
        bases = ("process_basics",)
        if named_manual_intent and (active & INCIDENT_CONCEPTS or exact_ids):
            bases = ("incident_playbook", "process_basics")
    elif (
        exact_ids or active & INCIDENT_CONCEPTS or any(w in query for w in ("대응", "조치", "위기"))
    ):
        bases = ("incident_playbook",)
        if any(w in positive for w in ("원리", "기초", "기본 개념", "이론")):
            bases += ("process_basics",)
    else:
        bases = ("process_basics",)
    fabs = tuple(
        dict.fromkeys(
            "fab" + match.group(1)
            for match in re.finditer(
                r"(?i)(?<![a-z0-9])(?:fab|m|팹)\s*[-_]?\s*(\d+)(?![0-9a-z])", positive
            )
        )
    )
    terms = tuple(t for t in tokenize(positive) if t not in {"concept:" + c for c in excluded})
    subqueries = [query]
    for key in sorted(active & INCIDENT_CONCEPTS):
        if len(active & INCIDENT_CONCEPTS) > 1:
            subqueries.append(ALIASES[key][0])
    return QueryPlan(
        query,
        bases,
        tuple(sorted(active)),
        tuple(sorted(excluded)),
        exact_ids,
        fabs,
        terms,
        tuple(subqueries[:4]),
        bool(
            re.search(
                r"(?:실제|사내|승인된|검증된).{0,20}(?:SOP|절차서|운영 매뉴얼)|(?:verified|approved)\s+SOP",
                positive,
                re.IGNORECASE,
            )
        ),
    )
