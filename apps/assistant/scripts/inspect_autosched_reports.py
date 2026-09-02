from __future__ import annotations

import csv
import io
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[1]
AUTOSCHED_DIR = APP_ROOT / "data" / "smt2020" / "AutoSched"

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
    "cont.rep",
]


def read_report(path: Path) -> list[list[str]]:
    text = path.read_bytes().decode("utf-16")
    return [
        row
        for row in csv.reader(io.StringIO(text), delimiter="\t")
        if any(cell.strip() for cell in row)
    ]


def first_data_row(rows: list[list[str]]) -> list[str]:
    for row in rows[1:]:
        if row and not row[0].startswith("~Report time"):
            return row
    return []


def main() -> None:
    for dataset_dir, schema_name, model_dir in DATASETS:
        print(f"\n[{dataset_dir} -> {schema_name}]")
        for report_file in REPORT_FILES:
            path = AUTOSCHED_DIR / dataset_dir / model_dir / report_file
            rows = read_report(path)
            header = rows[0] if rows else []
            sample = first_data_row(rows)
            report_markers = sum(1 for row in rows if row and row[0].startswith("~Report time"))
            data_rows = max(len(rows) - 1 - report_markers, 0)
            print(
                f"  {report_file}: data_rows={data_rows}, "
                f"report_times={report_markers}, cols={len(header)}"
            )
            print(f"    header={header}")
            if sample:
                print(f"    sample={sample[: min(len(sample), 8)]}")


if __name__ == "__main__":
    main()
