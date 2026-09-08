"""Search a dedicated, provenance-bearing FAB incident case store."""

from __future__ import annotations

import json
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.schemas.chat import Evidence
from app.sub_agent.issue_intent import negated_issue_types

SIMULATION_SOURCE_MARKERS = ("simulation", "synthetic", "generated", "mock")
VERIFIED_RESERVE_SCORE_RATIO = 0.75


def find_similar_cases(
    query: str,
    top_k: int = 5,
    *,
    store_path: Path | None = None,
    execution_context: dict[str, Any] | None = None,
) -> list[Evidence]:
    """Find verified or explicitly simulated incidents, never generic playbook chunks."""
    from app.agents.execution import scoped_retrieval_query

    query = scoped_retrieval_query(query, execution_context)
    if top_k <= 0:
        return []
    path = store_path or Path(get_settings().incident_case_store_path)
    if not path.exists():
        raise NotImplementedError(f"Incident case store is not available: {path}")

    records = []
    case_ids: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        _validate_case_record(record)
        case_id = str(record["case_id"])
        if case_id in case_ids:
            raise ValueError(f"Incident case store contains duplicate case_id: {case_id}")
        case_ids.add(case_id)
        records.append(record)
    if not records:
        raise NotImplementedError(f"Incident case store has no records: {path}")

    query_terms = _expand_terms(_tokenize(query))
    ranked = sorted(
        ((_score_case(query, query_terms, record), record) for record in records),
        key=lambda item: (item[0], _verified_timestamp(item[1])),
        reverse=True,
    )
    selected = _select_ranked_cases(ranked, top_k)
    return [_to_evidence(record, score, query=query) for score, record in selected]


def _select_ranked_cases(
    ranked: list[tuple[float, dict[str, Any]]],
    top_k: int,
) -> list[tuple[float, dict[str, Any]]]:
    eligible = [item for item in ranked if item[0] > 0]
    selected = eligible[:top_k]
    if not selected or any(record["case_type"] == "verified" for _, record in selected):
        return selected

    best_verified = next(
        (item for item in eligible if item[1]["case_type"] == "verified"),
        None,
    )
    if (
        best_verified
        and best_verified[0] >= selected[-1][0] * VERIFIED_RESERVE_SCORE_RATIO
    ):
        selected[-1] = best_verified
    return selected


def _validate_case_record(record: dict[str, Any]) -> None:
    required = {"case_id", "case_type", "summary", "cause", "actions", "outcome", "source"}
    missing = sorted(required - record.keys())
    if missing:
        raise ValueError(f"Incident case record is missing fields: {', '.join(missing)}")
    if record["case_type"] not in {"verified", "simulated_reference"}:
        raise ValueError("Incident case_type must be verified or simulated_reference.")
    if record["case_type"] == "verified":
        source = str(record["source"]).casefold()
        if any(marker in source for marker in SIMULATION_SOURCE_MARKERS):
            raise ValueError("Verified incident source must not be synthetic or simulated.")
        _validate_verification(record.get("verification"))


def _validate_verification(verification: Any) -> None:
    if not isinstance(verification, dict):
        raise TypeError("Verified incident requires verification metadata.")
    required = {"incident_at", "verified_at", "verified_by", "evidence_refs"}
    missing = sorted(required - verification.keys())
    if missing:
        raise ValueError(
            "Verified incident verification is missing fields: " + ", ".join(missing)
        )
    incident_at = _aware_datetime(verification["incident_at"], field="incident_at")
    verified_at = _aware_datetime(verification["verified_at"], field="verified_at")
    if verified_at < incident_at:
        raise ValueError("Verified incident verified_at must not precede incident_at.")
    if not str(verification["verified_by"]).strip():
        raise ValueError("Verified incident verified_by must not be empty.")
    refs = verification["evidence_refs"]
    if not isinstance(refs, list) or not refs or not all(str(ref).strip() for ref in refs):
        raise ValueError("Verified incident evidence_refs must be a non-empty list.")


