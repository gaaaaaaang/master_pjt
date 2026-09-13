from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Literal, cast

from app.agents.intent import build_answer_requirements
from app.agents.llm import AzureAgentClient
from app.agents.planner import AGENT_NAMES, PlannerDecision, _normalize_agent_route
from app.agents.prompts import (
    AGENT_RECOVERY_PROMPT_VERSION,
    AGENT_RECOVERY_SYSTEM_PROMPT,
    ANSWER_SUPERVISOR_PROMPT_VERSION,
    ANSWER_SUPERVISOR_SYSTEM_PROMPT,
    SUPERVISOR_PROMPT_VERSION,
    SUPERVISOR_SYSTEM_PROMPT,
)
from app.schemas.chat import ChatRequest, Evidence
from app.sub_agent.reflection import verify_response
from app.sub_agent.result_delivery import query_result_payload, result_presentation

SupervisorStatus = Literal[
    "succeeded",
    "needs_clarification",
    "data_unavailable",
    "unsupported",
    "failed",
    "needs_replan",
]
RecoveryAction = Literal["continue", "retry_same_agent", "retry_agents", "replan", "alternate_agent", "compose"]

SUPERVISOR_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "proceed": {"type": "boolean"},
        "status": {
            "type": "string",
            "enum": ["ready", "needs_clarification", "data_unavailable", "unsupported"],
        },
        "selected_sub_agents": {
            "type": "array",
            "items": {"type": "string", "enum": sorted(AGENT_NAMES)},
        },
        "reason": {"type": "string"},
        "answer": {"type": ["string", "null"]},
        "limitations": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["proceed", "status", "selected_sub_agents", "reason", "answer", "limitations"],
}

AGENT_RECOVERY_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "action": {
            "type": "string",
            "enum": ["continue", "retry_same_agent", "retry_agents", "replan", "alternate_agent", "compose"],
        },
        "retry_agents": {"type": "array", "items": {"type": "string", "enum": sorted(AGENT_NAMES)}},
        "repair_instructions": {"type": ["string", "null"]},
        "alternate_agent": {
            "type": ["string", "null"],
            "enum": [*sorted(AGENT_NAMES), None],
        },
        "reason": {"type": "string"},
        "planner_feedback": {"type": ["string", "null"]},
        "limitations": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["action", "retry_agents", "repair_instructions", "alternate_agent", "reason", "planner_feedback", "limitations"],
}

ANSWER_SUPERVISOR_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "approved": {"type": "boolean"},
        "issues": {"type": "array", "items": {"type": "string"}},
        "corrected_answer": {"type": ["string", "null"]},
        "reason": {"type": "string"},
        "semantic_checks": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {"warning_index": {"type": "integer"},
                           "violated": {"type": "boolean"}, "reason": {"type": "string"}},
            "required": ["warning_index", "violated", "reason"],
        }},
    },
    "required": ["approved", "issues", "corrected_answer", "reason", "semantic_checks"],
}


@dataclass(frozen=True)
class AgentRun:
    agent: str
    status: str
    summary: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SupervisorResult:
    conversation_id: str
    status: SupervisorStatus
    query_type: str
    answer: str
    evidence: list[Evidence] = field(default_factory=list)
    reasoning_state: list[dict[str, Any]] = field(default_factory=list)
    sql: str | None = None
    query_result: dict[str, Any] | None = None
    chart: dict[str, Any] | None = None
    confidence: float | None = None
    limitations: list[str] = field(default_factory=list)
    plan: PlannerDecision | None = None
    agent_runs: list[AgentRun] = field(default_factory=list)
    agent_reflections: list[dict[str, Any]] = field(default_factory=list)
    supervisor_reviews: list[dict[str, Any]] = field(default_factory=list)
    supervisor_decisions: list[dict[str, Any]] = field(default_factory=list)
    retry_counts: dict[str, int] = field(default_factory=dict)
    replan_count: int = 0
    reflection_decisions: list[dict[str, Any]] = field(default_factory=list)
    termination_reason: str | None = None
    reflection: dict[str, Any] = field(default_factory=dict)
    citations: list[dict[str, Any]] = field(default_factory=list)
    grounding: dict[str, Any] = field(default_factory=dict)
    model_usage: dict[str, Any] = field(default_factory=dict)
    answer_review: dict[str, Any] = field(default_factory=dict)
    prompt_version: str = SUPERVISOR_PROMPT_VERSION
    prompt_contract: str = SUPERVISOR_SYSTEM_PROMPT


