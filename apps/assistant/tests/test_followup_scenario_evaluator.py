import sys

from app.schemas.chat import ChatResponse
from scripts.evaluate_followup_scenarios import evaluate_sequences, main


class FakeRunner:
    def __init__(self) -> None:
        self.turn = 0

    def ask(self, request):
        self.turn += 1
        return ChatResponse(
            conversation_id=request.conversation_id or "conversation-1",
            status="succeeded",
            query_type="status",
            answer="fab10 Dry_Etch WIP result",
            conversation_history=[
                {
                    "role": "user",
                    "content": request.message,
                    "metadata": {"fab": "fab10", "process": "Dry_Etch"},
                }
            ],
            agent_runs=[{"agent": "text2sql", "status": "succeeded", "summary": "ok"}],
            answer_review={"approved": True},
        )


def test_followup_evaluator_scores_context_and_agents() -> None:
    sequences = [
        {
            "id": "process",
            "turns": [
                {
                    "message": "그 공정 WIP 보여줘",
                    "expected_status": "succeeded",
                    "expected_query_type": "status",
                    "expected_agents": ["text2sql"],
                    "expected_context": {"fab": "fab10", "process": "Dry_Etch"},
                }
            ],
        }
    ]

    report = evaluate_sequences(sequences, runner_factory=FakeRunner)

    assert report["summary"]["sequence_pass"] == 1
    assert report["summary"]["turn_pass_rate"] == 1.0


def test_followup_evaluator_requires_explicit_live_opt_in(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["evaluate_followup_scenarios.py"])

    assert main() == 2
    assert "without --live" in capsys.readouterr().err
