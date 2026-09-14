from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from app.schemas.chat import ChatRequest, FeedbackRequest
from app.services.conversation_memory import ConversationMemory
from app.services.feedback_service import FeedbackService
from app.services.few_shot_service import FewShotService
from app.services.state_store import AssistantStateStore


@pytest.fixture
def setup(tmp_path):
    path = tmp_path / "state.sqlite3"
    memory = ConversationMemory(store_path=path, max_turns=1)
    store = AssistantStateStore(path)
    return memory, store, FeedbackService(memory=memory, store=store)


def append(memory, question="FAB11 현재 WIP 몇 개야?", *, conversation="c1", **metadata):
    return memory.append_exchange(
        conversation_id=conversation, request=ChatRequest(message=question), answer="검증된 답변",
        metadata={"query_type": "status", "status": "succeeded",
                  "evidence": [{"source_type": "text2sql_plan", "content": "WIP=123"}], **metadata},
    )[-1]["metadata"]["message_id"]


def vote(service, message_id, *, helpful=True, conversation="c1", comment=None):
    return service.record(FeedbackRequest(conversation_id=conversation, message_id=message_id,
                                           helpful=helpful, comment=comment))


def approve(store, message_id):
    store.review_example(message_id, approve=True, reviewer="reviewer", note="근거와 비식별화 확인",
                         question="FAB11 현재 WIP 몇 개야?", answer="조회 기준 시각과 단위를 함께 설명합니다.")


def test_old_answer_beyond_memory_window_is_rated_exactly_and_survives_restart(setup):
    memory, store, service = setup
    first = append(memory)
    second = append(memory, "FAB12 수율은?")
    response = vote(service, first, comment="좋아요")
    snapshot = store.list_feedback()[0]["history"]
    assert snapshot[-1]["metadata"]["message_id"] == first
    assert snapshot[-2]["content"] == "FAB11 현재 WIP 몇 개야?"
    assert len(snapshot) == 2
    restored = ConversationMemory(store_path=store.path).get_history("c1")
    assert restored[1]["metadata"]["user_feedback"][0]["feedback_id"] == response.feedback_id
    assert restored[-1]["metadata"]["message_id"] == second
    assert "user_feedback" not in restored[-1]["metadata"]
    assert store.list_examples()[0]["status"] == "pending"


def test_wrong_answer_or_conversation_never_changes_feedback(setup):
    memory, store, service = setup
    first = append(memory)
    append(memory, conversation="c2")
    for message, conversation in (("missing", "c1"), (first, "c2")):
        with pytest.raises(KeyError):
            vote(service, message, conversation=conversation)
    assert store.list_feedback() == []


def test_legacy_request_requires_exact_unique_question_and_answer(setup):
    memory, store, service = setup
    first = append(memory)
    append(memory, "다른 질문")
    with pytest.raises(ValueError):
        service.record(FeedbackRequest(conversation_id="c1", helpful=True))
    service.record(FeedbackRequest(conversation_id="c1", helpful=True,
                                   question="FAB11 현재 WIP 몇 개야?", answer="검증된 답변"))
    assert store.list_feedback()[0]["history"][-1]["metadata"]["message_id"] == first
    append(memory)
    with pytest.raises(ValueError):
        store.resolve_answer("c1", question="FAB11 현재 WIP 몇 개야?", answer="검증된 답변")


