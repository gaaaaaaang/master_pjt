from __future__ import annotations

import os
import tempfile
from pathlib import Path

from app.config import get_settings
from app.schemas.chat import FeedbackRequest, FeedbackResponse
from app.services.conversation_memory import ConversationMemory, conversation_memory
from app.services.state_store import AssistantStateStore


class FeedbackService:
    def __init__(
        self,
        *,
        memory: ConversationMemory,
        store: AssistantStateStore,
    ) -> None:
        self.memory = memory
        self.store = store

    def record(self, request: FeedbackRequest) -> FeedbackResponse:
        if not self.memory.has_conversation(request.conversation_id):
            raise KeyError(request.conversation_id)
        history = self.memory.attach_feedback(
            conversation_id=request.conversation_id,
            helpful=request.helpful,
            comment=request.comment,
            trace_id=request.trace_id,
        )
        feedback_id = self.store.record_feedback(
            conversation_id=request.conversation_id,
            helpful=request.helpful,
            comment=request.comment,
            trace_id=request.trace_id,
            history=history,
        )
        return FeedbackResponse(status="accepted", feedback_id=feedback_id)


_settings = get_settings()
_feedback_path = (
    Path(_settings.assistant_state_store_path)
    if _settings.app_env != "test"
    else Path(tempfile.gettempdir()) / f"fab-ai-assistant-test-{os.getpid()}.sqlite3"
)
feedback_store = AssistantStateStore(_feedback_path)
feedback_service = FeedbackService(memory=conversation_memory, store=feedback_store)
