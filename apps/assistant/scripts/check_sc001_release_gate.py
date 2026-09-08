"""Validate fab10 AutoSched completeness, snapshot freshness, and golden queries."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import psycopg
from app.config import get_settings
from app.db.fab_catalog import physical_table_name, table_ref
from app.db.read_only import ReadOnlyQueryExecutor
from psycopg import sql
from psycopg.rows import dict_row

APP_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT = APP_ROOT / "tests" / "fixtures" / "sc001_fab10_golden.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--json", action="store_true", dest="as_json")
    return parser.parse_args()


def compare_contract(
    contract: dict[str, Any],
    table_observations: dict[str, dict[str, Any]],
    query_observations: dict[str, dict[str, Any] | None],
) -> dict[str, Any]:
    failures = []
    table_results = []
    for table_name, expected in contract["tables"].items():
        actual = table_observations.get(table_name)
        passed = actual == expected
        table_results.append(
            {"table": table_name, "passed": passed, "expected": expected, "actual": actual}
        )
        if not passed:
            failures.append(f"{table_name}: completeness or snapshot time mismatch")

    query_results = []
    for query in contract["queries"]:
        actual = query_observations.get(query["id"])
        passed = actual == query["expected"]
        query_results.append(
            {
                "id": query["id"],
                "passed": passed,
                "expected": query["expected"],
                "actual": actual,
            }
        )
        if not passed:
            failures.append(f"{query['id']}: golden result mismatch")

    return {
        "passed": not failures,
        "fab_id": contract["fab_id"],
        "freshness_mode": contract["freshness_mode"],
        "source_snapshot_time": contract["source_snapshot_time"],
        "failures": failures,
        "tables": table_results,
        "queries": query_results,
    }


def inspect_database(contract: dict[str, Any], dsn: str) -> dict[str, Any]:
    table_observations: dict[str, dict[str, Any] | None] = {}
    query_observations = {}
    validator = ReadOnlyQueryExecutor(dsn="postgresql://validation-only")
    with psycopg.connect(
        dsn,
        options="-c default_transaction_read_only=on",
        row_factory=dict_row,
    ) as connection, connection.cursor() as cursor:
        for table_name in contract["tables"]:
            cursor.execute("SELECT to_regclass(%s)", (table_ref("fab10", table_name),))
            if cursor.fetchone()["to_regclass"] is None:
                table_observations[table_name] = None
                continue
            cursor.execute(
                sql.SQL(
                    "SELECT COUNT(*) AS row_count, "
                    "MAX(report_time)::text AS max_report_time FROM {}.{}"
                ).format(sql.Identifier("fab10"), sql.Identifier(physical_table_name("fab10", table_name)))
            )
            table_observations[table_name] = dict(cursor.fetchone())
        for query in contract["queries"]:
            if table_observations.get(query["table"]) is None:
                query_observations[query["id"]] = None
                continue
            validator.validate(query["sql"])
            cursor.execute(query["sql"])
            row = cursor.fetchone()
            query_observations[query["id"]] = dict(row) if row else None
    return compare_contract(contract, table_observations, query_observations)


def main() -> int:
    args = parse_args()
    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    dsn = get_settings().postgres_dsn
    if not dsn:
        raise RuntimeError("POSTGRES_DSN is not configured.")
    result = inspect_database(contract, dsn)
    if args.as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"SC-001 release gate: {'PASS' if result['passed'] else 'FAIL'}")
        for failure in result["failures"]:
            print(f"- {failure}")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