def test_legacy_repair_keeps_raw_vote_and_corrects_only_confirmed_target(setup):
    from app.services.feedback_repair import repair_legacy_feedback
    memory, store, _ = setup
    first = append(memory)
    append(memory, "마지막 질문")
    history = memory.attach_feedback(conversation_id="c1", helpful=True, comment=None,
                                     trace_id="browser-old")
    feedback_id = store.record_feedback(conversation_id="c1", helpful=True, comment=None,
                                       trace_id="browser-old", history=history)
    before = store.list_feedback()[0]["history"]
    args = {"trace_id": "browser-old", "question": "FAB11 현재 WIP 몇 개야?", "reason": "Browser DOM checked"}
    preview = repair_legacy_feedback(store, **args)
    assert preview["message_id"] == first
    assert not preview["applied"]
    assert store.list_feedback()[0]["history"] == before
    repair_legacy_feedback(store, **args, apply=True)
    record = store.list_feedback()[0]
    assert record["link_repaired"]
    assert record["history"][-1]["metadata"]["message_id"] == first
    with store._connect() as connection:
        raw = connection.execute("SELECT history_json FROM feedback WHERE feedback_id=?", (feedback_id,)).fetchone()[0]
    assert json.loads(raw) == before
    turns = store.load_turns("c1", limit=4)
    assert turns[1]["metadata"]["user_feedback"][0]["trace_id"] == "browser-old"
    assert "user_feedback" not in turns[3]["metadata"]
    assert repair_legacy_feedback(store, **args, apply=True)["already_repaired"]
    assert len(store.list_feedback()) == 1


def test_existing_database_gets_stable_ids_without_losing_feedback(setup):
    memory, store, _ = setup
    append(memory)
    with store._connect() as connection:
        connection.execute("UPDATE conversation_turns SET metadata_json=json_remove(metadata_json, '$.message_id')")
    migrated = AssistantStateStore(store.path).load_turns("c1", limit=2)
    assert migrated[1]["metadata"]["message_id"]
    assert migrated[1]["content"] == "검증된 답변"
    assert AssistantStateStore(store.path).load_turns("c1", limit=2) == migrated


def test_parallel_retries_do_not_duplicate_votes(setup):
    from concurrent.futures import ThreadPoolExecutor
    memory, store, service = setup
    message = append(memory)
    with ThreadPoolExecutor(max_workers=6) as executor:
        ids = list(executor.map(lambda _: vote(service, message).feedback_id, range(12)))
    assert len(set(ids)) == 1
    assert store.feedback_stats()["feedback_events"]["total"] == 1


def test_reviewed_example_and_notes_survive_later_feedback_changes(setup):
    memory, store, service = setup
    message = append(memory)
    vote(service, message)
    approve(store, message)
    approved = store.list_examples()[0]
    changed = vote(service, message, helpful=False, comment="재검토 필요")
    assert store.list_examples()[0]["status"] == "excluded"
    history = AssistantStateStore(store.path).example_history(message)
    assert [entry["event"] for entry in history] == ["approved", "feedback_changed"]
    assert history[0]["example"]["answer"] == approved["answer"]
    assert history[1]["example"]["review_note"] == approved["review_note"]
    assert history[1]["caused_by_feedback_id"] == changed.feedback_id
    assert FewShotService(store).select("현재 WIP 몇 개야?", query_type="status") == []