def review_plan(
    plan: PlannerDecision,
    question: str,
    *,
    llm_client: AzureAgentClient | None = None,
) -> tuple[PlannerDecision, dict[str, Any]]:
    """Review and authorize a Planner plan with an independent LLM call."""
    output = (llm_client or AzureAgentClient()).complete_json(
        system_prompt=SUPERVISOR_SYSTEM_PROMPT,
        input_data={"question": question, "planner_decision": {k: v for k, v in asdict(plan).items() if k not in {"prompt_contract", "prompt_version"}}},
        output_schema=SUPERVISOR_OUTPUT_SCHEMA,
        schema_name="fab_supervisor_decision",
    )
    if (
        plan.status == "ready"
        and "text2sql" in plan.selected_sub_agents
        and output["status"] == "data_unavailable"
    ):
        output = {
            **output, "proceed": True, "status": "ready",
            "selected_sub_agents": list(plan.selected_sub_agents), "answer": None,
            "limitations": [],
            "reason": "현재 DB 가용성은 Text2SQL의 새 조회 결과로 확인합니다.",
        }
    status = output["status"]
    if plan.status != "ready":
        status = plan.status  # An approval cannot invent missing user input.
    elif not output["proceed"] and status == "ready":
        status = "needs_clarification" if output.get("answer") else "unsupported"
    selected, steps = _normalize_agent_route(
        status=status, query_type=plan.query_type, question=question,
        selected_sub_agents=list(output["selected_sub_agents"]),
        execution_steps=plan.execution_steps,
    )
    if status != "ready":
        selected, steps = [], []
    # Preserve the grounded dependency/input contract after supervisor selection.
    original = {step.agent: step for step in plan.execution_steps}
    steps = [replace(
        step,
        depends_on=(original[step.agent].depends_on if step.agent in original
                    else ["text2sql"] if step.agent in {"impact", "visualization"} else []),
        input_requirements=(original[step.agent].input_requirements if step.agent in original
                            else ["request scope", "available upstream evidence and its limitations"]),
    ) for step in steps]
    reviewed = replace(
        plan, status=status, selected_sub_agents=selected, execution_steps=steps,
        answer_requirements=(build_answer_requirements(plan.query_type, selected, plan.intent_analysis)
                             if plan.intent_analysis else plan.answer_requirements),
        clarification_question=(plan.clarification_question or output.get("answer")
                                if status == "needs_clarification" else None),
        limitations=list(dict.fromkeys([*plan.limitations, *output["limitations"]])),
    )
    output = {**output, "status": status, "proceed": status == "ready",
              "selected_sub_agents": selected}

    return reviewed, output


def review_agent_result(
    plan: PlannerDecision,
    reflection: dict[str, Any],
    *,
    retry_count: int,
    retry_budget_remaining: int,
    replan_budget_remaining: int,
    alternate_budget_remaining: int,
    allowed_alternate_agents: list[str],
    execution_context: dict[str, Any] | None = None,
    llm_client: AzureAgentClient | None = None,
) -> dict[str, Any]:
    """Choose a bounded recovery action for one reflected agent result."""
    output = (llm_client or AzureAgentClient()).complete_json(
        system_prompt=AGENT_RECOVERY_SYSTEM_PROMPT,
        input_data={
            "planner_decision": {k: v for k, v in asdict(plan).items() if k not in {"prompt_contract", "prompt_version"}},
            "agent_reflection": reflection,
            "execution_context": execution_context or {},
            "retry_count": retry_count,
            "retry_budget_remaining": retry_budget_remaining,
            "replan_budget_remaining": replan_budget_remaining,
            "alternate_budget_remaining": alternate_budget_remaining,
            "allowed_alternate_agents": allowed_alternate_agents,
        },
        output_schema=AGENT_RECOVERY_OUTPUT_SCHEMA,
        schema_name="fab_agent_recovery_decision",
    )
    return enforce_recovery_policy(
        output, reflection=reflection, retry_count=retry_count,
        retry_budget_remaining=retry_budget_remaining,
        replan_budget_remaining=replan_budget_remaining,
        alternate_budget_remaining=alternate_budget_remaining,
        allowed_alternate_agents=allowed_alternate_agents,
        execution_context=execution_context or {},
    )


