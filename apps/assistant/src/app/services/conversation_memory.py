from __future__ import annotations

import re
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import uuid4

from app.config import get_settings
from app.schemas.chat import ChatRequest
from app.services.state_store import AssistantStateStore

FAB_PATTERN = re.compile(r"\bfab[\s_-]*(\d+)\b", re.IGNORECASE)
PRODUCT_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])(?:product|part)[-_ ]?([eE]?\d+)(?![A-Za-z0-9_])",
    re.IGNORECASE,
)
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
        prepared = request.model_copy(
            update={
                "conversation_id": conversation_id,
                "fab": request.fab or current["fab"] or inferred.get("fab"),
                "line": request.line or inferred.get("line"),
                "process": request.process or current["process"] or inferred.get("process"),
                "product": request.product or current["product"] or inferred.get("product"),
                "route": request.route or current["route"] or inferred.get("route"),
                "equipment": (
                    request.equipment or current["equipment"] or inferred.get("equipment")
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
    ) -> list[dict[str, Any]]:
        with self._lock:
            self._ensure_loaded(conversation_id)
            turns = self._turns[conversation_id]
            user_metadata = {
                "fab": request.fab or _parse_fab(request.message),
                "line": request.line,
                "process": request.process or _parse_process(request.message),
                "product": request.product or _parse_product(request.message),
                "route": request.route or _parse_route(request.message),
                "equipment": request.equipment or _parse_equipment(request.message),
                "date_basis": request.date_basis or _parse_date_basis(request.message),
                "metric": request.metric or _parse_metric(request.message),
            }
            assistant_metadata = metadata or {}
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
        for turn in reversed(history):
            metadata = turn.get("metadata") or {}
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
                if value and key not in context:
                    context[key] = str(value)
            if "fab" not in context:
                fab = _parse_fab(str(turn.get("content") or ""))
                if fab:
                    context["fab"] = fab
        return context


def _parse_fab(content: str) -> str | None:
    match = FAB_PATTERN.search(content)
    return f"fab{match.group(1)}" if match else None


def _parse_process(content: str) -> str | None:
    match = PROCESS_PATTERN.search(content)
    if not match:
        return None
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
    for alias, metric in METRIC_ALIASES:
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
