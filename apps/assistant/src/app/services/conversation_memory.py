from __future__ import annotations

import re
from collections import defaultdict, deque
from dataclasses import dataclass
from threading import Lock
from typing import Any
from uuid import uuid4

from app.schemas.chat import ChatRequest

FAB_PATTERN = re.compile(r"\bfab\d+\b", re.IGNORECASE)


@dataclass(frozen=True)
class ConversationTurn:
    role: str
    content: str
    metadata: dict[str, Any]


class ConversationMemory:
    """Small in-process conversation store for local multi-turn chat."""

    def __init__(self, *, max_turns: int = 12) -> None:
        self.max_turns = max_turns
        self._turns: dict[str, deque[ConversationTurn]] = defaultdict(
            lambda: deque(maxlen=max_turns * 2)
        )
        self._lock = Lock()

    def prepare_request(self, request: ChatRequest) -> tuple[ChatRequest, list[dict[str, Any]]]:
        conversation_id = request.conversation_id or str(uuid4())
        history = self.get_history(conversation_id)
        inferred = self._infer_context(history)
        prepared = request.model_copy(
            update={
                "conversation_id": conversation_id,
                "fab": request.fab or inferred.get("fab"),
                "line": request.line or inferred.get("line"),
                "process": request.process or inferred.get("process"),
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
            turns = self._turns[conversation_id]
            turns.append(
                ConversationTurn(
                    role="user",
                    content=request.message,
                    metadata={
                        "fab": request.fab or _parse_fab(request.message),
                        "line": request.line,
                        "process": request.process,
                    },
                )
            )
            turns.append(
                ConversationTurn(
                    role="assistant",
                    content=answer,
                    metadata=metadata or {},
                )
            )
            return self._serialize(conversation_id)

    def get_history(self, conversation_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return self._serialize(conversation_id)

    def clear(self) -> None:
        with self._lock:
            self._turns.clear()

    def _serialize(self, conversation_id: str) -> list[dict[str, Any]]:
        return [
            {"role": turn.role, "content": turn.content, "metadata": turn.metadata}
            for turn in self._turns.get(conversation_id, [])
        ]

    def _infer_context(self, history: list[dict[str, Any]]) -> dict[str, str]:
        context: dict[str, str] = {}
        for turn in reversed(history):
            metadata = turn.get("metadata") or {}
            for key in ("fab", "line", "process"):
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
    return match.group(0).lower() if match else None


conversation_memory = ConversationMemory()
