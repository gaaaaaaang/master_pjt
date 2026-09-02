from pathlib import Path


def ingest_workbook(path: Path, target_table: str = "smt2020_raw") -> int:
    """TODO: SMT2020 Excel을 검증 후 MySQL staging table에 적재합니다."""
    _ = (path, target_table)
    return 0

