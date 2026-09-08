"""Refresh the shared FAB metadata table without changing business data."""
import json
import sys
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from app.config import get_settings
from app.db.metadata_catalog import sync_metadata


def main():
    settings = get_settings()
    if not settings.postgres_dsn:
        raise SystemExit("POSTGRES_DSN is required")
    with psycopg.connect(settings.postgres_dsn, connect_timeout=5) as conn:
        records = sync_metadata(conn)
    print(json.dumps({"logical_tables": len(records), "physical_tables": sum(
        state["is_available"] for record in records for state in record["fab_status"].values()
    )}))


if __name__ == "__main__":
    main()
