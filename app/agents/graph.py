from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentState:
    message: str
    conversation_id: str
    query_type: str | None = None
    plan: list[str] = field(default_factory=list)
    tool_results: dict[str, Any] = field(default_factory=dict)
    answer: str | None = None
    limitations: list[str] = field(default_factory=list)


def build_agent_graph():
    """LangGraph 연결 지점.

    Keep this empty until detailed agent behavior is finalized.
    Target flow: router -> planner -> supervisor/sub_agent -> verifier -> composer.
    """
