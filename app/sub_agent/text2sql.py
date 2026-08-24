"""Text-to-SQL sub-agent placeholder.

The production implementation should translate constrained FAB questions into
read-only SQL against the MySQL schema after schema validation is finalized.
"""

from typing import Any


def generate_sql(question: str, schema_context: str | None = None) -> str:
    """Generate a read-only SQL statement for a FAB operational question."""
    raise NotImplementedError("Text-to-SQL generation is not implemented yet.")


def execute_read_only(sql: str) -> list[dict[str, Any]]:
    """Execute a validated read-only SQL statement."""
    raise NotImplementedError("Read-only SQL execution is not implemented yet.")

