"""Discover all readable FAB business tables and maintain one shared meta table."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.config import get_settings
from app.db.fab_catalog import ALLOWED_FABS, table_pattern, table_ref
from app.db.semantic_metadata import enrich

CATALOG_DDL = """
CREATE SCHEMA IF NOT EXISTS agent_meta;
CREATE TABLE IF NOT EXISTS agent_meta.table_catalog (
    logical_table text PRIMARY KEY,
    table_pattern text NOT NULL UNIQUE,
    description text NOT NULL,
    data_source_type text NOT NULL,
    columns jsonb NOT NULL,
    fab_status jsonb NOT NULL,
    aliases jsonb NOT NULL DEFAULT '[]',
    relationships jsonb NOT NULL DEFAULT '[]',
    metrics jsonb NOT NULL DEFAULT '[]',
    semantics jsonb NOT NULL DEFAULT '{}',
    updated_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE agent_meta.table_catalog ADD COLUMN IF NOT EXISTS semantics jsonb NOT NULL DEFAULT '{}'
"""

DISCOVER_SQL = """
SELECT n.nspname AS fab, c.relname AS physical_table,
       obj_description(c.oid, 'pg_class') AS description,
       a.attname AS column_name, format_type(a.atttypid, a.atttypmod) AS data_type,
       NOT a.attnotnull AS nullable, col_description(c.oid, a.attnum) AS column_description
FROM pg_catalog.pg_class c
JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
JOIN pg_catalog.pg_attribute a ON a.attrelid = c.oid
WHERE n.nspname = ANY(%s) AND c.relkind IN ('r', 'p', 'v', 'm')
  AND a.attnum > 0 AND NOT a.attisdropped
  AND has_schema_privilege(n.oid, 'USAGE') AND has_table_privilege(c.oid, 'SELECT')
ORDER BY n.nspname, c.relname, a.attnum
"""


def source_type(logical: str) -> str:
    if logical.startswith("live_process_"):
        return "simulation_snapshot"
    if logical.startswith("autosched_"):
        return "operational_report"
    if logical.startswith("lotrelease"):
        return "release_plan"
    return "model_master"


def group_tables(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    tables: dict[str, dict[str, Any]] = {}
    for row in rows:
        fab, physical = row["fab"], row["physical_table"]
        if fab not in ALLOWED_FABS or not physical.endswith(f"_{fab}"):
            continue
        logical = physical.removesuffix(f"_{fab}")
        ref = f"{fab}.{physical}"
        entry = tables.setdefault(ref, {
            "fab": fab, "logical_table": logical, "table_pattern": table_pattern(logical),
            "description": row.get("description") or logical.replace("_", " "),
            "data_source_type": source_type(logical), "columns": [],
        })
        entry["columns"].append({
            "name": row["column_name"], "type": row["data_type"],
            "nullable": row["nullable"], "description": row.get("column_description") or "",
        })
    return tables


def shared_records(tables: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    families: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entry in tables.values():
        families[entry["logical_table"]].append(entry)
    records = []
    for logical, variants in sorted(families.items()):
        canonical = variants[0]
        status = {fab: {"is_available": False} for fab in sorted(ALLOWED_FABS)}
        for variant in variants:
            state: dict[str, Any] = {"is_available": True}
            if variant["columns"] != canonical["columns"]:
                state["columns_override"] = variant["columns"]
            status[variant["fab"]] = state
        records.append({**canonical, "fab_status": status, "logical_table": logical})
    return records


def sync_metadata(conn: psycopg.Connection) -> list[dict[str, Any]]:
    with conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SET LOCAL statement_timeout = '10s'")
        cur.execute("SELECT pg_advisory_xact_lock(73110908)")
        cur.execute(DISCOVER_SQL, (sorted(ALLOWED_FABS),))
        records = shared_records({ref: enrich(entry) for ref, entry in group_tables(cur.fetchall()).items()})
        cur.execute(CATALOG_DDL)
        # Removed tables remain documented, but no longer appear as available.
        cur.execute("UPDATE agent_meta.table_catalog SET fab_status = %s", (
            Jsonb({fab: {"is_available": False} for fab in sorted(ALLOWED_FABS)}),
        ))
        for record in records:
            cur.execute("""
                INSERT INTO agent_meta.table_catalog
                    (logical_table, table_pattern, description, data_source_type, columns, fab_status,
                     aliases, metrics, semantics, relationships)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (logical_table) DO UPDATE SET
                    table_pattern = EXCLUDED.table_pattern,
                    columns = EXCLUDED.columns, fab_status = EXCLUDED.fab_status,
                    description = CASE WHEN table_catalog.description = replace(table_catalog.logical_table, '_', ' ')
                        THEN EXCLUDED.description ELSE table_catalog.description END,
                    aliases = CASE WHEN table_catalog.aliases = '[]'::jsonb
                        THEN EXCLUDED.aliases ELSE table_catalog.aliases END,
                    metrics = CASE WHEN table_catalog.semantics->>'managed_by' = 'editor'
                        THEN table_catalog.metrics
                        WHEN table_catalog.metrics = '[]'::jsonb
                        OR table_catalog.semantics->>'managed_by' = 'code'
                        THEN EXCLUDED.metrics ELSE table_catalog.metrics END,
                    semantics = CASE WHEN table_catalog.semantics = '{}'::jsonb
                        OR table_catalog.semantics->>'managed_by' = 'code'
                        THEN EXCLUDED.semantics ELSE table_catalog.semantics END,
                    relationships = CASE WHEN table_catalog.semantics->>'managed_by' = 'editor'
                        THEN table_catalog.relationships
                        WHEN table_catalog.relationships = '[]'::jsonb
                        OR table_catalog.semantics->>'managed_by' = 'code'
                        THEN EXCLUDED.relationships ELSE table_catalog.relationships END,
                    updated_at = now()
            """, (record["logical_table"], record["table_pattern"], record["description"],
                  record["data_source_type"], Jsonb(record["columns"]), Jsonb(record["fab_status"]),
                  Jsonb(record["aliases"]), Jsonb(record["metrics"]), Jsonb(record["semantics"]),
                  Jsonb(record["relationships"])))
    return records


def load_fab_catalog(fab: str) -> dict[str, dict[str, Any]]:
    if fab not in ALLOWED_FABS:
        raise ValueError("Unsupported FAB")
    settings = get_settings()
    if not settings.postgres_dsn:
        raise RuntimeError("POSTGRES_DSN is not configured")
    with (psycopg.connect(settings.postgres_dsn, connect_timeout=3) as conn,
          conn.cursor(row_factory=dict_row) as cur):
        cur.execute("SET TRANSACTION READ ONLY")
        cur.execute("SET LOCAL statement_timeout = '5s'")
        cur.execute(DISCOVER_SQL, ([fab],))
        tables = group_tables(cur.fetchall())
        cur.execute("SELECT to_regclass('agent_meta.table_catalog') AS relation")
        if cur.fetchone()["relation"]:
            # to_jsonb keeps reads compatible while the additive migration rolls out.
            cur.execute("SELECT logical_table, description, aliases, relationships, metrics, "
                        "COALESCE(to_jsonb(t)->'semantics', '{}'::jsonb) AS semantics "
                        "FROM agent_meta.table_catalog t")
            definitions = {row["logical_table"]: row for row in cur.fetchall()}
            for entry in tables.values():
                definition = definitions.get(entry["logical_table"], {})
                for key in ("description", "aliases", "relationships", "metrics", "semantics"):
                    if key in definition:
                        entry[key] = definition[key]
        for entry in tables.values():
            if (entry["logical_table"] in {"live_process_snapshots", "live_process_events", "toolgroups"}
                    and any(column["name"] == "area" for column in entry["columns"])):
                cur.execute(sql.SQL("SELECT DISTINCT area AS value FROM {}.{} WHERE area IS NOT NULL ORDER BY area LIMIT 33").format(
                    sql.Identifier(fab), sql.Identifier(entry["logical_table"] + "_" + fab)))
                values = [row["value"] for row in cur.fetchall()]
                if len(values) <= 32:
                    entry["value_domains"] = {"area": values}
    # Physical schema discovery is authoritative for columns and availability; cached
    # definitions and old conversation errors can never remove a newly available table.
    enriched = {ref: enrich(entry) for ref, entry in tables.items()}
    for entry in enriched.values():
        bound = []
        source_columns = {column["name"] for column in entry["columns"]}
        for relation in entry.get("relationships", []):
            if not isinstance(relation, dict):
                continue
            logical = relation.get("target_logical_table")
            if not isinstance(logical, str) or not logical:
                continue
            try:
                target = table_ref(fab, logical)
            except ValueError:
                # A malformed editorial relationship must not hide readable tables.
                continue
            if target not in enriched:
                continue
            target_columns = {column["name"] for column in enriched[target]["columns"]}
            keys = relation.get("keys", [])
            if isinstance(keys, list) and keys and all(
                isinstance(key, dict) and isinstance(key.get("source"), str)
                and isinstance(key.get("target"), str)
                and key["source"] in source_columns and key["target"] in target_columns for key in keys
            ):
                bound.append({**relation, "target_table_ref": target})
        entry["relationships"] = bound
    return enriched
