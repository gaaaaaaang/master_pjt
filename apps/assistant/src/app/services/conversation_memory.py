from __future__ import annotations

import json
import re
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import uuid4

from app.config import get_settings
from app.db.fab_catalog import comparison_fabs, normalize_fab, resolve_fab
from app.schemas.chat import ChatRequest
from app.services.state_store import AssistantStateStore
from app.sub_agent.snapshot_queries import simulation_areas
from app.sub_agent.text2sql import PERIOD_SCOPE_KEYS, PRODUCT_PATTERN, extract_query_slots

ROUTE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])route[-_ ]?product[-_ ]?([eE]?\d+)(?![A-Za-z0-9_])",
    re.IGNORECASE,
)
EQUIPMENT_PATTERN = re.compile(
    r"\b([A-Z][A-Za-z]{1,8})[\s_-]+([A-Z]{2})[\s_-]+([0-9]{1,3})\b"
)
PROCESS_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])(?:Dry[\s_-]*Etch|Wet[\s_-]*Etch|Diffusion|Implant|Photo|"
    r"TF[\s_-]*Met|Def[\s_-]*Met|CMP)"
    r"(?![A-Za-z0-9_])",
    re.IGNORECASE,
)
METRIC_ALIASES = (
    ("정비 시간", "pm_minutes"),
    ("정비시간", "pm_minutes"),
    ("pm 시간", "pm_minutes"),
    ("pm_minutes", "pm_minutes"),
    ("downtime", "down_minutes"),
    ("down_minutes", "down_minutes"),
    ("다운 시간", "down_minutes"),
    ("비가동 시간", "down_minutes"),
    ("비가동시간", "down_minutes"),
    ("투입 lot", "lotstarts"),
    ("투입lot", "lotstarts"),
    ("투입 로트", "lotstarts"),
    ("투입량", "lotstarts"),
    ("수율", "yield_percent"),
    ("yield", "yield_percent"),
    ("대기시간", "avg_queue_minutes"),
    ("대기 시간", "avg_queue_minutes"),
    ("현재 wip", "wiplotcur"),
    ("current wip", "wiplotcur"),
    ("queue time", "queue_time"),
    ("cycle time", "cycleavg"),
    ("납기 준수", "ontime_percent"),
    ("utilization", "util_percent"),
    ("가동률", "util_percent"),
    ("비가동률", "down_percent"),
    ("처리량", "lotcomps"),
    ("ontime", "ontime_percent"),
    ("on-time", "ontime_percent"),
    ("재공", "wiplotavg"),
    ("wip", "wiplotavg"),
    ("cycle", "cycleavg"),
    ("down", "down_percent"),
    ("비가동", "down_percent"),
    ("pm", "pm_percent"),
)


@dataclass(frozen=True)
class ConversationTurn:
    role: str
    content: str
    metadata: dict[str, Any]