def enforce_recovery_policy(
    output: dict[str, Any], *, reflection: dict[str, Any], retry_count: int,
    retry_budget_remaining: int, replan_budget_remaining: int,
    alternate_budget_remaining: int, allowed_alternate_agents: list[str],
    execution_context: dict[str, Any],
) -> dict[str, Any]:
    """Enforce budgets again at the graph boundary, independent of model behavior."""
    action = str(output.get("action", "continue"))
    status = str(reflection.get("status") or "unknown")
    alternate = output.get("alternate_agent")
    rejection_reason: str | None = None

    if action not in {"continue", "compose", "retry_same_agent", "retry_agents", "replan", "alternate_agent"}:
        action = "continue"
        rejection_reason = "Unknown recovery action was rejected."
    if action == "compose" and not execution_context.get("coverage", {}).get("all_satisfied"):
        action = "continue"
        rejection_reason = "Composition cannot bypass unresolved answer requirements."
    retry_agents = list(dict.fromkeys(output.get("retry_agents") or []))
    if action == "retry_agents":
        results = execution_context.get("active_results", {})
        counts = execution_context.get("retry_counts", {})
        valid = bool(retry_agents) and bool(output.get("repair_instructions"))
        valid = valid and len(retry_agents) <= retry_budget_remaining
        valid = valid and all(
            agent in results and counts.get(agent, 0) < 1
            and results[agent].get("status") not in {
                "data_unavailable", "unsupported", "needs_clarification", "skipped",
            } for agent in retry_agents
        )
        if not valid:
            action = "continue"
            rejection_reason = "Combination retry requires repair instructions, attempted agents, and budget."
    if action == "retry_same_agent" and (
        retry_budget_remaining <= 0
        or retry_count >= 1
        or status in {"data_unavailable", "unsupported", "needs_clarification", "skipped"}
    ):
        action = "replan" if status != "succeeded" and replan_budget_remaining > 0 else "continue"
        rejection_reason = "Retry was rejected by the bounded recovery policy."
    if action == "replan" and not output.get("planner_feedback") and not rejection_reason:
        action = "continue"
        rejection_reason = "Replanning requires concrete feedback for the Planner."
    if action == "replan" and replan_budget_remaining <= 0:
        action = "continue"
        rejection_reason = "Replan budget is exhausted."
    if action == "alternate_agent" and (
        alternate_budget_remaining <= 0 or alternate not in allowed_alternate_agents
    ):
        action = "continue"
        alternate = None
        rejection_reason = "Alternate agent was rejected by the compatibility or budget policy."

    return {
        **output,
        "action": action,
        "retry_agents": retry_agents if action == "retry_agents" else [],
        "alternate_agent": alternate if action == "alternate_agent" else None,
        "reason": (
            f"{output['reason']} {rejection_reason}" if rejection_reason else output["reason"]
        ),
        "prompt_version": AGENT_RECOVERY_PROMPT_VERSION,
    }


def review_final_answer(
    *,
    question: str,
    answer: str,
    plan: PlannerDecision,
    evidence: list[dict[str, Any]],
    limitations: list[str],
    llm_client: AzureAgentClient | None = None,
) -> dict[str, Any]:
    """Bounded model revisions; every revision uses the same evidence and validation."""
    candidate = answer
    attempts = []
    for _ in range(3):
        review = _review_final_answer_once(
            question=question, answer=candidate, plan=plan, evidence=evidence,
            limitations=limitations, llm_client=llm_client,
        )
        attempts.append({"approved": review["approved"], "issues": review.get("issues", []),
                         "correction_check": review.get("correction_check"),
                         "candidate": candidate})
        if review["approved"]:
            if candidate != answer and not review.get("correction_applied"):
                review.update(corrected_answer=candidate, correction_applied=True, correction_source="model")
            break
        revised = review.get("corrected_answer")
        if not revised or revised == candidate:
            break
        candidate = revised
    return {**review, "review_attempts": attempts}


