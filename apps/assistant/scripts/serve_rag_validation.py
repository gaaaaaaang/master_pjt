"""Start this worktree's real app on loopback using the approved RAG API and test index."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT / "src"))
from app.config import Settings, get_settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8007)
    args = parser.parse_args()
    settings = Settings(_env_file=args.env_file)
    if settings.openai_endpoint.rstrip("/") != "https://skax.ai-talentlab.com":
        parser.error("Only the explicitly approved skax.ai-talentlab.com endpoint is allowed.")
    for key in (
        "openai_api_key",
        "openai_model",
        "openai_endpoint",
        "openai_api_version",
        "embedding_model",
        "embedding_dimension",
        "embedding_revision",
    ):
        value = getattr(settings, key)
        if value is not None:
            os.environ[key.upper()] = str(value)
    os.environ.update(
        VECTOR_DB_URL="http://127.0.0.1:19530",
        VECTOR_DB_COLLECTION="master_pjt_rag_api_eval",
        RAG_INDEX_MANIFEST_PATH=str(APP_ROOT / "output/rag/api_eval_manifest.json"),
        RAG_LOCAL_STORE_PATH=str(APP_ROOT / "output/rag/master_pjt_v2.jsonl"),
        RAG_RERANKER="llm",
        POSTGRES_DSN="",
        MYSQL_DSN="",
    )
    get_settings.cache_clear()
    import uvicorn
    from app.main import app

    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
