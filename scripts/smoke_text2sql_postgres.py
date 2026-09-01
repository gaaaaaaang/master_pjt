from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DEFAULT_DSN = "postgresql://fab_user:fab_password@localhost:5432/fab"
LOGGER = logging.getLogger("text2sql_smoke")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one Text2SQL smoke test pass against local PostgreSQL data."
    )
    parser.add_argument("--fab", default="fab10", choices=["fab10", "fab11", "fab12", "fab13"])
    parser.add_argument(
        "--dsn",
        default=os.environ.get("POSTGRES_DSN", DEFAULT_DSN),
        help="PostgreSQL DSN. Defaults to POSTGRES_DSN or local docker-compose DSN.",
    )
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING"])
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print a final machine-readable JSON summary after the log output.",
    )
    parser.add_argument(
        "--case-file",
        type=Path,
        help="Optional JSON file containing Text2SQL eval cases.",
    )
    return parser.parse_args()


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s %(levelname)s [text2sql-smoke] %(message)s",
    )


def default_cases(fab: str) -> list[dict[str, Any]]:
    return [
        {
            "id": "status_without_autosched",
            "question": f"지금 {fab} WIP 몇 개야?",
            "expected_status": "data_unavailable",
            "expect_sql": False,
        },
        {
            "id": "toolgroup_area_lookup",
            "question": f"{fab} Dry_Etch toolgroup 목록 보여줘",
            "expected_status": "succeeded",
            "expected_query_type": "master_data_lookup",
            "expect_sql": True,
            "min_rows": 1,
        },
        {
            "id": "route_product_lookup",
            "question": f"{fab} Product_3 route step 보여줘",
            "expected_status": "succeeded",
            "expected_query_type": "master_data_lookup",
            "expect_sql": True,
            "min_rows": 1,
        },
        {
            "id": "release_plan_lookup",
            "question": f"{fab} Product_3 release plan 보여줘",
            "expected_status": "succeeded",
            "expected_query_type": "release_plan_lookup",
            "expect_sql": True,
        },
        {
            "id": "broad_release_plan_guard",
            "question": f"{fab} release plan 보여줘",
            "expected_status": "needs_clarification",
            "expect_sql": False,
        },
    ]


def main() -> int:
    args = parse_args()
    setup_logging(args.log_level)
    os.environ["POSTGRES_DSN"] = args.dsn

    from app.sub_agent.text2sql import answer_question

    LOGGER.info("branch_scope=Text2SQL initial smoke test")
    LOGGER.info("fab=%s", args.fab)
    LOGGER.info("postgres_dsn=%s", mask_dsn(args.dsn))

    failures: list[str] = []
    summaries: list[dict[str, Any]] = []
    cases = load_cases(args.case_file, args.fab)

    for idx, case in enumerate(cases, start=1):
        LOGGER.info("case %s/%s id=%s", idx, len(cases), case["id"])
        LOGGER.info("question=%s", case["question"])

        fab_context = case.get("fab_context", args.fab)
        result = answer_question(case["question"], fab=fab_context, execute=True)
        plan = asdict(result.plan) if result.plan else None

        LOGGER.info(
            "result status=%s query_type=%s confidence=%.2f rows=%s",
            result.status,
            result.query_type,
            result.confidence,
            result.row_count,
        )
        LOGGER.info("answer=%s", result.answer)
        if result.limitations:
            LOGGER.info("limitations=%s", " | ".join(result.limitations))
        if plan:
            LOGGER.info("plan=%s", json.dumps(plan, ensure_ascii=False, default=str))
        if result.sql:
            LOGGER.info("sql=\n%s", result.sql)
        if result.rows:
            LOGGER.info(
                "sample_row=%s",
                json.dumps(result.rows[0], ensure_ascii=False, default=str),
            )

        failures.extend(validate_case(case, result))
        summaries.append(
            {
                "id": case["id"],
                "question": case["question"],
                "status": result.status,
                "query_type": result.query_type,
                "row_count": result.row_count,
                "has_sql": bool(result.sql),
                "template_id": result.plan.template_id if result.plan else None,
                "answer": result.answer,
                "limitations": result.limitations,
            }
        )

    if failures:
        LOGGER.error("smoke test failed with %s failure(s)", len(failures))
        for failure in failures:
            LOGGER.error("failure=%s", failure)
    else:
        LOGGER.info("smoke test passed")

    if args.json:
        print(json.dumps({"ok": not failures, "failures": failures, "cases": summaries}, ensure_ascii=False))

    return 1 if failures else 0


def validate_case(case: dict[str, Any], result: Any) -> list[str]:
    failures: list[str] = []
    case_id = case["id"]

    if result.status != case["expected_status"]:
        failures.append(
            f"{case_id}: expected status={case['expected_status']}, got status={result.status}"
        )

    expected_query_type = case.get("expected_query_type")
    if expected_query_type and result.query_type != expected_query_type:
        failures.append(
            f"{case_id}: expected query_type={expected_query_type}, got query_type={result.query_type}"
        )

    expected_template_id = case.get("expected_template_id")
    actual_template_id = result.plan.template_id if result.plan else None
    if expected_template_id and actual_template_id != expected_template_id:
        failures.append(
            f"{case_id}: expected template_id={expected_template_id}, got {actual_template_id}"
        )

    if bool(result.sql) != case["expect_sql"]:
        failures.append(f"{case_id}: expected has_sql={case['expect_sql']}, got {bool(result.sql)}")

    for fragment in case.get("expected_sql_contains", []):
        if fragment not in (result.sql or ""):
            failures.append(f"{case_id}: SQL did not contain expected fragment={fragment!r}")

    for fragment in case.get("expected_answer_contains", []):
        if fragment not in result.answer:
            failures.append(f"{case_id}: answer did not contain expected fragment={fragment!r}")

    limitation_text = " ".join(result.limitations)
    for fragment in case.get("expected_limitation_contains", []):
        if fragment not in limitation_text:
            failures.append(f"{case_id}: limitations did not contain expected fragment={fragment!r}")

    min_rows = case.get("min_rows")
    if min_rows is not None and result.row_count < min_rows:
        failures.append(f"{case_id}: expected at least {min_rows} row(s), got {result.row_count}")

    return failures


def load_cases(case_file: Path | None, fab: str) -> list[dict[str, Any]]:
    if case_file is None:
        return default_cases(fab)
    with case_file.open(encoding="utf-8") as fh:
        cases = json.load(fh)
    if not isinstance(cases, list):
        raise TypeError(f"Case file must contain a JSON array: {case_file}")
    return cases


def mask_dsn(dsn: str) -> str:
    if "://" not in dsn or "@" not in dsn:
        return dsn
    scheme, rest = dsn.split("://", maxsplit=1)
    _credentials, host = rest.split("@", maxsplit=1)
    return f"{scheme}://***:***@{host}"


if __name__ == "__main__":
    sys.exit(main())