def _review_final_answer_once(
    *, question: str, answer: str, plan: PlannerDecision,
    evidence: list[dict[str, Any]], limitations: list[str],
    llm_client: AzureAgentClient | None = None,
) -> dict[str, Any]:
    """Review the composed answer against the original request and grounded evidence."""
    if plan.status == "needs_clarification" and answer.strip() == (plan.clarification_question or "").strip():
        return {"approved": bool(answer.strip()), "issues": [], "correction_applied": False,
                "corrected_answer": None, "reason": "Preserved the grounded clarification question.",
                "prompt_version": ANSWER_SUPERVISOR_PROMPT_VERSION}
    deterministic = verify_response(
        answer,
        evidence=evidence,
        limitations=limitations,
        query_type=plan.query_type,
        question=question,
    )
    output = (llm_client or AzureAgentClient()).complete_json(
        system_prompt=ANSWER_SUPERVISOR_SYSTEM_PROMPT,
        input_data={
            "question": question,
            "planner_decision": {k: v for k, v in asdict(plan).items() if k not in {"prompt_contract", "prompt_version"}},
            "final_answer": answer,
            "result_presentation": result_presentation(evidence),
            "evidence": evidence,
            "limitations": limitations,
            # Do not anchor the reviewer on a lexical probe's asserted failure
            # or aggregate score. Supply neutral topics plus enforceable checks.
            "deterministic_check": {
                "blocking_warnings": deterministic["blocking_warnings"],
                "unsupported_numeric_claims": deterministic["unsupported_numeric_claims"],
            },
            "semantic_review_items": [
                {"warning_index": i, "topic": topic}
                for i, topic in enumerate(deterministic["semantic_review_topics"])
            ],
        },
        output_schema=ANSWER_SUPERVISOR_OUTPUT_SCHEMA,
        schema_name="fab_answer_supervisor_decision",
    )
    issues = list(dict.fromkeys([*deterministic["blocking_warnings"], *output["issues"]]))
    checks = output.get("semantic_checks") or []
    expected_indices = set(range(len(deterministic["semantic_warnings"])))
    valid_checks = all(isinstance(check, dict)
                       and type(check.get("warning_index")) is int
                       and type(check.get("violated")) is bool
                       and isinstance(check.get("reason"), str) and check["reason"].strip()
                       for check in checks)
    if (not valid_checks or len(checks) != len(expected_indices)
            or {check["warning_index"] for check in checks} != expected_indices):
        issues.append("Semantic review must explicitly examine every flagged claim.")
    else:
        issues.extend(check["reason"] for check in checks if check["violated"])
    original_approved = bool(output["approved"]) and not issues
    approved = original_approved
    corrected_answer = str(output.get("corrected_answer") or "").strip() or None
    correction_check = None
    correction_applied = False
    if not approved and corrected_answer:
        correction_check = verify_response(
            corrected_answer,
            evidence=evidence,
            limitations=limitations,
            query_type=plan.query_type,
            question=question,
        )
        novel_topics = _ungrounded_correction_topics(corrected_answer, answer, evidence) if plan.query_type == "diagnosis" else []
        if novel_topics:
            correction_check["is_supported"] = False
            correction_check["warnings"].append(
                "Correction introduced diagnosis topics absent from the supplied evidence: " + ", ".join(novel_topics)
            )
        if not correction_check["blocking_warnings"] and not correction_check["semantic_warnings"] and not novel_topics:
            approved = True
            issues = []
            correction_applied = True

    correction_source = "model" if correction_applied else None

    return {
        **output,
        "correction_source": correction_source,
        "approved": approved,
        "original_approved": original_approved,
        "issues": issues,
        "corrected_answer": corrected_answer,
        "correction_applied": correction_applied,
        "deterministic_check": deterministic,
        "correction_check": correction_check,
        "prompt_version": ANSWER_SUPERVISOR_PROMPT_VERSION,
    }


class Supervisor:
    """Run the same LLM LangGraph cycle used by the streaming API."""

    def run(
        self,
        request: ChatRequest,
        *,
        conversation_history: list[dict[str, Any]] | None = None,
    ) -> SupervisorResult:
        from app.agents.graph import build_agent_graph, initial_graph_state

        state = initial_graph_state(request, conversation_history=conversation_history)
        try:
            for update in build_agent_graph().stream(state, config={"recursion_limit": 100}, stream_mode="updates"):
                for patch in update.values():
                    state.update(patch)
        finally:
            state["usage_ledger"].cancel()

        plan = state["plan"]
        return SupervisorResult(
            conversation_id=state["conversation_id"],
            status=cast(SupervisorStatus, state.get("status", "failed")),
            query_type=plan.query_type,
            answer=state.get("answer", ""),
            evidence=[Evidence.model_validate(item) for item in state.get("evidence", [])],
            reasoning_state=state.get("reasoning_state", []),
            sql=state.get("sql"),
            query_result=query_result_payload(state.get("text2sql_result")),
            chart=state.get("chart"),
            confidence=state.get("confidence"),
            limitations=state.get("limitations", []),
            plan=plan,
            agent_runs=[AgentRun(**run) for run in state.get("agent_runs", [])],
            agent_reflections=state.get("agent_reflections", []),
            supervisor_reviews=state.get("supervisor_reviews", []),
            supervisor_decisions=state.get("supervisor_decisions", []),
            retry_counts=state.get("retry_counts", {}),
            replan_count=state.get("replan_count", 0),
            reflection_decisions=state.get("reflection_decisions", []),
            termination_reason=state.get("termination_reason"),
            reflection=state.get("reflection", {}),
            citations=state.get("citations", []),
            grounding=state.get("grounding", {}),
            model_usage=state.get("model_usage", {}),
            answer_review=state.get("answer_review", {}),
        )


def _ungrounded_correction_topics(
    corrected: str, original: str, evidence: list[dict[str, Any]],
) -> list[str]:
    """A reviewer may repair wording, but may not add new domain hypotheses."""
    supported = " ".join([original, *(str(item.get("content") or "") for item in evidence)])
    topics = {
        "equipment_down": r"다운타임|장비\s*고장|equipment\s+down|downtime",
        "product_mix": r"제품\s*mix|product\s+mix|제품\s*구성\s*변화",
        "queue_time": r"queue\s*time|대기\s*시간",
        "material_shortage": r"자재\s*부족|material\s+shortage",
    }
    return [name for name, pattern in topics.items()
            if re.search(pattern, corrected, re.IGNORECASE)
            and not re.search(pattern, supported, re.IGNORECASE)]
