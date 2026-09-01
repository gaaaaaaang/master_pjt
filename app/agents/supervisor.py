from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import uuid4

from app.agents.planner import PlannerDecision, create_plan
from app.agents.prompts import SUPERVISOR_PROMPT_VERSION, SUPERVISOR_SYSTEM_PROMPT
from app.schemas.chat import ChatRequest, Evidence
from app.sub_agent.case_search import find_similar_cases
from app.sub_agent.impact import estimate_output_delta
from app.sub_agent.rag import retrieve_knowledge
from app.sub_agent.reflection import verify_response
from app.sub_agent.text2sql import Text2SQLResult, answer_question
from app.sub_agent.visualization import build_chart_spec

SupervisorStatus = Literal[
    "succeeded",
    "needs_clarification",
    "data_unavailable",
    "unsupported",
    "failed",
    "needs_replan",
]


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
    sql: str | None = None
    chart: dict[str, Any] | None = None
    confidence: float | None = None
    limitations: list[str] = field(default_factory=list)
    plan: PlannerDecision | None = None
    agent_runs: list[AgentRun] = field(default_factory=list)
    reflection: dict[str, Any] = field(default_factory=dict)
    prompt_version: str = SUPERVISOR_PROMPT_VERSION
    prompt_contract: str = SUPERVISOR_SYSTEM_PROMPT


class Supervisor:
    """Execute Planner decisions and control sub-agent branching."""

    def run(self, request: ChatRequest) -> SupervisorResult:
        conversation_id = request.conversation_id or str(uuid4())
        plan = create_plan(request.message, fab=request.fab)
        evidence = [_planner_evidence(plan)]
        limitations = list(plan.limitations)
        agent_runs: list[AgentRun] = []

        if plan.status == "needs_clarification":
            return self._finalize(
                conversation_id=conversation_id,
                status="needs_clarification",
                query_type=plan.query_type,
                answer=plan.clarification_question or "추가 정보가 필요합니다.",
                evidence=evidence,
                limitations=limitations,
                plan=plan,
                agent_runs=agent_runs,
            )

        if plan.status == "unsupported":
            return self._finalize(
                conversation_id=conversation_id,
                status="unsupported",
                query_type=plan.query_type,
                answer="현재 지원 범위 밖의 질문입니다.",
                evidence=evidence,
                limitations=limitations,
                plan=plan,
                agent_runs=agent_runs,
            )

        text2sql_result: Text2SQLResult | None = None
        sql: str | None = None
        chart: dict[str, Any] | None = None
        confidence: float | None = None
        answer_parts: list[str] = []

        for agent_name in plan.selected_sub_agents:
            if agent_name == "text2sql":
                text2sql_result = answer_question(request.message, fab=request.fab)
                sql = text2sql_result.sql
                confidence = text2sql_result.confidence
                limitations.extend(text2sql_result.limitations)
                evidence.append(_text2sql_evidence(text2sql_result))
                agent_runs.append(
                    AgentRun(
                        agent="text2sql",
                        status=text2sql_result.status,
                        summary=text2sql_result.answer,
                    )
                )

                if text2sql_result.status == "needs_clarification":
                    return self._finalize(
                        conversation_id=conversation_id,
                        status="needs_clarification",
                        query_type=plan.query_type,
                        answer=text2sql_result.answer,
                        evidence=evidence,
                        sql=sql,
                        confidence=confidence,
                        limitations=limitations,
                        plan=plan,
                        agent_runs=agent_runs,
                    )

                if text2sql_result.status in {"data_unavailable", "unsupported", "failed"}:
                    if plan.query_type in {"status", "master_data_lookup", "release_plan_lookup"}:
                        return self._finalize(
                            conversation_id=conversation_id,
                            status=text2sql_result.status,
                            query_type=plan.query_type,
                            answer=text2sql_result.answer,
                            evidence=evidence,
                            sql=sql,
                            confidence=confidence,
                            limitations=limitations,
                            plan=plan,
                            agent_runs=agent_runs,
                        )
                    answer_parts.append(text2sql_result.answer)
                    continue

                answer_parts.append(text2sql_result.answer)

            elif agent_name == "rag":
                try:
                    rag_evidence = retrieve_knowledge(request.message)
                except NotImplementedError as exc:
                    limitations.append(str(exc))
                    agent_runs.append(AgentRun("rag", "data_unavailable", str(exc)))
                else:
                    evidence.extend(rag_evidence)
                    agent_runs.append(
                        AgentRun("rag", "succeeded", f"retrieved {len(rag_evidence)} evidence chunks")
                    )

            elif agent_name == "case_search":
                case_evidence = find_similar_cases(request.message)
                evidence.extend(case_evidence)
                agent_runs.append(
                    AgentRun("case_search", "succeeded", f"found {len(case_evidence)} similar cases")
                )

            elif agent_name == "impact":
                impact = estimate_output_delta(
                    baseline={},
                    scenario={"question": request.message, "fab": request.fab},
                )
                limitations.extend(impact.get("limitations", []))
                agent_runs.append(AgentRun("impact", "placeholder", "impact model is not connected"))
                answer_parts.append("영향도 계산은 operational metric 적재 후 활성화해야 합니다.")

            elif agent_name == "visualization":
                rows = text2sql_result.rows if text2sql_result else []
                if rows:
                    chart = build_chart_spec("FAB trend/comparison", rows)
                    agent_runs.append(AgentRun("visualization", "succeeded", "chart spec generated"))
                else:
                    limitations.append("Visualization은 조회 row가 있을 때만 생성합니다.")
                    agent_runs.append(AgentRun("visualization", "skipped", "no rows available"))

        answer = _compose_answer(plan, answer_parts, limitations)
        return self._finalize(
            conversation_id=conversation_id,
            status=_status_from_runs(agent_runs),
            query_type=plan.query_type,
            answer=answer,
            evidence=evidence,
            sql=sql,
            chart=chart,
            confidence=confidence,
            limitations=limitations,
            plan=plan,
            agent_runs=agent_runs,
        )

    def _finalize(
        self,
        *,
        conversation_id: str,
        status: SupervisorStatus,
        query_type: str,
        answer: str,
        evidence: list[Evidence],
        limitations: list[str],
        plan: PlannerDecision,
        agent_runs: list[AgentRun],
        sql: str | None = None,
        chart: dict[str, Any] | None = None,
        confidence: float | None = None,
    ) -> SupervisorResult:
        unique_limitations = list(dict.fromkeys(limitations))
        reflection = verify_response(
            answer,
            evidence=[item.model_dump() for item in evidence],
            limitations=unique_limitations,
            query_type=query_type,
        )
        if reflection.get("warnings"):
            unique_limitations.extend(
                warning for warning in reflection["warnings"] if warning not in unique_limitations
            )
        return SupervisorResult(
            conversation_id=conversation_id,
            status=status,
            query_type=query_type,
            answer=answer,
            evidence=evidence,
            sql=sql,
            chart=chart,
            confidence=confidence,
            limitations=unique_limitations,
            plan=plan,
            agent_runs=agent_runs,
            reflection=reflection,
        )


