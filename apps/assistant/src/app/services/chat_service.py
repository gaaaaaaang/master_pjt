from dataclasses import asdict

from app.agents.intent import resolved_request_context
from app.agents.supervisor import Supervisor
from app.schemas.chat import ChatRequest, ChatResponse
from app.services.answer_presentation import present_response
from app.services.conversation_memory import ConversationMemory, conversation_memory


class ChatService:
    def __init__(
        self,
        supervisor: Supervisor | None = None,
        memory: ConversationMemory | None = None,
    ) -> None:
        self.supervisor = supervisor or Supervisor()
        self.memory = memory or conversation_memory

    def ask(self, request: ChatRequest) -> ChatResponse:
        prepared, history = self.memory.prepare_request(request)
        result = self.supervisor.run(prepared, conversation_history=history)
        presentation = present_response({"answer": result.answer, "status": result.status, "query_type": result.query_type,
                                         "evidence": result.evidence, "limitations": result.limitations, "query_result": result.query_result})
        updated_history = self.memory.append_exchange(
            conversation_id=result.conversation_id,
            request=prepared.model_copy(update=resolved_request_context(result.plan.slots))
            if result.plan else prepared,
            answer=presentation["answer"],
            supplied_fab=request.fab,
            query_result=result.query_result,
            metadata={
                "query_type": result.query_type,
                "status": result.status,
                "sql": result.sql,
                "limitations": result.limitations,
            },
        )
        return ChatResponse(
            conversation_id=result.conversation_id,
            status=result.status,
            query_type=result.query_type,
            answer=presentation["answer"],
            conversation_history=updated_history,
            reasoning_state=result.reasoning_state,
            evidence=result.evidence,
            sql=result.sql,
            query_result=result.query_result,
            chart=result.chart,
            confidence=result.confidence,
            limitations=presentation["limitations"],
            data_sources=presentation["data_sources"],
            citations=result.citations,
            grounding=result.grounding,
            model_usage=result.model_usage,
            agent_runs=[asdict(run) for run in result.agent_runs],
            agent_reflections=result.agent_reflections,
            supervisor_reviews=result.supervisor_reviews,
            supervisor_decisions=result.supervisor_decisions,
            retry_counts=result.retry_counts,
            replan_count=result.replan_count,
            reflection_decisions=result.reflection_decisions,
            termination_reason=result.termination_reason,
            reflection=result.reflection,
            answer_review=result.answer_review,
        )
