"""Repair legacy links only from an observed browser trace ID and an unambiguous question.

Original feedback rows stay immutable. The corrected history is an audited overlay.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime

from app.services.state_store import AssistantStateStore, _to_json


def repair_legacy_feedback(store: AssistantStateStore, *, trace_id: str,
                           question: str, reason: str, apply: bool = False) -> dict:
    if not reason.strip():
        raise ValueError("A browser observation/audit reason is required")
    with store._connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        records = connection.execute(
            "SELECT feedback_id, conversation_id, helpful, comment, history_json "
            "FROM feedback WHERE trace_id=?", (trace_id,),
        ).fetchall()
        if len(records) != 1:
            raise ValueError("Expected exactly one legacy feedback row for this browser trace")
        feedback_id, conversation_id, helpful, comment, raw_history = records[0]
        original = json.loads(raw_history)
        if any(f.get("feedback_id") == feedback_id and f.get("target_message_id")
               for turn in original for f in turn.get("metadata", {}).get("user_feedback", [])):
            raise ValueError("This is a modern server-linked vote, not legacy feedback")
        rows = connection.execute(
            "SELECT id, role, content, metadata_json FROM conversation_turns "
            "WHERE conversation_id=? ORDER BY id", (conversation_id,),
        ).fetchall()
        targets = [i for i, row in enumerate(rows) if row[1] == "assistant" and i > 0
                   and rows[i - 1][1] == "user" and rows[i - 1][2] == question]
        if len(targets) != 1:
            raise ValueError("The observed question must identify exactly one stored exchange")
        index = targets[0]
        metadata = json.loads(rows[index][3])
        message_id = metadata["message_id"]
        result = {"feedback_id": feedback_id, "message_id": message_id,
                  "trace_id": trace_id, "question": question, "helpful": bool(helpful),
                  "applied": False}
        existing = connection.execute("SELECT message_id FROM feedback_relinks WHERE feedback_id=?",
                                      (feedback_id,)).fetchone()
        if existing:
            if existing[0] != message_id:
                raise ValueError("An existing repair points to a different answer")
            return {**result, "applied": True, "already_repaired": True}
        if not apply:
            return result
        # Move just the confirmed vote, never another user's feedback on the same answer.
        corrected = []
        for i, row in enumerate(rows):
            meta = json.loads(row[3])
            votes = meta.get("user_feedback", [])
            filtered = [vote for vote in votes if vote.get("trace_id") != trace_id]
            if i == index:
                filtered.append({"helpful": bool(helpful), "comment": comment,
                                 "trace_id": trace_id, "feedback_id": feedback_id,
                                 "target_message_id": message_id})
            if filtered != votes:
                if filtered:
                    meta["user_feedback"] = filtered
                else:
                    meta.pop("user_feedback", None)
                connection.execute("UPDATE conversation_turns SET metadata_json=? WHERE id=?",
                                   (_to_json(meta), row[0]))
            if i <= index:
                corrected.append({"role": row[1], "content": row[2], "metadata": meta})
        now = datetime.now(UTC).isoformat()
        connection.execute("INSERT INTO feedback_relinks VALUES (?, ?, ?, ?, ?)",
                           (feedback_id, message_id, _to_json(corrected[-24:]), reason, now))
        eligible = helpful and metadata.get("status") == "succeeded"
        connection.execute(
            "INSERT INTO feedback_examples (message_id, feedback_id, conversation_id, query_type, "
            "question, answer, status, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (message_id, feedback_id, conversation_id, metadata.get("query_type", ""),
             question, rows[index][2], "pending" if eligible else "excluded", now),
        )
        return {**result, "applied": True}