def _planner_evidence(plan: PlannerDecision) -> Evidence:
    return Evidence(
        source_type="planner_plan",
        title="Planner plan",
        content=plan.intent,
        metadata={
            "status": plan.status,
            "query_type": plan.query_type,
            "selected_sub_agents": plan.selected_sub_agents,
            "missing_slots": plan.missing_slots,
            "execution_steps": [
                {
                    "agent": step.agent,
                    "action": step.action,
                    "required": step.required,
                    "reason": step.reason,
                }
                for step in plan.execution_steps
            ],
            "prompt_version": plan.prompt_version,
        },
    )


def _text2sql_evidence(result: Text2SQLResult) -> Evidence:
    metadata: dict[str, Any] = {
        "status": result.status,
        "query_type": result.query_type,
        "row_count": result.row_count,
    }
    if result.plan:
        metadata.update(
            {
                "template_id": result.plan.template_id,
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
            }
        )
    return Evidence(
        source_type="text2sql_plan",
        title="Text2SQL result",
        content=result.plan.template_id if result.plan and result.plan.template_id else result.status,
        metadata=metadata,
    )


def _compose_answer(plan: PlannerDecision, answer_parts: list[str], limitations: list[str]) -> str:
    if answer_parts:
        return "\n\n".join(dict.fromkeys(answer_parts))
    if plan.query_type == "diagnosis":
        return "원인 진단은 SQL 근거와 RAG 근거를 결합해야 합니다. 현재 필요한 근거가 부족합니다."
    if plan.query_type == "impact":
        return "영향도 계산은 기준 지표와 operational metric 적재 후 수행할 수 있습니다."
    if plan.query_type == "trend":
        return "추세/비교 조회는 AutoSched 기반 시계열 데이터 적재 후 활성화해야 합니다."
    if limitations:
        return limitations[0]
    return "요청을 처리할 실행 결과가 없습니다."


def _status_from_runs(agent_runs: list[AgentRun]) -> SupervisorStatus:
    statuses = {run.status for run in agent_runs}
    if "failed" in statuses:
        return "failed"
    if "needs_clarification" in statuses:
        return "needs_clarification"
    if "data_unavailable" in statuses:
        return "data_unavailable"
    if "unsupported" in statuses:
        return "unsupported"
    return "succeeded"