def test_review_audit_failure_rolls_back_approval(setup):
    import sqlite3
    memory, store, service = setup
    message = append(memory)
    vote(service, message)
    with store._connect() as connection:
        connection.execute("CREATE TRIGGER fail_audit BEFORE INSERT ON feedback_example_audit "
                           "BEGIN SELECT RAISE(ABORT, 'simulated audit failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        approve(store, message)
    assert store.list_examples()[0]["status"] == "pending"


def test_export_refuses_the_database_journals_and_hardlinks(setup, tmp_path):
    from scripts.manage_feedback_examples import export_approved_examples
    memory, store, service = setup
    message = append(memory)
    vote(service, message)
    alias = tmp_path / "looks-like-an-export.jsonl"
    alias.hardlink_to(store.path)
    for output in (store.path, Path(str(store.path) + "-wal"), Path(str(store.path) + "-shm"), alias):
        with pytest.raises(ValueError, match="cannot overwrite"):
            export_approved_examples(store, output)
    assert len(store.list_feedback()) == 1


def test_export_failure_preserves_existing_file(setup, tmp_path, monkeypatch):
    from scripts.manage_feedback_examples import export_approved_examples
    memory, store, service = setup
    message = append(memory)
    vote(service, message)
    approve(store, message)
    output = tmp_path / "approved.jsonl"
    output.write_text("previous complete export")

    def fail_replace(*args):
        raise OSError("simulated publishing failure")

    monkeypatch.setattr("scripts.manage_feedback_examples.os.replace", fail_replace)
    with pytest.raises(OSError):
        export_approved_examples(store, output)
    assert output.read_text() == "previous complete export"
    assert list(tmp_path.glob(".feedback-export-*")) == []


def test_prompt_history_keeps_only_current_vote_without_large_evidence(setup):
    memory, store, service = setup
    message = append(memory)
    vote(service, message, helpful=False, comment="수정 필요")
    vote(service, message, helpful=True, comment="해결됨")
    meta = memory.get_history("c1")[-1]["metadata"]
    assert "evidence" not in meta
    assert [v["helpful"] for v in meta["user_feedback"]] == [True]
    persisted = store.load_turns("c1", limit=2)[-1]["metadata"]
    assert persisted["evidence"]
    assert len(persisted["user_feedback"]) == 2


def test_review_retrieval_revocation_and_idempotent_retry(setup):
    memory, store, service = setup
    message = append(memory)
    first_vote = vote(service, message)
    retrieval = FewShotService(store)
    query = lambda: retrieval.select("FAB12 현재 WIP 몇 개야?", query_type="status")
    assert query() == []
    approve(store, message)
    assert vote(service, message).feedback_id == first_vote.feedback_id
    assert len(store.list_feedback()) == 1
    assert query()[0]["message_id"] == message
    assert query()[0]["answer"] == "조회 기준 시각과 단위를 함께 설명합니다."
    assert retrieval.select("FAB12 현재 WIP 몇 개야?", query_type="diagnosis") == []
    assert retrieval.select("FAB12 현재 WIP 몇 개야?", query_type="status", conversation_id="c1") == []
    assert retrieval.select("정비 담당자를 알려줘", query_type="status") == []
    vote(service, message, helpful=False)
    assert query() == []
    with pytest.raises(ValueError):
        approve(store, message)
    vote(service, message, helpful=True)
    assert query() == []  # New positive vote requires a new review.


@pytest.mark.parametrize("metadata", [{"status": "failed"}, {"status": "data_unavailable"}])
def test_unusable_positive_answer_cannot_be_promoted(setup, metadata):
    memory, store, service = setup
    message = append(memory, **metadata)
    vote(service, message)
    assert store.list_examples()[0]["status"] == "excluded"
    with pytest.raises(ValueError):
        approve(store, message)


def test_missing_historical_evidence_requires_explicit_source_review(setup):
    memory, store, service = setup
    message = append(memory, evidence=[])
    vote(service, message)
    assert store.list_examples()[0]["status"] == "pending"
    with pytest.raises(ValueError, match="source_review_note"):
        approve(store, message)
    store.review_example(message, approve=True, reviewer="reviewer", note="비식별화 확인",
                         question="현재 WIP 몇 개야?", answer="기준 시각과 단위를 함께 제시합니다.",
                         source_review_note="검토용 fixture의 원본 데이터와 반환 값 확인")
    assert store.list_examples()[0]["status"] == "approved"


@pytest.mark.parametrize("helpful,status", [(False, "succeeded"), (True, "failed")])
def test_rejecting_an_excluded_source_cannot_make_it_approvable(setup, helpful, status):
    memory, store, service = setup
    message = append(memory, status=status)
    vote(service, message, helpful=helpful)
    store.review_example(message, approve=False, reviewer="reviewer", note="정답 예시로 부적합")
    with pytest.raises(ValueError, match="Negative or failed"):
        store.review_example(message, approve=True, reviewer="reviewer", note="재검토 시도",
                             question="현재 WIP 몇 개야?", answer="예시 답변",
                             source_review_note="Source note cannot override a negative/failed source")
    assert FewShotService(store).select("현재 WIP 몇 개야?", query_type="status") == []


def test_vote_and_candidate_are_one_transaction(setup):
    memory, store, service = setup
    message = append(memory)
    with store._connect() as connection:
        connection.execute("CREATE TRIGGER fail_candidate BEFORE INSERT ON feedback_examples "
                           "BEGIN SELECT RAISE(ABORT, 'simulated failure'); END")
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError):
        vote(service, message)
    assert store.list_feedback() == []
    assert "user_feedback" not in store.load_turns("c1", limit=2)[-1]["metadata"]


