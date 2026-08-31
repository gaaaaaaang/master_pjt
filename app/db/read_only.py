from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.config import get_settings


class SqlValidationError(ValueError):
    """Raised when SQL violates the read-only query policy."""


@dataclass(frozen=True)
class ReadOnlyQueryResult:
    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int
    sql: str
    limit: int


FORBIDDEN_KEYWORDS = {
    "alter",
    "analyze",
    "call",
    "comment",
    "copy",
    "create",
    "delete",
    "drop",
    "execute",
    "grant",
    "insert",
    "merge",
    "refresh",
    "reindex",
    "replace",
    "revoke",
    "set",
    "truncate",
    "update",
    "vacuum",
}

TABLE_REF_PATTERN = re.compile(
    r"\b(?:from|join)\s+((?:\"[^\"]+\"|[a-zA-Z_][\w]*)\s*\.\s*(?:\"[^\"]+\"|[a-zA-Z_][\w]*))",
    re.IGNORECASE,
)
TOKEN_PATTERN = re.compile(r"[a-zA-Z_][\w]*")


def _strip_sql_comments(sql: str) -> str:
    without_line_comments = re.sub(r"--.*?(?=\n|$)", " ", sql)
    return re.sub(r"/\*.*?\*/", " ", without_line_comments, flags=re.DOTALL)


def _strip_string_literals(sql: str) -> str:
    return re.sub(r"'(?:''|[^'])*'", "''", sql)


def _normalize_identifier(identifier: str) -> str:
    return identifier.strip().strip('"').lower()


def _split_schema_table(table_ref: str) -> tuple[str, str]:
    left, right = table_ref.split(".", maxsplit=1)
    return _normalize_identifier(left), _normalize_identifier(right)


class ReadOnlyQueryExecutor:
    def __init__(
        self,
        dsn: str | None = None,
        allowed_schemas: set[str] | None = None,
        timeout_seconds: int | None = None,
        max_rows: int | None = None,
    ) -> None:
        settings = get_settings()
        self.dsn = dsn or settings.postgres_dsn
        self.allowed_schemas = allowed_schemas or {
            schema.strip().lower()
            for schema in settings.db_allowed_schemas.split(",")
            if schema.strip()
        }
        self.timeout_seconds = timeout_seconds or settings.db_query_timeout_seconds
        self.max_rows = max_rows or settings.db_max_rows

    def validate(self, sql: str) -> str:
        cleaned = _strip_sql_comments(sql).strip()
        if not cleaned:
            raise SqlValidationError("SQL is empty.")

        statement_source = _strip_string_literals(cleaned)
        if ";" in statement_source.rstrip(";"):
            raise SqlValidationError("Only one SQL statement is allowed.")

        cleaned = cleaned.rstrip(";").strip()
        first_token = TOKEN_PATTERN.search(cleaned)
        if not first_token or first_token.group(0).lower() not in {"select", "with"}:
            raise SqlValidationError("Only SELECT or WITH queries are allowed.")

        token_source = _strip_string_literals(cleaned)
        tokens = {token.lower() for token in TOKEN_PATTERN.findall(token_source)}
        blocked = sorted(tokens & FORBIDDEN_KEYWORDS)
        if blocked:
            raise SqlValidationError(f"Forbidden SQL keyword: {blocked[0]}.")

        table_refs = TABLE_REF_PATTERN.findall(cleaned)
        if not table_refs:
            raise SqlValidationError("At least one schema-qualified table reference is required.")

        for table_ref in table_refs:
            schema, _table = _split_schema_table(table_ref.replace(" ", ""))
            if schema not in self.allowed_schemas:
                raise SqlValidationError(f"Schema is not allowed: {schema}.")

        return cleaned

    def with_limit(self, sql: str, limit: int | None = None) -> tuple[str, int]:
        effective_limit = min(limit or self.max_rows, self.max_rows)
        if effective_limit <= 0:
            raise SqlValidationError("Limit must be positive.")
        validated = self.validate(sql)
        limited_sql = f"SELECT * FROM ({validated}) AS read_only_query LIMIT {effective_limit}"
        return limited_sql, effective_limit

    def execute(self, sql: str, limit: int | None = None) -> ReadOnlyQueryResult:
        if not self.dsn:
            raise RuntimeError("POSTGRES_DSN is not configured.")

        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError("psycopg is required for PostgreSQL access.") from exc

        query, effective_limit = self.with_limit(sql, limit)
        with psycopg.connect(self.dsn, row_factory=dict_row) as conn, conn.cursor() as cur:
            cur.execute("SET TRANSACTION READ ONLY")
            cur.execute(f"SET LOCAL statement_timeout = {int(self.timeout_seconds * 1000)}")
            cur.execute(query)
            rows = list(cur.fetchall())
            columns = [desc.name for desc in cur.description or []]

        return ReadOnlyQueryResult(
            columns=columns,
            rows=rows,
            row_count=len(rows),
            sql=query,
            limit=effective_limit,
        )
