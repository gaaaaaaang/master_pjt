from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

APP_SRC = Path(__file__).resolve().parents[1] / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from app.rag.milvus_store import ensure_collection


def main() -> None:
    parser = argparse.ArgumentParser(description="Ensure the FAB RAG Milvus collection exists.")
    parser.add_argument("--uri", default=None, help="Milvus URI. Defaults to VECTOR_DB_URL or Lite DB.")
    parser.add_argument("--collection", default=None, help="Collection name. Defaults to master_pjt.")
    parser.add_argument("--dimension", default=None, type=int, help="Embedding dimension. Defaults to 3072.")
    parser.add_argument("--recreate", action="store_true", help="Drop and recreate the collection.")
    args = parser.parse_args()

    result = ensure_collection(
        uri=args.uri,
        collection_name=args.collection,
        dimension=args.dimension,
        recreate=args.recreate,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
