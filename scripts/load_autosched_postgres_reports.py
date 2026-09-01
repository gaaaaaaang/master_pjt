from __future__ import annotations

import argparse
import csv
import io
import re
import subprocess
import tempfile
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUTOSCHED_DIR = ROOT / "SMT_2020 - Final" / "AutoSched"

DATASETS = [
    ("dataset 1", "fab10", "HVLM_Model"),
    ("dataset 2", "fab11", "LVHM_Model"),
    ("dataset 3", "fab12", "HVLM_E_Model"),
    ("dataset 4", "fab13", "LVHM_E_Model"),
]

REPORT_FILES = [
    "perf.rep",
    "stngrp.rep",
    "stn.rep",
    "part.rep",
    "lot.rep",
    "order.rep",
    "semi.rep",
    "stnfam.rep",
]

REPORT_TIME_PATTERN = re.compile(r"~Report time:\s*(.*?)\s*:\s*$")
TIMESTAMP_COLUMNS = {"startdate", "compdate", "duedate"}
TEXT_COLUMNS = {
    "period",
    "relative",
    "stngrp",
    "stn",
    "stnfam",
    "curstate",
    "part",
    "lot",
    "curstn",
    "curstep",
    "order",
    "partgrp",
    "partfam",
}
DURATION_COLUMNS = {
    "cyclecur",
    "cycleavg",
    "cyclestd",
    "netontime",
    "cyclemax",
    "xtheormax",
    "lateavg",
    "latestd",
    "latemax",
    "ontimeavg",
    "ontimestd",
}


@dataclass(frozen=True)
class ReportRows:
    report_file: str
    table_name: str
    columns: list[str]
    rows: list[dict[str, str | None]]


def sql_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def ident(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def snake(value: object, fallback: str) -> str:
    raw = str(value).strip() if value is not None else fallback
    raw = raw.replace("%", " percent ")
    raw = re.sub(r"[^0-9A-Za-z]+", "_", raw)
    raw = re.sub(r"_+", "_", raw).strip("_").lower()
    if not raw:
        raw = fallback
    if raw[0].isdigit():
        raw = f"col_{raw}"
    return raw


def dedupe(names: Iterable[str]) -> list[str]:
    counts: Counter[str] = Counter()
    result: list[str] = []
    for name in names:
        counts[name] += 1
        result.append(name if counts[name] == 1 else f"{name}_{counts[name]}")
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Load SMT2020 AutoSched .rep reports into fab10-fab13 PostgreSQL schemas."
    )
    parser.add_argument("--compose-file", default=str(ROOT / "docker-compose.yml"))
    parser.add_argument("--service", default="postgres")
    parser.add_argument("--database", default="fab")
    parser.add_argument("--user", default="fab_user")
    parser.add_argument("--autosched-dir", default=str(AUTOSCHED_DIR))
    parser.add_argument("--skip-ddl", action="store_true")
    parser.add_argument(
        "--recreate-ddl",
        action="store_true",
        help="Drop and recreate autosched_* tables before loading. Existing report rows are removed.",
    )
    parser.add_argument("--no-truncate", action="store_true")
    parser.add_argument(
        "--schema",
        action="append",
        choices=["fab10", "fab11", "fab12", "fab13"],
        help="Load only one schema. Can be passed multiple times.",
    )
    parser.add_argument(
        "--max-rows-per-table",
        type=int,
        help="Optional smoke-test limit for each report table.",
    )
    return parser.parse_args()


def psql_base(args: argparse.Namespace) -> list[str]:
    return [
        "docker",
        "compose",
        "-f",
        args.compose_file,
        "exec",
        "-T",
        args.service,
        "psql",
        "-U",
        args.user,
        "-d",
        args.database,
        "-v",
        "ON_ERROR_STOP=1",
    ]


def run_sql(args: argparse.Namespace, sql: str) -> None:
    subprocess.run([*psql_base(args), "-c", sql], check=True)


def report_table_name(report_file: str) -> str:
    return f"autosched_{Path(report_file).stem.lower()}"


def read_report(path: Path, *, max_rows: int | None = None) -> ReportRows:
    raw_rows = [
        row
        for row in csv.reader(io.StringIO(path.read_bytes().decode("utf-16")), delimiter="\t")
        if any(cell.strip() for cell in row)
    ]
    if not raw_rows:
        return ReportRows(path.name, report_table_name(path.name), [], [])

    columns = dedupe(snake(header, f"col_{idx:03d}") for idx, header in enumerate(raw_rows[0], 1))
    current_report_time: str | None = None
    rows: list[dict[str, str | None]] = []
    for source_row_id, raw_row in enumerate(raw_rows[1:], start=2):
        first_cell = raw_row[0].strip() if raw_row else ""
        report_time = parse_report_time(first_cell)
        if report_time:
            current_report_time = report_time
            continue

        values = normalize_row(raw_row, columns)
        rows.append(
            {
                "source_file": path.name,
                "report_time": current_report_time,
                "source_report_row": str(source_row_id),
                **dict(zip(columns, values, strict=True)),
            }
        )
        if max_rows is not None and len(rows) >= max_rows:
            break

    return ReportRows(path.name, report_table_name(path.name), columns, rows)


