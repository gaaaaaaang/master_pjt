from app.schemas.chat import ChatRequest
from app.services.chat_service import ChatService
from app.services.conversation_memory import ConversationMemory


def test_chat_service_reuses_conversation_context_for_follow_up() -> None:
    service = ChatService(memory=ConversationMemory())

    first = service.ask(ChatRequest(message="fab10 Dry_Etch toolgroup 목록 보여줘"))
    second = service.ask(
        ChatRequest(
            message="그럼 WIP는 몇 개야?",
            conversation_id=first.conversation_id,
        )
    )

    assert second.conversation_id == first.conversation_id
    assert second.query_type == "status"
    assert [item["node"] for item in second.reasoning_state]
    assert any(item["node"] == "planner" for item in second.reasoning_state)
    assert second.conversation_history[0]["content"] == first.conversation_history[0]["content"]
    assert second.conversation_history[-2]["metadata"]["fab"] == "fab10"


def test_chat_service_starts_new_conversation_when_id_is_missing() -> None:
    service = ChatService(memory=ConversationMemory())

    first = service.ask(ChatRequest(message="fab10 Queue Time 추세를 비교해줘"))
    second = service.ask(ChatRequest(message="fab10 Queue Time 추세를 비교해줘"))

    assert first.conversation_id != second.conversation_id
    assert len(first.conversation_history) == 2
    assert len(second.conversation_history) == 2