class ConversationMemory:
    """Bounded conversation memory with optional durable SQLite persistence."""

    def __init__(self, *, max_turns: int = 12, store_path: Path | None = None) -> None:
        self.max_turns = max_turns
        self._turns: dict[str, deque[ConversationTurn]] = defaultdict(
            lambda: deque(maxlen=max_turns * 2)
        )
        self._loaded: set[str] = set()
        self._store = AssistantStateStore(store_path) if store_path else None
        self._lock = Lock()

    def prepare_request(self, request: ChatRequest) -> tuple[ChatRequest, list[dict[str, Any]]]:
        conversation_id = request.conversation_id or str(uuid4())
        history = self.get_history(conversation_id)
        inferred = self._infer_context(history)
        resets = _scope_resets(request.message)
        for key in resets:
            inferred.pop(key, None)
        current = {
            "fab": _parse_fab(request.message),
            "process": _parse_process(request.message),
            "product": _parse_product(request.message),
            "route": _parse_route(request.message),
            "equipment": _parse_equipment(request.message),
            "date_basis": _parse_date_basis(request.message),
            "metric": _parse_metric(request.message),
        }
        inherited_metric = (
            inferred.get("metric") if _should_inherit_metric(request.message) else None
        )
        prior_user = next((turn for turn in reversed(history) if turn.get("role") == "user"), None)
        prior_metadata = (prior_user or {}).get("metadata") or {}
        supplied_fab = request.fab
        if (not current["fab"] and "supplied_fab" in prior_metadata
                and normalize_fab(request.fab) == normalize_fab(prior_metadata["supplied_fab"])):
            # An unchanged sidebar default must not undo the FAB named in the
            # previous question. A newly selected sidebar FAB still takes effect.
            supplied_fab = inferred.get("fab") or supplied_fab
        prepared = request.model_copy(
            update={
                "conversation_id": conversation_id,
                "fab": current["fab"] or supplied_fab or inferred.get("fab"),
                "line": None if "line" in resets else request.line or inferred.get("line"),
                "process": current["process"] or (None if "process" in resets else request.process or inferred.get("process")),
                "product": current["product"] or (None if "product" in resets else request.product or inferred.get("product")),
                "route": current["route"] or (None if "route" in resets else request.route or inferred.get("route")),
                "equipment": (
                    current["equipment"] or (None if "equipment" in resets else request.equipment or inferred.get("equipment"))
                ),
                "date_basis": (
                    request.date_basis or current["date_basis"] or inferred.get("date_basis")
                ),
                "metric": request.metric or current["metric"] or inherited_metric,
            }
        )
        return prepared, history

    def append_exchange(
        self,
        *,
        conversation_id: str,
        request: ChatRequest,
        answer: str,
        metadata: dict[str, Any] | None = None,
        supplied_fab: str | None = None,
        query_result: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        with self._lock:
            self._ensure_loaded(conversation_id)
            turns = self._turns[conversation_id]
            query_slots = extract_query_slots(request.message, conversation_history=self._serialize(conversation_id))
            user_metadata = {
                "fab_ids": comparison_fabs(request.message, self._serialize(conversation_id)),
                "fab": request.fab or _parse_fab(request.message),
                "line": request.line,
                "process": request.process or _parse_process(request.message),
                "product": request.product or _parse_product(request.message),
                "route": request.route or _parse_route(request.message),
                "equipment": request.equipment or _parse_equipment(request.message),
                "date_basis": request.date_basis or _parse_date_basis(request.message),
                "metric": request.metric or _parse_metric(request.message),
                "supplied_fab": supplied_fab,
                "scope_reset": sorted(_scope_resets(request.message)),
                "query_areas": query_slots["areas"].value.split(",") if "areas" in query_slots else [],
                "query_metrics": query_slots["metrics"].value.split(",") if "metrics" in query_slots else [],
                "query_aggregations": json.loads(query_slots["metric_aggregations"].value) if "metric_aggregations" in query_slots else {},
                "query_group_by_area": "group_by_area" in query_slots,
                "query_period": {
                    key:slot.value for key,slot in query_slots.items() if key in PERIOD_SCOPE_KEYS
                },
            }
            assistant_metadata = dict(metadata or {})
            result_scope = _query_result_scope(query_result, request.fab)
            if result_scope:
                assistant_metadata["query_result_scope"] = result_scope
            if self._store:
                self._store.append_exchange(
                    conversation_id=conversation_id,
                    user_content=request.message,
                    user_metadata=user_metadata,
                    assistant_content=answer,
                    assistant_metadata=assistant_metadata,
                )
            turns.append(
                ConversationTurn(
                    role="user",
                    content=request.message,
                    metadata=user_metadata,
                )
            )
            turns.append(
                ConversationTurn(
                    role="assistant",
                    content=answer,
                    metadata=assistant_metadata,
                )
            )
            return self._serialize(conversation_id)

    def get_history(self, conversation_id: str) -> list[dict[str, Any]]:
        with self._lock:
            self._ensure_loaded(conversation_id)
            return self._serialize(conversation_id)

    def has_conversation(self, conversation_id: str) -> bool:
        with self._lock:
            self._ensure_loaded(conversation_id)
            return bool(self._turns.get(conversation_id))

    def attach_feedback(
        self,
        *,
        conversation_id: str,
        helpful: bool,
        comment: str | None,
        trace_id: str | None,
    ) -> list[dict[str, Any]]:
        feedback = {"helpful": helpful, "comment": comment, "trace_id": trace_id}
        with self._lock:
            self._ensure_loaded(conversation_id)
            turns = self._turns.get(conversation_id)
            if not turns:
                raise KeyError(conversation_id)
            for index in range(len(turns) - 1, -1, -1):
                turn = turns[index]
                if turn.role != "assistant":
                    continue
                prior_feedback = list(turn.metadata.get("user_feedback") or [])
                metadata = {**turn.metadata, "user_feedback": [*prior_feedback, feedback]}
                turns[index] = ConversationTurn(
                    role=turn.role,
                    content=turn.content,
                    metadata=metadata,
                )
                if self._store:
                    self._store.update_latest_assistant_metadata(
                        conversation_id=conversation_id,
                        metadata={"user_feedback": metadata["user_feedback"]},
                    )
                return self._serialize(conversation_id)
            raise KeyError(conversation_id)

    def clear(self) -> None:
        with self._lock:
            self._turns.clear()
            self._loaded.clear()
            if self._store:
                self._store.clear()

    def _serialize(self, conversation_id: str) -> list[dict[str, Any]]:
        return [
            {"role": turn.role, "content": turn.content, "metadata": turn.metadata}
            for turn in self._turns.get(conversation_id, [])
        ]

    def _ensure_loaded(self, conversation_id: str) -> None:
        if conversation_id in self._loaded:
            return
        if self._store:
            for turn in self._store.load_turns(conversation_id, limit=self.max_turns * 2):
                self._turns[conversation_id].append(
                    ConversationTurn(
                        role=str(turn["role"]),
                        content=str(turn["content"]),
                        metadata=dict(turn.get("metadata") or {}),
                    )
                )
        self._loaded.add(conversation_id)

    def _infer_context(self, history: list[dict[str, Any]]) -> dict[str, str]:
        context: dict[str, str] = {}
        blocked: set[str] = set()
        for turn in reversed(history):
            if turn.get("role") != "user":
                continue
            metadata = turn.get("metadata") or {}
            blocked.update(metadata.get("scope_reset") or [])
            for key in (
                "fab",
                "line",
                "process",
                "product",
                "route",
                "equipment",
                "date_basis",
                "metric",
            ):
                value = metadata.get(key)
                if value and key not in context and key not in blocked:
                    context[key] = str(value)
            if "fab" not in context:
                fab = _parse_fab(str(turn.get("content") or ""))
                if fab:
                    context["fab"] = fab
        return context


def _query_result_scope(result: dict[str, Any] | None, fab: str | None) -> dict[str, Any] | None:
    """Persist typed SQL dimensions, never identifiers copied from answer prose."""
    if not result or result.get("status") != "succeeded" or result.get("limit_reached"):
        return None
    rows = result.get("rows") or []
    if not rows or len(rows) != result.get("row_count") or not normalize_fab(fab):
        return None
    if any(not isinstance(row.get("area"), str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,99}", row["area"])
           or (row.get("fab_id") and row["fab_id"] != normalize_fab(fab)) for row in rows):
        return None
    return {"source_type":"text2sql_result", "status":"succeeded", "complete":True,
            "fab":normalize_fab(fab), "areas":list(dict.fromkeys(row["area"] for row in rows))}


def _scope_resets(question: str) -> set[str]:
    if re.search(r"(?:fab|팹|공장)\s*(?:\d+\s*)?전체|전체\s*(?:fab|팹|공장)", question, re.IGNORECASE):
        return {"line", "process", "product", "route", "equipment"}
    if len(simulation_areas(question)) > 1:
        return {"process", "equipment"}
    if re.search(r"공정별|영역별|각\s*공정|모든\s*공정|전체\s*공정", question) and not _parse_process(question):
        return {"process", "equipment"}
    return set()


def _parse_fab(content: str) -> str | None:
    return resolve_fab(content).fab_id


def _parse_process(content: str) -> str | None:
    if len(simulation_areas(content)) > 1:
        return None
    match = PROCESS_PATTERN.search(content)
    if not match:
        areas = simulation_areas(content)
        return areas[0] if len(areas) == 1 else None
    aliases = {
        "dry_etch": "Dry_Etch",
        "wet_etch": "Wet_Etch",
        "diffusion": "Diffusion",
        "implant": "Implant",
        "photo": "Photo",
        "tf_met": "TF_Met",
        "def_met": "Def_Met",
        "cmp": "CMP",
    }
    key = re.sub(r"[\s_-]+", "_", match.group(0).casefold())
    return aliases[key]


def _parse_product(content: str) -> str | None:
    match = PRODUCT_PATTERN.search(content)
    return f"Product_{match.group(1).lower()}" if match else None


def _parse_route(content: str) -> str | None:
    match = ROUTE_PATTERN.search(content)
    return f"Route_Product_{match.group(1).lower()}" if match else None


def _parse_equipment(content: str) -> str | None:
    match = EQUIPMENT_PATTERN.search(content)
    return "_".join(match.groups()) if match else None


def _parse_date_basis(content: str) -> str | None:
    normalized = re.sub(r"[\s-]+", "_", content.casefold())
    if any(alias in normalized for alias in ("due_date", "duedate", "납기일", "납기")):
        return "due_date"
    if any(
        alias in normalized
        for alias in ("start_date", "startdate", "투입일", "릴리즈일")
    ):
        return "start_date"
    return None


def _parse_metric(content: str) -> str | None:
    normalized = content.casefold()
    for alias, metric in sorted(METRIC_ALIASES, key=lambda item: -len(item[0])):
        if re.search(rf"(?<![a-z0-9_]){re.escape(alias)}(?![a-z0-9_])", normalized):
            return metric
    return None


def _should_inherit_metric(content: str) -> bool:
    normalized = content.casefold()
    if any(
        term in normalized
        for term in ("그중", "그 중", "같은", "동일", "그 지표", "이어서", "거기서", "그대로")
    ):
        return True
    return bool(
        re.search(
            r"(?:>=|<=|>|<)\s*\d|\d+(?:\.\d+)?\s*(?:%|퍼센트|percent)?\s*"
            r"(?:이상|이하|초과|미만)",
            normalized,
        )
    )


_settings = get_settings()
conversation_memory = ConversationMemory(
    store_path=(
        None
        if _settings.app_env == "test"
        else Path(_settings.assistant_state_store_path)
    )
)
