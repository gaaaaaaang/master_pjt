from pathlib import Path

from scripts.load_autosched_postgres_reports import (
    APP_ROOT,
    column_type,
    copy_columns,
    parse_report_time,
    read_report,
    schema_ddl,
)


def test_parse_report_time_marker_to_timestamp() -> None:
    assert parse_report_time("~Report time: 01/01/2019 00:00:00 : ") == "2019-01-01 00:00:00"


def test_read_perf_report_adds_metadata_and_normalizes_columns() -> None:
    report_path = (
        APP_ROOT / "data" / "smt2020" / "AutoSched" / "dataset 1" / "HVLM_Model" / "perf.rep"
    )

    report = read_report(report_path, max_rows=2)

    assert report.table_name == "autosched_perf"
    assert report.columns[:4] == ["period", "relative", "lotstarts", "lotcomps"]
    assert copy_columns(report)[:3] == ["source_file", "report_time", "source_report_row"]
    assert report.rows[0]["source_file"] == "perf.rep"
    assert report.rows[0]["report_time"] == "2019-01-01 00:00:00"
    assert report.rows[0]["period"] == "WarmUp"
    assert report.rows[1]["report_time"] == "2020-01-01 00:00:00"


def test_schema_ddl_contains_autosched_metadata_and_numeric_status_columns() -> None:
    report_path = Path(
        APP_ROOT / "data" / "smt2020" / "AutoSched" / "dataset 1" / "HVLM_Model" / "stn.rep"
    )
    report = read_report(report_path, max_rows=1)

    ddl = schema_ddl("fab10", [report], recreate=True)

    assert 'DROP TABLE IF EXISTS "fab10"."autosched_stn"' in ddl
    assert 'CREATE TABLE IF NOT EXISTS "fab10"."autosched_stn"' in ddl
    assert '"source_file" TEXT NOT NULL' in ddl
    assert '"report_time" TIMESTAMP' in ddl
    assert '"source_report_row" INTEGER NOT NULL' in ddl
    assert '"util_percent" NUMERIC' in ddl
    assert '"wiplotavg" NUMERIC' in ddl
    assert column_type("curstate") == "TEXT"
    assert column_type("ontimeavg") == "TEXT"
