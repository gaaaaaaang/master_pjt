from uuid import uuid4

from app.agents.composer import compose_mock_answer
from app.agents.planner import make_plan
from app.agents.router import classify_query
from app.config import get_settings
from app.schemas.chat import ChatRequest, ChatResponse


class ChatService:
    def ask(self, request: ChatRequest) -> ChatResponse:
        settings = get_settings()
        conversation_id = request.conversation_id or str(uuid4())
        query_type = classify_query(request.message)
        _plan = make_plan(query_type)

        if settings.mock_mode:
            answer, evidence, limitations = compose_mock_answer(query_type, request.message)
            return ChatResponse(
                conversation_id=conversation_id,
                query_type=query_type,
                answer=answer,
                evidence=evidence,
                confidence=0.2,
                limitations=limitations,
            )

        raise NotImplementedError("실제 LLM/DB/RAG 연결은 Agent 상세 설계 후 구현합니다.")

