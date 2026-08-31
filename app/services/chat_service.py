from uuid import uuid4

from app.schemas.chat import ChatRequest, ChatResponse, Evidence
from app.sub_agent.text2sql import answer_question


class ChatService:
    def ask(self, request: ChatRequest) -> ChatResponse:
        conversation_id = request.conversation_id or str(uuid4())
        result = answer_question(request.message, fab=request.fab)
        evidence = []
        if result.plan:
            evidence.append(
                Evidence(
                    source_type="text2sql_plan",
                    title="Text2SQL plan",
                    content=result.plan.template_id or result.status,
                    metadata={
                        "status": result.status,
                        "fab_id": result.plan.fab_id,
                        "data_source_type": result.plan.data_source_type,
                        "slots": {
                            key: {
                                "value": slot.value,
                                "source": slot.source,
                                "confidence": slot.confidence,
                            }
                            for key, slot in result.plan.slots.items()
                        },
                    },
                )
            )

        return ChatResponse(
            conversation_id=conversation_id,
            query_type=result.query_type,
            answer=result.answer,
            evidence=evidence,
            sql=result.sql,
            confidence=result.confidence,
            limitations=result.limitations,
        )
