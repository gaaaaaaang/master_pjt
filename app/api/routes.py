from fastapi import APIRouter

from app.schemas.chat import ChatRequest, ChatResponse
from app.services.chat_service import ChatService

router = APIRouter(tags=["chat"])
service = ChatService()


@router.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    return service.ask(request)


@router.get("/meta")
def meta() -> dict[str, str]:
    return {
        "frontend": "streamlit",
        "backend": "fastapi",
        "agent": "planner-supervisor-initial",
    }


@router.post("/feedback")
def feedback(payload: dict) -> dict[str, str]:
    return {
        "status": "accepted",
        "message": "feedback persistence is a placeholder",
    }
