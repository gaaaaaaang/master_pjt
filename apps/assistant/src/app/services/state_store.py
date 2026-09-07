from __future__ import annotations

import json
import sqlite3
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
    ) -> None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id, metadata_json
                FROM conversation_turns
                WHERE conversation_id = ? AND role = 'assistant'
                ORDER BY id DESC
                LIMIT 1
                """,
                (conversation_id,),
            ).fetchone()
            if not row:
                raise KeyError(conversation_id)
            merged = {**json.loads(row[1]), **metadata}
            connection.execute(
                "UPDATE conversation_turns SET metadata_json = ? WHERE id = ?",
                (_to_json(merged), row[0]),
            )

    def list_feedback(self, *, conversation_id: str | None = None) -> list[dict[str, Any]]:
        sql = (
            "SELECT feedback_id, conversation_id, helpful, comment, trace_id, "
            "history_json, created_at FROM feedback"
        )
        params: tuple[Any, ...] = ()
        if conversation_id:
            sql += " WHERE conversation_id = ?"
            params = (conversation_id,)
        sql += " ORDER BY created_at, feedback_id"
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
            }
            for row in rows
        ]

    def clear(self) -> None:
        with self._connect() as connection:
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
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection


def _to_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))
