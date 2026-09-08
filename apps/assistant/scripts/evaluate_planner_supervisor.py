"""Evaluate frozen planner/supervisor contracts without executing database queries.

Default mode forces the deterministic outage path. --live sends only fixture
questions, plans and synthetic review context to the configured LLM endpoint.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT / "src"))

from app.agents.planner import create_plan
from app.agents.supervisor import review_agent_result, review_plan
from app.config import Settings, get_settings


class OfflineClient:
    def complete_json(self, **kwargs):
        raise RuntimeError("deterministic evaluation")


def evaluate(case, client, *, require_llm=False):
    started = time.monotonic()
    plan = create_plan(case["question"], **case.get("context", {}), llm_client=client)
    reviewed, decision = review_plan(plan, case["question"], llm_client=client)
    failures = []
    llm_completed = plan.execution_mode == "llm_chat_completions" and not decision.get(
        "fallback_used"
    )
    if require_llm and not llm_completed:
        failures.append("live LLM execution unavailable; fallback is not a live quality result")
    for key, actual in (
        ("status", reviewed.status),
        ("query_type", reviewed.query_type),
        ("agents", reviewed.selected_sub_agents),
    ):
        if key in case and case[key] != actual:
            failures.append(f"{key}: expected={case[key]!r}, actual={actual!r}")
    if case.get("query_type_any") and reviewed.query_type not in case["query_type_any"]:
        failures.append(f"query_type not in {case['query_type_any']!r}: {reviewed.query_type}")
    for key, expected in case.get("slots", {}).items():
        actual = reviewed.slots[key].value if key in reviewed.slots else None
        if actual != expected:
            failures.append(f"slot {key}: expected={expected!r}, actual={actual!r}")
    if reviewed.status == "ready" and not reviewed.answer_requirements:
        failures.append("missing answer requirements")
    return {
        "id": case["id"],
        "question": case["question"],
        "passed": not failures,
        "failures": failures,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "llm_completed": llm_completed,
        "plan": asdict(plan),
        "supervisor": decision,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fixture", type=Path, default=APP_ROOT / "tests/fixtures/planner_supervisor_eval.json"
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--suite", choices=["planner", "recovery", "graph"], default="planner")
    args = parser.parse_args()
    if args.env_file:
        settings = Settings(_env_file=args.env_file)
        cached = get_settings()
        for key in ("openai_api_key", "openai_endpoint", "openai_model", "openai_api_version"):
            setattr(cached, key, getattr(settings, key))
    if args.suite == "recovery":
        return evaluate_recovery_suite(args)
    if args.suite == "graph":
        if not args.live:
            parser.error("--suite graph requires --live; all tool data is synthetic.")
        return evaluate_graph_suite(args)
    cases = json.loads(args.fixture.read_text())
    if args.limit:
        cases = cases[: args.limit]
    client = None if args.live else OfflineClient()
    results = []
    args.output.parent.mkdir(parents=True, exist_ok=True)
    for case in cases:
        result = evaluate(case, client, require_llm=args.live)
        results.append(result)
        report = {
            "mode": "live_questions_only" if args.live else "deterministic",
            "completed": len(results),
            "total": len(cases),
            "passed": sum(item["passed"] for item in results),
            "llm_completed": sum(item["llm_completed"] for item in results),
            "results": results,
        }
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(
            f"{case['id']}: {'PASS' if result['passed'] else 'FAIL'} {result['failures']}",
            flush=True,
        )
    return 0 if all(item["passed"] for item in results) else 1


def evaluate_recovery_suite(args):
    from app.agents.execution import requirement_coverage

    offline = OfflineClient()
    cases = [
        ("REC-001", "fab10 현재 WIP", "text2sql", "succeeded", [], {"compose", "continue"}, 2, 1),
        (
            "REC-002",
            "fab10 WIP 원인 후보",
            "text2sql",
            "succeeded",
            ["rag", "case_search"],
            {"continue"},
            2,
            1,
        ),
        (
            "REC-003",
            "fab10 현재 WIP",
            "text2sql",
            "failed",
            [],
            {"retry_same_agent", "replan", "retry_agents"},
            2,
            1,
        ),
        (
            "REC-004",
            "fab10 현재 Queue Time",
            "text2sql",
            "data_unavailable",
            [],
            {"continue"},
            0,
            0,
        ),
        (
            "REC-005",
            "fab10 WIP 원인 후보",
            "rag",
            "data_unavailable",
            ["case_search"],
            {"continue", "alternate_agent"},
            0,
            0,
        ),
        ("REC-006", "fab10 현재 WIP", "text2sql", "failed", [], {"continue"}, 0, 0),
    ]
    results = []
    for case_id, question, agent, status, pending, allowed, retries, replans in cases:
        plan = create_plan(question, llm_client=offline)
        summary = {
            "succeeded": "합성 테스트 데이터: fab10 WIP=123개. 요청 대상과 지표 일치.",
            "failed": "Temporary HTTP 503 transport error. The same request may succeed on retry.",
            "data_unavailable": "필수 관측 데이터가 저장소에 없어 값을 계산할 수 없습니다.",
        }[status]
        active = {
            name: {"status": "succeeded", "summary": "합성 테스트 근거", "evidence": []}
            for name in plan.selected_sub_agents
            if name not in pending
        }
        active[agent] = {
            "status": status,
            "summary": summary,
            "evidence": [
                {
                    "source_type": "text2sql_plan" if agent == "text2sql" else "rag_chunk",
                    "title": "synthetic fixture",
                    "content": summary,
                    "metadata": {
                        "status": status,
                        "sample_rows": [{"wiplotavg": 123}] if status == "succeeded" else [],
                        "row_count": 1 if status == "succeeded" else 0,
                    },
                }
            ],
        }
        decision = review_agent_result(
            plan,
            {
                "agent_name": agent,
                "status": status,
                "reason": summary,
                "decision": "accepted" if status == "succeeded" else "needs_supervisor_review",
            },
            retry_count=0,
            retry_budget_remaining=retries,
            replan_budget_remaining=replans,
            alternate_budget_remaining=1,
            allowed_alternate_agents=["case_search"] if agent == "rag" else [],
            execution_context={
                "question": question,
                "active_results": active,
                "coverage": requirement_coverage(plan, active),
                "retry_counts": {},
                "remaining_steps": pending,
            },
            llm_client=None if args.live else offline,
        )
        passed = decision["action"] in allowed and not (args.live and decision.get("fallback_used"))
        results.append(
            {"id": case_id, "passed": passed, "allowed": sorted(allowed), "decision": decision}
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(
                {
                    "mode": "synthetic_recovery_live" if args.live else "offline",
                    "passed": sum(r["passed"] for r in results),
                    "total": len(cases),
                    "results": results,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        print(f"{case_id}: {'PASS' if passed else 'FAIL'} {decision['action']}", flush=True)
    return 0 if all(r["passed"] for r in results) else 1


def evaluate_graph_suite(args):
    from unittest.mock import patch

    from app.agents.graph import build_agent_graph, initial_graph_state
    from app.schemas.chat import ChatRequest, Evidence
    from app.sub_agent.text2sql import QueryPlan, Text2SQLResult

    cases = [
        ("GRAPH-001", "fab10 현재 WIP 몇 개야?", "status", [{"wiplotavg": 123}], None),
        (
            "GRAPH-002",
            "fab10 WIP 추세를 그래프로 보여줘",
            "trend",
            [
                {"report_date": "2020-01-01", "wiplotavg": 100},
                {"report_date": "2020-01-02", "wiplotavg": 120},
                {"report_date": "2020-01-03", "wiplotavg": 110},
            ],
            {"type": "line", "x": "report_date", "y": "wiplotavg", "series": None},
        ),
        (
            "GRAPH-003",
            "fab10 utilization이 5%p 떨어지면 capacity 영향은?",
            "status",
            [{"util_percent": 80}],
            None,
        ),
        ("GRAPH-004", "fab10 WIP 증가 원인 후보를 알려줘", "status", [{"wiplotavg": 123}], None),
    ]
    if args.limit:
        cases = cases[: args.limit]
    results = []
    for case_id, question, query_type, rows, chart in cases:
        sql_result = Text2SQLResult(
            status="succeeded",
            query_type=query_type,
            answer="합성 테스트 보고서 결과입니다.",
            sql="SELECT * FROM fab10.autosched_perf LIMIT 3",
            rows=rows,
            row_count=len(rows),
            columns=list(rows[0]),
            limitations=["합성 테스트 데이터이며 실제 FAB 운영 측정값이 아닙니다."],
            plan=QueryPlan(
                query_type=query_type, template_id=None, fab_id="fab10", chart_intent=chart
            ),
        )
        with (
            patch("app.agents.graph.answer_question", return_value=sql_result),
            patch(
                "app.agents.graph.retrieve_knowledge",
                return_value=[
                    Evidence(
                        source_type="rag_chunk",
                        title="합성 WIP 점검 지침",
                        content="WIP 증가 시 병목과 투입량 변화를 점검한다.",
                        metadata={
                            "knowledge_base": "incident_playbook",
                            "issue_types": "wip",
                            "source": "synthetic-test",
                        },
                    )
                ],
            ),
            patch("app.agents.graph.find_similar_cases", return_value=[]),
        ):
            started = time.monotonic()
            state = initial_graph_state(ChatRequest(message=question))
            node_order = []
            fallback_nodes = []
            for update in build_agent_graph().stream(
                state, config={"recursion_limit": 100}, stream_mode="updates"
            ):
                for node, delta in update.items():
                    state.update(delta)
                    node_order.append(node)
                    event_data = delta.get("stream_event", {}).get("data", {})
                    if (
                        event_data.get("execution_mode") == "deterministic_fallback"
                        or event_data.get("fallback_used")
                        or delta.get("answer_review", {}).get("fallback_used")
                    ):
                        fallback_nodes.append(node)
                    print(f"{case_id}: {node}", flush=True)
        result = {
            "id": case_id,
            "question": question,
            "status": state["status"],
            "answer": state.get("answer"),
            "node_order": node_order,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "llm_completed": not fallback_nodes,
            "fallback_nodes": fallback_nodes,
            "evidence": state.get("evidence"),
            "plan": asdict(state["plan"]),
            "limitations": state.get("limitations", []),
            "agent_runs": state.get("agent_runs"),
            "supervisor_decisions": state.get("supervisor_decisions"),
            "answer_review": state.get("answer_review"),
            "reflection": state.get("reflection"),
            "passed": bool(state.get("answer_review", {}).get("approved"))
            and state["status"] == "succeeded"
            and not fallback_nodes,
        }
        results.append(result)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(
                {
                    "mode": "live_graph_synthetic_tools",
                    "results": results,
                    "passed": sum(r["passed"] for r in results),
                    "total": len(cases),
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )
        print(f"{case_id}: {'PASS' if result['passed'] else 'FAIL'}", flush=True)
    return 0 if all(r["passed"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
