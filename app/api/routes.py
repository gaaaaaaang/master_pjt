from fastapi import APIRouter

from app.schemas.chat import ChatRequest, ChatResponse
from app.services.chat_service import ChatService

router = APIRouter(tags=["chat"])
service = ChatService()


@router.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    return service.ask(request)


@router.post("/feedback")
def feedback(payload: dict) -> dict[str, str]:
    # 저장소와 Few-shot 반영 규칙은 데이터 정책 확정 후 연결합니다.
    return {"status": "accepted", "message": "feedback persistence is a placeholder"}

