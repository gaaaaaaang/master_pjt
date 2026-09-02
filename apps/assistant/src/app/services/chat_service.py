from app.agents.supervisor import Supervisor
from app.schemas.chat import ChatRequest, ChatResponse


class ChatService:
    def __init__(self, supervisor: Supervisor | None = None) -> None:
        self.supervisor = supervisor or Supervisor()

    def ask(self, request: ChatRequest) -> ChatResponse:
        result = self.supervisor.run(request)
        return ChatResponse(
            conversation_id=result.conversation_id,
            query_type=result.query_type,
            answer=result.answer,
            evidence=result.evidence,
            sql=result.sql,
            chart=result.chart,
            confidence=result.confidence,
            limitations=result.limitations,
        )
