"""Rename FAB tables in place; default to a dry run. No data is copied or deleted."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import psycopg
from psycopg import sql

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from app.config import get_settings
from app.db.fab_catalog import ALLOWED_FABS, physical_table_name


def build_rename_plan(tables: list[tuple[str, str, int]], *, reverse: bool = False) -> list[dict]:
    existing = {(schema, table) for schema, table, _ in tables}
    plan = []
    for schema, table, oid in sorted(tables):
        if schema not in ALLOWED_FABS:
            raise ValueError(f"Unexpected schema: {schema}")
        suffix = f"_{schema}"
        if reverse:
            if not table.endswith(suffix):
                continue
            target = table.removesuffix(suffix)
        else:
            if table.endswith(suffix):
                continue
            if re.search(r"_fab\d+$", table):
                raise ValueError(f"Mismatched FAB suffix: {schema}.{table}")
            target = physical_table_name(schema, table)
        if (schema, target) in existing:
            raise ValueError(f"Rename target already exists: {schema}.{target}")
        plan.append({"schema": schema, "from": table, "to": target, "oid": oid})
    return plan


def migrate(conn: psycopg.Connection, *, apply: bool = False, reverse: bool = False) -> list[dict]:
    # One transaction: a collision, lock timeout, or failed verification rolls back every rename.
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("SET LOCAL lock_timeout = '5s'")
        cur.execute("SET LOCAL statement_timeout = '60s'")
        cur.execute("SELECT pg_advisory_xact_lock(73110907)")
        cur.execute(
            "SELECT n.nspname, c.relname, c.oid FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = ANY(%s) AND c.relkind IN ('r', 'p')",
            (sorted(ALLOWED_FABS),),
        )
        plan = build_rename_plan(cur.fetchall(), reverse=reverse)
        if not apply:
            return plan
        for item in plan:
            cur.execute(sql.SQL("ALTER TABLE {}.{} RENAME TO {}").format(
                sql.Identifier(item["schema"]), sql.Identifier(item["from"]),
                sql.Identifier(item["to"]),
            ))
            cur.execute("SELECT to_regclass(%s)::oid", (f"{item['schema']}.{item['to']}",))
            if cur.fetchone()[0] != item["oid"]:
                raise RuntimeError("Table identity changed during rename")
    return plan


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Commit the rename transaction")
    parser.add_argument("--reverse", action="store_true", help="Remove suffixes for rollback")
    args = parser.parse_args()
    dsn = get_settings().postgres_dsn
    if not dsn:
        raise SystemExit("POSTGRES_DSN is required")
    with psycopg.connect(dsn, connect_timeout=5) as conn:
        plan = migrate(conn, apply=args.apply, reverse=args.reverse)
    print(json.dumps({"applied": args.apply, "reverse": args.reverse,
                      "table_count": len(plan), "tables": plan}, indent=2))


if __name__ == "__main__":
    main()
