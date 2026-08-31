from uuid import uuid4

from app.schemas.chat import ChatRequest, ChatResponse


class ChatService:
    def ask(self, request: ChatRequest) -> ChatResponse:
        conversation_id = request.conversation_id or str(uuid4())
        answer = (
            "백엔드 골격이 준비된 상태입니다. "
            "현재는 agent, DB, RAG 연결 없이 요청/응답 형태만 확인할 수 있습니다."
        )
        return ChatResponse(
            conversation_id=conversation_id,
            query_type="shell",
            answer=answer,
            confidence=0.0,
            limitations=[
                "agent 로직은 아직 연결하지 않았습니다.",
                "실제 FAB 데이터 조회와 문서 검색은 이후 단계에서 붙입니다.",
            ],
        )
