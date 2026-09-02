from __future__ import annotations

from typing import Any

import pytest

from app.sub_agent.text2sql import QueryPlan, Text2SQLResult


def _planner_output(question: str) -> dict[str, Any]:
    lowered = question.casefold()
    fab_id = next((fab for fab in ("fab10", "fab11", "fab12", "fab13") if fab in lowered), None)
    basics_terms = ("뭐야", "무엇", "설명", "기초", "photo", "cmp", "autosched")
    if any(term in lowered for term in basics_terms):
        return {
            "status": "ready",
            "query_type": "knowledge_lookup",
            "intent": "test process basics knowledge lookup",
            "fab_id": fab_id,
            "rag_knowledge_base": "process_basics",
            "missing_slots": [],
            "selected_sub_agents": ["rag"],
            "execution_steps": [
                {
                    "agent": "rag",
                    "action": "retrieve process basics",
                    "required": True,
                    "reason": "test fixture",
                }
            ],
            "clarification_question": None,
            "limitations": [],
        }
    if not fab_id:
        return {
            "status": "needs_clarification",
            "query_type": "master_data_lookup",
            "intent": "identify target fab",
            "fab_id": None,
            "rag_knowledge_base": None,
            "missing_slots": ["fab_id"],
            "selected_sub_agents": [],
            "execution_steps": [],
            "clarification_question": "어느 FAB을 조회할까요?",
            "limitations": [],
        }

    if "왜" in lowered:
        query_type = "diagnosis"
        agents = ["text2sql", "rag", "case_search"]
        rag_knowledge_base = "incident_playbook"
    elif "영향" in lowered:
        query_type = "impact"
        agents = ["text2sql", "impact"]
        rag_knowledge_base = None
    elif any(term in lowered for term in ("라인차트", "추세", "비교")):
        query_type = "trend"
        agents = ["text2sql", "visualization"]
        rag_knowledge_base = None
    elif "toolgroup" in lowered:
        query_type = "master_data_lookup"
        agents = ["text2sql"]
        rag_knowledge_base = None
    elif "release" in lowered or "lotrelease" in lowered:
        query_type = "release_plan_lookup"
        agents = ["text2sql"]
        rag_knowledge_base = None
    else:
        query_type = "status"
        agents = ["text2sql"]
        rag_knowledge_base = None

    return {
        "status": "ready",
        "query_type": query_type,
        "intent": f"test {query_type}",
        "fab_id": fab_id,
        "rag_knowledge_base": rag_knowledge_base,
        "missing_slots": [],
        "selected_sub_agents": agents,
        "execution_steps": [
            {
                "agent": agent,
                "action": f"run {agent}",
                "required": agent == "text2sql" and query_type != "diagnosis",
                "reason": "test fixture",
            }
            for agent in agents
        ],
        "clarification_question": None,
        "limitations": [],
    }


@pytest.fixture(autouse=True)
def fake_agent_chat_completions(monkeypatch):
    def complete_json(self, *, schema_name, input_data, **kwargs):
        if schema_name == "fab_planner_decision":
            return _planner_output(input_data["question"])
        if schema_name == "fab_supervisor_decision":
            plan = input_data["planner_decision"]
            return {
                "proceed": plan["status"] == "ready",
                "status": plan["status"],
                "selected_sub_agents": plan["selected_sub_agents"],
                "reason": "test supervisor approval",
                "answer": None,
                "limitations": [],
            }
        if schema_name == "fab_self_reflection":
            return {"is_supported": True, "warnings": [], "composer_instructions": []}
        if schema_name == "fab_final_answer":
            summaries = input_data.get("tool_summaries") or []
            return {"answer": "\n\n".join(summaries) or "테스트 답변입니다."}
        raise AssertionError(f"Unexpected schema: {schema_name}")

    monkeypatch.setattr("app.agents.llm.AzureAgentClient.complete_json", complete_json)
    def answer_question(message, **kwargs):
        planner = _planner_output(message)
        query_type = planner["query_type"]
        succeeded = query_type in {"impact", "trend"}
        return Text2SQLResult(
            status="succeeded" if succeeded else "failed",
            query_type=query_type,
            answer=(
                "테스트용 SQL 근거를 준비했습니다."
                if succeeded
                else "LLM Text2SQL 호출을 완료하지 못했습니다."
            ),
            limitations=[] if succeeded else ["OPENAI_API_KEY is not configured."],
            plan=QueryPlan(
                query_type=query_type,
                template_id=None,
                fab_id=planner["fab_id"],
            ),
        )

    monkeypatch.setattr("app.agents.graph.answer_question", answer_question)
