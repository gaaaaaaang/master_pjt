"""Build a versioned Milvus index; publish the serving manifest only after successful insertion."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT / "src"))
from app.config import Settings, get_settings
from app.rag.ingest import load_chunks
from app.rag.manifest import make_manifest, write_manifest
from app.rag.milvus_store import insert_chunks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--store-path", type=Path, default=APP_ROOT / "output/rag/master_pjt_v2.jsonl"
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--collection", default="master_pjt_rag_v2")
    parser.add_argument("--uri", default="http://localhost:19530")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--embedding-revision", default="text-embedding-3-large.v1")
    args = parser.parse_args()
    if not args.live:
        parser.error("--live is required; this embeds documents and writes a new index.")
    settings = Settings(_env_file=args.env_file)
    for key in (
        "openai_api_key",
        "openai_endpoint",
        "openai_api_version",
        "embedding_model",
        "embedding_dimension",
    ):
        value = getattr(settings, key)
        if value is not None:
            os.environ[key.upper()] = str(value)
    get_settings.cache_clear()
    chunks = load_chunks(args.store_path)
    manifest = make_manifest(
        chunks,
        embedding_model=settings.embedding_model,
        embedding_revision=args.embedding_revision,
        dimension=settings.embedding_dimension,
    )
    result = insert_chunks(
        chunks,
        uri=args.uri,
        collection_name=args.collection,
        dimension=settings.embedding_dimension,
        index_version=manifest.index_version,
    )
    if result["inserted"] != len(chunks):
        raise RuntimeError("Incomplete index build; serving manifest was not published.")
    write_manifest(args.manifest, manifest)
    print(
        json.dumps(
            {
                "chunk_count": len(chunks),
                "index_version": manifest.index_version,
                "collection": args.collection,
                "manifest": str(args.manifest),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
