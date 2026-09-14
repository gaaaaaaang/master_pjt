from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4


class AssistantStateStore:
    """Durable local conversation and feedback store backed by SQLite."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def append_exchange(
        self,
        *,
        conversation_id: str,
        user_content: str,
        user_metadata: dict[str, Any],
        assistant_content: str,
        assistant_metadata: dict[str, Any],
    ) -> None:
        created_at = datetime.now(UTC).isoformat()
        rows = (
            (conversation_id, "user", user_content, _to_json(user_metadata), created_at),
            (
                conversation_id,
                "assistant",
                assistant_content,
                _to_json(assistant_metadata),
                created_at,
            ),
        )
        with self._connect() as connection:
            connection.executemany(
                """
                INSERT INTO conversation_turns (
                    conversation_id, role, content, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                rows,
            )

    def load_turns(self, conversation_id: str, *, limit: int) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT role, content, metadata_json
                FROM (
                    SELECT id, role, content, metadata_json
                    FROM conversation_turns
                    WHERE conversation_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                )
                ORDER BY id ASC
                """,
                (conversation_id, limit),
            ).fetchall()
        return [
            {"role": row[0], "content": row[1], "metadata": json.loads(row[2])}
            for row in rows
        ]

    def has_conversation(self, conversation_id: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM conversation_turns WHERE conversation_id = ? LIMIT 1",
                (conversation_id,),
            ).fetchone()
        return row is not None

    def record_feedback(
        self,
        *,
        conversation_id: str,
        helpful: bool,
        comment: str | None,
        trace_id: str | None,
        history: list[dict[str, Any]],
    ) -> str:
        feedback_id = str(uuid4())
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO feedback (
                    feedback_id, conversation_id, helpful, comment, trace_id,
                    history_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    feedback_id,
                    conversation_id,
                    int(helpful),
                    comment,
                    trace_id,
                    _to_json(history),
                    datetime.now(UTC).isoformat(),
                ),
            )
        return feedback_id

    def update_latest_assistant_metadata(
        self,
        *,
        conversation_id: str,
        metadata: dict[str, Any],
        message_id: str | None = None,
    ) -> None:
        with self._connect() as connection:
            condition = " AND json_extract(metadata_json, '$.message_id') = ?" if message_id else ""
            row = connection.execute(
                "SELECT id, metadata_json FROM conversation_turns "
                "WHERE conversation_id = ? AND role = 'assistant'" + condition
                + " ORDER BY id DESC LIMIT 1",
                (conversation_id, message_id) if message_id else (conversation_id,),
            ).fetchone()
            if not row:
                raise KeyError(conversation_id)
            merged = {**json.loads(row[1]), **metadata}
            connection.execute(
                "UPDATE conversation_turns SET metadata_json = ? WHERE id = ?",
                (_to_json(merged), row[0]),
            )

    def record_answer_feedback(
        self, *, conversation_id: str, message_id: str | None,
        helpful: bool, comment: str | None, trace_id: str | None,
    ) -> str:
        """Resolve the server answer ID and save rating, snapshot and candidate atomically."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if not message_id:
                count = connection.execute(
                    "SELECT count(*) FROM conversation_turns WHERE conversation_id=? AND role='assistant'",
                    (conversation_id,),
                ).fetchone()[0]
                if count > 1:
                    raise ValueError("message_id is required when a conversation has multiple answers")
            condition = " AND json_extract(metadata_json, '$.message_id') = ?" if message_id else ""
            params = (conversation_id, message_id) if message_id else (conversation_id,)
            target = connection.execute(
                "SELECT id, content, metadata_json FROM conversation_turns "
                "WHERE conversation_id = ? AND role = 'assistant'" + condition
                + " ORDER BY id DESC LIMIT 1", params,
            ).fetchone()
            if target is None:
                raise KeyError(message_id or conversation_id)
            metadata = json.loads(target[2])
            # Retrying the same vote must not duplicate it or undo an approved example.
            previous = (metadata.get("user_feedback") or [])[-1:]
            if previous and previous[0].get("feedback_id") and (
                previous[0]["helpful"] == helpful and previous[0].get("comment") == comment
            ):
                return previous[0]["feedback_id"]
            feedback_id = str(uuid4())
            rating = {"helpful": helpful, "comment": comment, "trace_id": trace_id,
                      "feedback_id": feedback_id, "target_message_id": metadata.get("message_id")}
            metadata["user_feedback"] = [*(metadata.get("user_feedback") or []), rating]
            connection.execute("UPDATE conversation_turns SET metadata_json = ? WHERE id = ?",
                               (_to_json(metadata), target[0]))
            rows = connection.execute(
                "SELECT role, content, metadata_json FROM ("
                "SELECT id, role, content, metadata_json FROM conversation_turns "
                "WHERE conversation_id = ? AND id <= ? ORDER BY id DESC LIMIT 24) ORDER BY id",
                (conversation_id, target[0]),
            ).fetchall()
            history = [{"role": r[0], "content": r[1], "metadata": json.loads(r[2])} for r in rows]
            now = datetime.now(UTC).isoformat()
            connection.execute(
                "INSERT INTO feedback VALUES (?, ?, ?, ?, ?, ?, ?)",
                (feedback_id, conversation_id, int(helpful), comment, trace_id, _to_json(history), now),
            )
            answer_id = metadata.get("message_id")
            question = next((turn["content"] for turn in reversed(history[:-1])
                             if turn["role"] == "user"), "")
            if answer_id:
                self._audit_example(connection, answer_id, event="feedback_changed",
                                    caused_by_feedback_id=feedback_id)
                eligible = (helpful and metadata.get("status") == "succeeded"
                            and bool(question) and bool(target[1]))
                # A changed vote revokes prior approval. Failed answers cannot be promoted;
                # legacy answers without saved evidence need an explicit source review.
                connection.execute(
                    """INSERT INTO feedback_examples (
                        message_id, feedback_id, conversation_id, query_type, question, answer,
                        status, reviewed_by, review_note, reviewed_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?)
                    ON CONFLICT(message_id) DO UPDATE SET
                        feedback_id=excluded.feedback_id, question=excluded.question,
                        answer=excluded.answer, status=excluded.status, reviewed_by=NULL,
                        review_note=NULL, reviewed_at=NULL, updated_at=excluded.updated_at""",
                    (answer_id, feedback_id, conversation_id, metadata.get("query_type", ""),
                     question, target[1], "pending" if eligible else "excluded", now),
                )
            return feedback_id

    def resolve_answer(self, conversation_id: str, *, question: str, answer: str) -> str:
        """Match older browser sessions only when both original texts identify one exchange."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT a.metadata_json FROM conversation_turns a "
                "JOIN conversation_turns u ON u.id=(SELECT max(id) FROM conversation_turns "
                "WHERE conversation_id=a.conversation_id AND id<a.id) "
                "WHERE a.conversation_id=? AND a.role='assistant' AND a.content=? "
                "AND u.role='user' AND u.content=?",
                (conversation_id, answer, question),
            ).fetchall()
        if not rows:
            raise KeyError(conversation_id)
        if len(rows) != 1:
            raise ValueError("Ambiguous legacy answer; a server message_id is required")
        return json.loads(rows[0][0])["message_id"]

    def list_examples(self, *, status: str | None = None, limit: int | None = 100) -> list[dict[str, Any]]:
        sql = "SELECT * FROM feedback_examples"
        params: list[Any] = []
        if status:
            sql += " WHERE status = ?"
            params.append(status)
        sql += " ORDER BY updated_at DESC, message_id"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(max(1, min(limit, 1000)))
        with self._connect() as connection:
            connection.row_factory = sqlite3.Row
            return [dict(row) for row in connection.execute(sql, params).fetchall()]

    def review_example(
        self, message_id: str, *, approve: bool, reviewer: str, note: str,
        question: str | None = None, answer: str | None = None,
        source_review_note: str | None = None,
    ) -> None:
        if not reviewer.strip() or not note.strip():
            raise ValueError("Reviewer and review note are required")
        if approve and (not question or not question.strip() or len(question) > 1000
                        or not answer or not answer.strip() or len(answer) > 4000):
            raise ValueError("Provide a reviewed question (1–1000 chars) and answer (1–4000 chars)")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT status FROM feedback_examples WHERE message_id = ?",
                                     (message_id,)).fetchone()
            if row is None:
                raise KeyError(message_id)
            if approve and row[0] == "excluded":
                raise ValueError("Negative or failed feedback cannot be approved")
            if approve:
                source = connection.execute(
                    "SELECT COALESCE(r.history_json, f.history_json), f.helpful FROM feedback_examples e "
                    "JOIN feedback f USING(feedback_id) LEFT JOIN feedback_relinks r USING(feedback_id) "
                    "WHERE e.message_id=?", (message_id,),
                ).fetchone()
                history = json.loads(source[0])
                target_metadata = history[-1].get("metadata", {})
                # Review status is mutable: rejecting an excluded item must never
                # turn a negative/failed source into an approvable example.
                if not source[1] or target_metadata.get("status") != "succeeded":
                    raise ValueError("Negative or failed feedback cannot be approved")
                if not target_metadata.get("evidence") and not (
                    source_review_note and source_review_note.strip()
                ):
                    raise ValueError("Saved evidence is missing; provide an explicit source_review_note after verifying sources")
            now = datetime.now(UTC).isoformat()
            connection.execute(
                "UPDATE feedback_examples SET status=?, reviewed_by=?, review_note=?, "
                "reviewed_at=?, updated_at=?, question=COALESCE(?, question), "
                "answer=COALESCE(?, answer) WHERE message_id=?",
                ("approved" if approve else "rejected", reviewer.strip(),
                 note.strip() + ("\nSource review: " + source_review_note.strip() if approve and source_review_note else ""), now, now,
                 question.strip() if approve else None, answer.strip() if approve else None, message_id),
            )
            self._audit_example(connection, message_id, event="approved" if approve else "rejected")

    @staticmethod
    def _audit_example(connection: sqlite3.Connection, message_id: str, *, event: str,
                       caused_by_feedback_id: str | None = None) -> None:
        cursor = connection.execute("SELECT * FROM feedback_examples WHERE message_id=?", (message_id,))
        row = cursor.fetchone()
        if row is None:
            return
        snapshot = dict(zip((column[0] for column in cursor.description), row, strict=True))
        if event == "feedback_changed" and not snapshot.get("reviewed_at"):
            return  # Unreviewed originals already live in immutable feedback snapshots.
        connection.execute(
            "INSERT INTO feedback_example_audit (message_id, event, snapshot_json, "
            "caused_by_feedback_id, created_at) VALUES (?, ?, ?, ?, ?)",
            (message_id, event, _to_json(snapshot), caused_by_feedback_id, datetime.now(UTC).isoformat()),
        )

    def example_history(self, message_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, event, snapshot_json, caused_by_feedback_id, created_at "
                "FROM feedback_example_audit WHERE message_id=? ORDER BY id", (message_id,),
            ).fetchall()
        return [{"revision": row[0], "event": row[1], "example": json.loads(row[2]),
                 "caused_by_feedback_id": row[3], "created_at": row[4]} for row in rows]

    def approved_examples(self, *, query_type: str, exclude_conversation_id: str | None) -> list[dict[str, Any]]:
        with self._connect() as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                "SELECT message_id, question, answer FROM feedback_examples "
                "WHERE status='approved' AND query_type=? AND conversation_id != ? "
                "ORDER BY updated_at DESC, message_id LIMIT 500",
                (query_type, exclude_conversation_id or ""),
            ).fetchall()
        return [dict(row) for row in rows]

    def feedback_stats(self) -> dict[str, Any]:
        with self._connect() as connection:
            ratings = dict(connection.execute("SELECT helpful, count(*) FROM feedback GROUP BY helpful"))
            examples = dict(connection.execute("SELECT status, count(*) FROM feedback_examples GROUP BY status"))
            return {
                "conversation_messages": connection.execute("SELECT count(*) FROM conversation_turns").fetchone()[0],
                "feedback_events": {"helpful": ratings.get(1, 0), "unhelpful": ratings.get(0, 0),
                                    "total": sum(ratings.values())},
                "examples": {status: examples.get(status, 0) for status in
                             ("pending", "approved", "rejected", "excluded")},
                "repaired_legacy_links": connection.execute("SELECT count(*) FROM feedback_relinks").fetchone()[0],
            }

    def list_feedback(self, *, conversation_id: str | None = None) -> list[dict[str, Any]]:
        sql = (
            "SELECT f.feedback_id, f.conversation_id, f.helpful, f.comment, f.trace_id, "
            "COALESCE(r.history_json, f.history_json), f.created_at, r.message_id "
            "FROM feedback f LEFT JOIN feedback_relinks r USING(feedback_id)"
        )
        params: tuple[Any, ...] = ()
        if conversation_id:
            sql += " WHERE f.conversation_id = ?"
            params = (conversation_id,)
        sql += " ORDER BY f.created_at, f.feedback_id"
        with self._connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [
            {
                "feedback_id": row[0],
                "conversation_id": row[1],
                "helpful": bool(row[2]),
                "comment": row[3],
                "trace_id": row[4],
                "history": json.loads(row[5]),
                "created_at": row[6],
                "link_repaired": row[7] is not None,
            }
            for row in rows
        ]

    def clear(self) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM feedback_example_audit")
            connection.execute("DELETE FROM feedback_examples")
            connection.execute("DELETE FROM feedback_relinks")
            connection.execute("DELETE FROM feedback")
            connection.execute("DELETE FROM conversation_turns")

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS conversation_turns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL,
                    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_conversation_turns_conversation_id
                    ON conversation_turns (conversation_id, id);
                CREATE TABLE IF NOT EXISTS feedback (
                    feedback_id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    helpful INTEGER NOT NULL CHECK (helpful IN (0, 1)),
                    comment TEXT,
                    trace_id TEXT,
                    history_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_feedback_conversation_id
                    ON feedback (conversation_id, created_at);
                CREATE TABLE IF NOT EXISTS feedback_examples (
                    message_id TEXT PRIMARY KEY,
                    feedback_id TEXT NOT NULL REFERENCES feedback(feedback_id),
                    conversation_id TEXT NOT NULL,
                    query_type TEXT NOT NULL,
                    question TEXT NOT NULL,
                    answer TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('pending','approved','rejected','excluded')),
                    reviewed_by TEXT,
                    review_note TEXT,
                    reviewed_at TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_feedback_examples_status_type
                    ON feedback_examples (status, query_type, updated_at);
                CREATE TABLE IF NOT EXISTS feedback_example_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_id TEXT NOT NULL,
                    event TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    caused_by_feedback_id TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_feedback_example_audit_message
                    ON feedback_example_audit (message_id, id);
                CREATE TABLE IF NOT EXISTS feedback_relinks (
                    feedback_id TEXT PRIMARY KEY REFERENCES feedback(feedback_id),
                    message_id TEXT NOT NULL,
                    history_json TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )

            # Existing sessions predate message IDs; preserve their content and give
            # each stored assistant turn a stable server ID once.
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                "SELECT id, metadata_json FROM conversation_turns WHERE role='assistant' "
                "AND json_extract(metadata_json, '$.message_id') IS NULL"
            ).fetchall()
            for turn_id, raw in rows:
                metadata = {**json.loads(raw), "message_id": str(uuid4())}
                connection.execute("UPDATE conversation_turns SET metadata_json=? WHERE id=?",
                                   (_to_json(metadata), turn_id))

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=5.0)
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA foreign_keys=ON")
            with connection:
                yield connection
        finally:
            connection.close()


def _to_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))
