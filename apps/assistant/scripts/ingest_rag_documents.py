from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
APP_SRC = APP_ROOT / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from app.config import get_settings
from app.rag.ingest import build_chunks_for_paths, write_chunks
from app.rag.milvus_store import insert_chunks
from app.sub_agent.rag import INCIDENT_PLAYBOOK, PROCESS_BASICS

DEFAULT_PLAYBOOK_PATHS = [
    APP_ROOT / "output" / "pdf" / "semiconductor_fab_incident_response_playbook_kr.pdf",
]
DEFAULT_BASICS_PATHS = [
    APP_ROOT / "data" / "smt2020" / "AutoSched" / "SMT_2020_AutoSched_AP_documentation.pdf",
    APP_ROOT / "data" / "smt2020" / "General Data" / "SMT_2020_generic_format.pdf",
]


def main() -> None:
    settings = get_settings()
    parser = argparse.ArgumentParser(
        description="Chunk, embed, and insert FAB RAG documents into Milvus."
    )
    parser.add_argument("--uri", default=settings.vector_db_url or "http://localhost:19530")
    parser.add_argument("--collection", default=settings.vector_db_collection)
    parser.add_argument("--store-path", default=settings.rag_local_store_path)
    parser.add_argument("--playbook", action="append", default=[], help="Incident playbook file/dir.")
    parser.add_argument("--basics", action="append", default=[], help="Process basics file/dir.")
    parser.add_argument("--chunk-target-chars", type=int, default=2400)
    parser.add_argument("--chunk-overlap-chars", type=int, default=300)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--mode", choices=["upsert", "insert"], default="upsert")
    parser.add_argument("--skip-insert", action="store_true", help="Only write JSONL chunks.")
    args = parser.parse_args()

    playbook_paths = _paths(args.playbook, DEFAULT_PLAYBOOK_PATHS)
    basics_paths = _paths(args.basics, DEFAULT_BASICS_PATHS)
    chunks = [
        *build_chunks_for_paths(
            playbook_paths,
            collection=args.collection,
            knowledge_base=INCIDENT_PLAYBOOK,
            chunk_target_chars=args.chunk_target_chars,
            chunk_overlap_chars=args.chunk_overlap_chars,
        ),
        *build_chunks_for_paths(
            basics_paths,
            collection=args.collection,
            knowledge_base=PROCESS_BASICS,
            chunk_target_chars=args.chunk_target_chars,
            chunk_overlap_chars=args.chunk_overlap_chars,
        ),
    ]

    store_path = Path(args.store_path)
    write_chunks(store_path, chunks)
    summary = {
        "collection_name": args.collection,
        "store_path": str(store_path),
        "chunk_count": len(chunks),
        "knowledge_bases": {
            INCIDENT_PLAYBOOK: sum(chunk.knowledge_base == INCIDENT_PLAYBOOK for chunk in chunks),
            PROCESS_BASICS: sum(chunk.knowledge_base == PROCESS_BASICS for chunk in chunks),
        },
        "inserted": 0,
    }
    if not args.skip_insert:
        insert_result = insert_chunks(
            [asdict(chunk) for chunk in chunks],
            uri=args.uri,
            collection_name=args.collection,
            batch_size=args.batch_size,
            mode=args.mode,
        )
        summary["inserted"] = insert_result["inserted"]
        summary["mode"] = insert_result["mode"]
        summary["row_count"] = insert_result["row_count"]
        summary["uri"] = insert_result["uri"]

    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


def _paths(values: list[str], defaults: list[Path]) -> list[Path]:
    if not values:
        return defaults
    return [Path(value).expanduser().resolve() for value in values]


if __name__ == "__main__":
    main()
