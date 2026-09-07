"""Evaluate stateful FAB follow-up routing, context restoration, and answer review."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Protocol

APP_ROOT = Path(__file__).resolve().parents[1]
APP_SRC = APP_ROOT / "src"
sys.path.insert(0, str(APP_SRC))

from app.schemas.chat import ChatRequest, ChatResponse
from app.services.chat_service import ChatService
from app.services.conversation_memory import ConversationMemory

DEFAULT_FIXTURE = APP_ROOT / "tests" / "fixtures" / "followup_scenario_eval.json"


class ChatRunner(Protocol):
    def ask(self, request: ChatRequest) -> ChatResponse: ...


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument(
        "--live",
        action="store_true",
        help="Allow configured Azure LLM calls and database queries.",
    )
    return parser.parse_args()


def evaluate_sequences(
    sequences: list[dict[str, Any]],
    *,
    runner_factory: Any = None,
) -> dict[str, Any]:
    factory = runner_factory or (lambda: ChatService(memory=ConversationMemory()))
    results = []
    for sequence in sequences:
        runner = factory()
        conversation_id = None
        turn_results = []
        for index, turn in enumerate(sequence["turns"], start=1):
            response = runner.ask(
                ChatRequest(message=turn["message"], conversation_id=conversation_id)
            )
            conversation_id = response.conversation_id
            failures = _evaluate_turn(turn, response)
            turn_results.append(
                {
                    "turn": index,
                    "message": turn["message"],
                    "status": response.status,
                    "query_type": response.query_type,
                    "agents": [run["agent"] for run in response.agent_runs],
                    "failures": failures,
                    "passed": not failures,
                }
            )
        results.append(
            {
                "id": sequence["id"],
                "passed": all(turn["passed"] for turn in turn_results),
                "turns": turn_results,
            }
        )

    total_turns = sum(len(result["turns"]) for result in results)
    passed_turns = sum(turn["passed"] for result in results for turn in result["turns"])
    return {
        "summary": {
            "sequence_count": len(results),
            "sequence_pass": sum(result["passed"] for result in results),
            "turn_count": total_turns,
            "turn_pass": passed_turns,
            "turn_pass_rate": round(passed_turns / total_turns, 4) if total_turns else 0.0,
        },
        "results": results,
    }


def _evaluate_turn(turn: dict[str, Any], response: ChatResponse) -> list[str]:
    failures = []
    if response.status != turn["expected_status"]:
        failures.append(f"status expected={turn['expected_status']} actual={response.status}")
    if response.query_type != turn["expected_query_type"]:
        failures.append(
            f"query_type expected={turn['expected_query_type']} actual={response.query_type}"
        )
    actual_agents = [run["agent"] for run in response.agent_runs]
    if actual_agents != turn["expected_agents"]:
        failures.append(f"agents expected={turn['expected_agents']} actual={actual_agents}")

    user_turn = next(
        (item for item in reversed(response.conversation_history) if item["role"] == "user"),
        {},
    )
    actual_context = user_turn.get("metadata") or {}
    for key, expected in (turn.get("expected_context") or {}).items():
        if actual_context.get(key) != expected:
            failures.append(f"context {key} expected={expected} actual={actual_context.get(key)}")
    if not response.answer_review.get("approved", False):
        failures.append("answer supervisor did not approve the final answer")
    return failures


def main() -> int:
    args = parse_args()
    if not args.live:
        print(
            "Refusing live evaluation without --live. This run can send follow-up questions and "
            "conversation context to the configured Azure endpoint and execute database queries.",
            file=sys.stderr,
        )
        return 2
    sequences = json.loads(args.fixture.read_text(encoding="utf-8"))
    report = evaluate_sequences(sequences)
    print(json.dumps(report if args.as_json else report["summary"], ensure_ascii=False, indent=2))
    return 0 if report["summary"]["sequence_pass"] == len(sequences) else 1


if __name__ == "__main__":
    raise SystemExit(main())
