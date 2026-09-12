from typing import Any

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    conversation_id: str | None = None
    fab: str | None = None
    line: str | None = None
    process: str | None = None
    product: str | None = None
    route: str | None = None
    equipment: str | None = None
    date_basis: str | None = None
    metric: str | None = None


class Evidence(BaseModel):
    source_type: str
    title: str
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChatResponse(BaseModel):
    conversation_id: str
    status: str
    query_type: str
    answer: str
    conversation_history: list[dict[str, Any]] = Field(default_factory=list)
    reasoning_state: list[dict[str, Any]] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    sql: str | None = None
    query_result: dict[str, Any] | None = None
    chart: dict[str, Any] | None = None
    confidence: float | None = None
    limitations: list[str] = Field(default_factory=list)
    data_sources: list[dict[str, Any]] = Field(default_factory=list)
    citations: list[dict[str, Any]] = Field(default_factory=list)
    grounding: dict[str, Any] = Field(default_factory=dict)
    model_usage: dict[str, Any] = Field(default_factory=dict)

    agent_runs: list[dict[str, Any]] = Field(default_factory=list)
    agent_reflections: list[dict[str, Any]] = Field(default_factory=list)
    supervisor_reviews: list[dict[str, Any]] = Field(default_factory=list)
    supervisor_decisions: list[dict[str, Any]] = Field(default_factory=list)
    retry_counts: dict[str, int] = Field(default_factory=dict)
    replan_count: int = 0
    reflection_decisions: list[dict[str, Any]] = Field(default_factory=list)
    termination_reason: str | None = None
    reflection: dict[str, Any] = Field(default_factory=dict)
    answer_review: dict[str, Any] = Field(default_factory=dict)


class FeedbackRequest(BaseModel):
    conversation_id: str = Field(min_length=1, max_length=128)
    helpful: bool
    comment: str | None = Field(default=None, max_length=2000)
    trace_id: str | None = Field(default=None, max_length=128)


class FeedbackResponse(BaseModel):
    status: str
    feedback_id: str