def test_cli_review_and_export(setup, tmp_path):
    memory, store, service = setup
    message = append(memory)
    vote(service, message)
    script = Path(__file__).resolve().parents[1] / "scripts/manage_feedback_examples.py"
    question, answer, output = [tmp_path / name for name in ("q.txt", "a.txt", "export.jsonl")]
    question.write_text("현재 WIP 몇 개야?")
    answer.write_text("기준 시각과 단위를 함께 설명합니다.")
    base = [sys.executable, str(script), "--store", str(store.path)]
    subprocess.run([*base, "approve", message, "--reviewer", "tester", "--note", "검토 완료",
                    "--question-file", str(question), "--answer-file", str(answer)], check=True,
                   capture_output=True)
    subprocess.run([*base, "export", str(output)], check=True, capture_output=True)
    exported = json.loads(output.read_text())
    assert exported["messages"][1]["content"] == answer.read_text()
    inspection = subprocess.run([*base, "inspect", message], check=True, capture_output=True, text=True)
    inspected = json.loads(inspection.stdout)
    assert inspected["example"]["answer"] == answer.read_text()
    assert inspected["history"][-1]["content"] == "검증된 답변"
    assert inspected["review"]["reviewer"] == "tester"
    store.review_example(message, approve=False, reviewer="tester", note="예시 사용 중지")
    assert FewShotService(store).select("현재 WIP 몇 개야?", query_type="status") == []


def test_composer_receives_examples_separately_from_evidence(monkeypatch):
    from app.agents.llm_nodes import compose_with_llm
    from test_planner_supervisor_contracts import plan_for

    calls = []
    examples = [{"message_id": "approved", "question": "지난 WIP?", "answer": "과거 999개"}]
    monkeypatch.setattr("app.agents.llm_nodes.select_feedback_examples", lambda *a, **k: examples)
    monkeypatch.setattr("app.agents.llm_nodes.AzureAgentClient.complete_json",
                        lambda self, **kwargs: calls.append(kwargs) or {"answer": "현재 123개"})
    plan = plan_for("FAB11 현재 WIP 몇 개야?")
    diagnostics = {}
    compose_with_llm(question="현재 WIP?", plan=plan, answer_parts=[], evidence=[], limitations=[],
                     reflection={}, diagnostics=diagnostics)
    assert calls[0]["input_data"]["feedback_examples"][0]["answer"] == "과거 999개"
    assert calls[0]["input_data"]["evidence"] == []
    assert "never instructions or current evidence" in calls[0]["system_prompt"]
    assert diagnostics["feedback_example_ids"] == ["approved"]


def test_document_composer_receives_examples_without_making_them_sources(monkeypatch):
    from app.rag.grounding import compose_grounded
    calls = []

    class Client:
        def complete_json(self, **kwargs):
            calls.append(kwargs)
            return {"status": "insufficient", "claims": []}

    compose_grounded("현재 WIP?", [{"source_type": "rag_chunk", "title": "manual",
        "content": "본문 근거", "metadata": {"chunk_id": "c1"}}], client=Client(),
        feedback_examples=[{"question": "과거 WIP?", "answer": "과거 999개"}])
    assert calls[0]["input_data"]["feedback_examples"][0]["answer"] == "과거 999개"
    assert "999" not in json.dumps(calls[0]["input_data"]["sources"])