def _aware_datetime(value: Any, *, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError as exc:
        raise ValueError(f"Verified incident {field} must be ISO-8601.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"Verified incident {field} must include a timezone offset.")
    return parsed


def _score_case(query: str, query_terms: set[str], record: dict[str, Any]) -> float:
    metadata = record.get("metadata") or {}
    if not _target_metadata_matches(query, metadata):
        return 0.0
    summary_terms = _expand_terms(_tokenize(str(record["summary"])))
    all_terms = _expand_terms(
        _tokenize(
            " ".join(
                [
                    str(record["summary"]),
                    str(record["cause"]),
                    " ".join(map(str, record["actions"])),
                    str(record["outcome"]),
                    " ".join(map(str, metadata.values())),
                ]
            )
        )
    )
    if not query_terms or not all_terms:
        return 0.0
    overlap = query_terms & all_terms
    issue_intents = _query_issue_intents(query_terms, query=query)
    issue_type = str(metadata.get("issue_type") or "").casefold()
    issue_match = any(_issue_type_matches(intent, issue_type) for intent in issue_intents)
    if issue_intents and issue_type and not issue_match:
        return 0.0
    if not overlap & _INCIDENT_SIGNAL_TERMS and not issue_match:
        return 0.0
    score = (
        len(overlap) / math.sqrt(len(all_terms))
        + len(query_terms & summary_terms) * 0.5
        + (1.25 if issue_match else 0.0)
    )
    if _temporal_alignment(query, record) == "historical_outside_requested_period":
        score *= 0.8
    return score


def _target_metadata_matches(query: str, metadata: dict[str, Any]) -> bool:
    query_fab = _query_fab(query)
    record_fab = str(metadata.get("fab_id") or "").strip().casefold()
    if query_fab and record_fab and query_fab != record_fab:
        return False

    query_equipment_type = _query_equipment_type(query)
    record_equipment_type = str(metadata.get("equipment_type") or "").strip()
    if (
        query_equipment_type
        and record_equipment_type
        and _normalize_target(query_equipment_type)
        != _normalize_target(record_equipment_type)
    ):
        return False

    query_process = _query_process_group(query)
    record_process = str(
        metadata.get("process_group") or metadata.get("toolgroup") or ""
    ).strip()
    return not (
        query_process
        and record_process
        and _normalize_target(query_process) != _normalize_target(record_process)
    )


def _query_fab(query: str) -> str | None:
    match = re.search(r"(?<![a-z0-9])fab[\s_-]*(1[0-3])(?!\d)", query, re.IGNORECASE)
    return f"fab{match.group(1)}" if match else None


def _verified_timestamp(record: dict[str, Any]) -> float:
    if record.get("case_type") != "verified":
        return 0.0
    verification = record.get("verification") or {}
    try:
        return datetime.fromisoformat(str(verification.get("verified_at"))).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _temporal_alignment(query: str, record: dict[str, Any]) -> str:
    dates = re.findall(r"(?<!\d)\d{4}[-/.]\d{2}[-/.]\d{2}(?!\d)", query)
    verification = record.get("verification") or {}
    incident_at = verification.get("incident_at")
    if not dates or not incident_at:
        return "unspecified"
    try:
        requested = [
            datetime.fromisoformat(value.replace("/", "-").replace(".", "-")).date()
            for value in dates
        ]
        incident_date = _aware_datetime(incident_at, field="incident_at").date()
    except ValueError:
        return "unspecified"
    if len(requested) == 1:
        aligned = incident_date == requested[0]
    elif len(requested) % 2 == 0:
        aligned = any(
            start <= incident_date <= end
            for start, end in zip(requested[::2], requested[1::2], strict=True)
        )
    else:
        aligned = min(requested) <= incident_date <= max(requested)
    return "aligned" if aligned else "historical_outside_requested_period"


def _query_equipment_type(query: str) -> str | None:
    match = re.search(
        r"(?<![a-z0-9])([a-z]{2,8})[\s_-]+([a-z]{2})[\s_-]+\d{1,3}",
        query,
        flags=re.IGNORECASE,
    )
    return "_".join(match.groups()) if match else None


def _query_process_group(query: str) -> str | None:
    match = re.search(
        r"(?<![a-z0-9])(?:dry[\s_-]*etch|wet[\s_-]*etch|diffusion|implant|photo|"
        r"tf[\s_-]*met|def[\s_-]*met|cmp)(?![a-z0-9])",
        query,
        flags=re.IGNORECASE,
    )
    return match.group(0) if match else None


def _normalize_target(value: str) -> str:
    return re.sub(r"[\s_-]+", "_", value.strip().casefold())


def _to_evidence(record: dict[str, Any], score: float, *, query: str) -> Evidence:
    case_type = str(record["case_type"])
    label = "검증 사례" if case_type == "verified" else "시뮬레이션 참고 사례"
    content = (
        f"{label}: {record['summary']}\n"
        f"원인: {record['cause']}\n"
        f"조치: {', '.join(map(str, record['actions']))}\n"
        f"결과: {record['outcome']}"
    )
    return Evidence(
        source_type="similar_case",
        title=f"Incident case {record['case_id']}",
        content=content,
        metadata={
            **(record.get("metadata") or {}),
            "case_id": record["case_id"],
            "case_type": case_type,
            "source": record["source"],
            "cause": record["cause"],
            "actions": list(record["actions"]),
            "outcome": record["outcome"],
            "verification": record.get("verification"),
            "temporal_alignment": _temporal_alignment(query, record),
            "score": round(score, 4),
        },
    )


def _tokenize(text: str) -> set[str]:
    normalized = text.casefold().replace("_", " ")
    tokens = {_normalize_token(token) for token in re.findall(r"[0-9a-z가-힣]{2,}", normalized)}
    return {token for token in tokens if len(token) >= 2 and token not in _STOPWORDS}


def _normalize_token(token: str) -> str:
    if token.endswith("s") and re.fullmatch(r"[a-z]{4,}", token):
        token = token[:-1]
    for suffix in (
        "에서",
        "으로",
        "에게",
        "까지",
        "부터",
        "처럼",
        "과",
        "와",
        "은",
        "는",
        "이",
        "가",
        "을",
        "를",
        "의",
        "로",
    ):
        if token.endswith(suffix) and len(token) - len(suffix) >= 2:
            return token[: -len(suffix)]
    return token


def _expand_terms(terms: set[str]) -> set[str]:
    expanded = set(terms)
    for aliases in _TERM_ALIASES:
        if terms & aliases:
            expanded.update(aliases)
    return expanded


_TERM_ALIASES = (
    {
        "queue", "대기", "대기시간", "대기열", "체류", "체류시간", "기다림",
        "웨이팅", "정체", "적체",
    },
    {"wip", "재공", "정체", "적체"},
    {
        "down",
        "breakdown",
        "고장",
        "장애",
        "비가동",
        "비가동률",
        "중단",
        "멈춘",
        "멈춰서",
        "멈췄어",
        "작동불능",
        "가동불능",
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
    {"ontime", "납기", "준수율", "납기준수율", "납기성능", "정시"},
    {"cycle", "cycleavg", "사이클", "사이클타임", "리드타임"},
    {"bottleneck", "병목", "starvation", "막힘", "막혀서", "막힌", "포화"},
)

_INCIDENT_SIGNAL_TERMS = set().union(*_TERM_ALIASES)

_ISSUE_QUERY_TERMS = {
    "queue_time": {"queue", "대기", "대기시간", "대기열", "체류", "체류시간", "기다림", "웨이팅"},
    "bottleneck": {"bottleneck", "병목", "starvation", "막힘", "막혀서", "막힌", "포화"},
    "ontime_drop": {"ontime", "납기", "준수율", "납기준수율", "납기성능", "정시"},
    "equipment_down": {
        "down", "breakdown", "고장", "장애", "비가동", "비가동률", "중단",
        "멈춘", "멈춰서", "멈췄어", "작동불능", "가동불능", "알람", "경보",
    },
    "pm": {
        "pm", "maintenance", "정비", "예방정비", "보전", "정기점검",
        "유지보수", "점검일정",
    },
}


def _query_issue_intents(query_terms: set[str], *, query: str = "") -> set[str]:
    intents = {
        issue_type
        for issue_type, terms in _ISSUE_QUERY_TERMS.items()
        if query_terms & terms
    }
    if query:
        intents -= negated_issue_types(query, _ISSUE_QUERY_TERMS)
    return intents


def _issue_type_matches(intent: str, issue_type: str) -> bool:
    aliases = {
        "equipment_down": {"equipment_down", "station_down"},
    }
    return issue_type in aliases.get(intent, {intent}) or issue_type.startswith(f"{intent}_")


_STOPWORDS = {"관련", "원인", "사례", "같이", "찾아줘", "fab10", "fab11", "fab12", "fab13"}
