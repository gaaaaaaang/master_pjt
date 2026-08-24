from typing import Any

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    conversation_id: str | None = None
    fab: str | None = None
    line: str | None = None
    process: str | None = None


class Evidence(BaseModel):
    source_type: str
    title: str
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChatResponse(BaseModel):
    conversation_id: str
    query_type: str
    answer: str
    evidence: list[Evidence] = Field(default_factory=list)
    sql: str | None = None
    chart: dict[str, Any] | None = None
    confidence: float | None = None
    limitations: list[str] = Field(default_factory=list)