def parse_report_time(value: str) -> str | None:
    match = REPORT_TIME_PATTERN.match(value.strip())
    if not match:
        return None
    parsed = datetime.strptime(match.group(1), "%m/%d/%Y %H:%M:%S")  # noqa: DTZ007
    return parsed.isoformat(sep=" ")


def normalize_row(raw_row: list[str], columns: list[str]) -> list[str | None]:
    padded = [*raw_row[: len(columns)], *([""] * max(len(columns) - len(raw_row), 0))]
    return [normalize_value(column, value) for column, value in zip(columns, padded, strict=True)]


def normalize_value(column: str, value: str) -> str | None:
    stripped = value.strip()
    if not stripped:
        return None
    if column in TIMESTAMP_COLUMNS:
        return datetime.strptime(stripped, "%m/%d/%Y %H:%M:%S").isoformat(sep=" ")  # noqa: DTZ007
    return stripped


def column_type(column: str) -> str:
    if column in TIMESTAMP_COLUMNS:
        return "TIMESTAMP"
    if column in TEXT_COLUMNS or column in DURATION_COLUMNS:
        return "TEXT"
    return "NUMERIC"


def table_ddl(schema_name: str, report: ReportRows, *, recreate: bool = False) -> str:
    columns = [
        "    source_row_id BIGSERIAL PRIMARY KEY",
        f"    {ident('source_file')} TEXT NOT NULL",
        f"    {ident('report_time')} TIMESTAMP",
        f"    {ident('source_report_row')} INTEGER NOT NULL",
    ]
    columns.extend(f"    {ident(column)} {column_type(column)}" for column in report.columns)
    lines = []
    if recreate:
        lines.append(f"DROP TABLE IF EXISTS {ident(schema_name)}.{ident(report.table_name)};")
    lines.extend(
        [
            f"CREATE TABLE IF NOT EXISTS {ident(schema_name)}.{ident(report.table_name)} (",
            ",\n".join(columns),
            ");",
            (
                f"COMMENT ON TABLE {ident(schema_name)}.{ident(report.table_name)} IS "
                f"{sql_quote(f'SMT2020 AutoSched report: {report.report_file}')};"
            ),
        ]
    )
    return "\n".join(lines)


def schema_ddl(schema_name: str, reports: Iterable[ReportRows], *, recreate: bool = False) -> str:
    lines = [f"CREATE SCHEMA IF NOT EXISTS {ident(schema_name)};", ""]
    for report in reports:
        lines.append(table_ddl(schema_name, report, recreate=recreate))
        lines.append("")
    return "\n".join(lines).rstrip()


def export_report_csv(report: ReportRows, csv_path: Path) -> int:
    columns = copy_columns(report)
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        for row in report.rows:
            writer.writerow(row)
    return len(report.rows)


def copy_columns(report: ReportRows) -> list[str]:
    return ["source_file", "report_time", "source_report_row", *report.columns]


def copy_csv(args: argparse.Namespace, schema_name: str, report: ReportRows, csv_path: Path) -> None:
    column_sql = ", ".join(ident(column) for column in copy_columns(report))
    copy_sql = (
        f"\\copy {ident(schema_name)}.{ident(report.table_name)} ({column_sql}) "
        "FROM STDIN WITH (FORMAT csv, NULL '')"
    )
    subprocess.run([*psql_base(args), "-c", copy_sql], input=csv_path.read_bytes(), check=True)


def load_dataset_reports(
    args: argparse.Namespace,
    autosched_dir: Path,
    dataset_dir: str,
    schema_name: str,
    model_dir: str,
) -> None:
    report_dir = autosched_dir / dataset_dir / model_dir
    reports = [
        read_report(report_dir / report_file, max_rows=args.max_rows_per_table)
        for report_file in REPORT_FILES
    ]
    print(f"\n[{dataset_dir} -> {schema_name}] {report_dir.relative_to(ROOT)}")

    if not args.skip_ddl:
        run_sql(args, schema_ddl(schema_name, reports, recreate=args.recreate_ddl))

    with tempfile.TemporaryDirectory(prefix="autosched_rep_") as tmpdir:
        tmp = Path(tmpdir)
        for report in reports:
            if not args.no_truncate:
                run_sql(
                    args,
                    f"TRUNCATE TABLE {ident(schema_name)}.{ident(report.table_name)} RESTART IDENTITY;",
                )
            csv_path = tmp / f"{schema_name}__{report.table_name}.csv"
            row_count = export_report_csv(report, csv_path)
            if row_count:
                copy_csv(args, schema_name, report, csv_path)
            print(f"  {schema_name}.{report.table_name}: {row_count} rows")


def main() -> None:
    args = parse_args()
    autosched_dir = Path(args.autosched_dir)
    schemas = set(args.schema or ["fab10", "fab11", "fab12", "fab13"])
    for dataset in DATASETS:
        if dataset[1] in schemas:
            load_dataset_reports(args, autosched_dir, *dataset)


if __name__ == "__main__":
    main()
