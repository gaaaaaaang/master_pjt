import argparse
import json
import os
import sys
from pathlib import Path
from time import perf_counter

import httpx

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT / "src"))
from app.config import Settings, get_settings
from app.rag.embeddings import AzureEmbeddingClient
from app.rag.rerank import AzureReranker

parser = argparse.ArgumentParser(
    description="Actual API checks with fixed synthetic strings; no corpus is loaded."
)
parser.add_argument("--live", action="store_true")
parser.add_argument("--env-file", type=Path, default=Path(".env"))
parser.add_argument(
    "--output", type=Path, default=APP_ROOT / "output/evals/rag_api_connectivity.json"
)
args = parser.parse_args()
if not args.live:
    parser.error("--live is required to call the configured API.")
settings = Settings(_env_file=args.env_file)
for key in (
    "openai_api_key",
    "openai_model",
    "openai_endpoint",
    "openai_api_version",
    "embedding_model",
):
    value = getattr(settings, key)
    if value is not None:
        os.environ[key.upper()] = str(value)
get_settings.cache_clear()
# Only these newly authored, non-sensitive test strings are transmitted. No corpus is read.
report = {"payload_kind": "synthetic_connectivity_only", "checks": []}
start = perf_counter()
try:
    vectors = AzureEmbeddingClient(timeout_seconds=20).embed_texts(["This is a connectivity test."])
    report["checks"].append(
        {
            "check": "embedding",
            "succeeded": len(vectors) == 1 and len(vectors[0]) == settings.embedding_dimension,
            "dimension": len(vectors[0]) if vectors else 0,
            "seconds": round(perf_counter() - start, 2),
        }
    )
except (RuntimeError, OSError, ValueError, httpx.HTTPError) as exc:
    report["checks"].append(
        {"check": "embedding", "succeeded": False, "error_type": type(exc).__name__}
    )
start = perf_counter()
try:
    grades = AzureReranker().rank(
        "What color is the test square?",
        [
            {
                "chunk_id": "test-blue",
                "content": "The test square is blue.",
                "title": "Synthetic test",
            },
            {
                "chunk_id": "test-other",
                "content": "This sentence describes a triangle.",
                "title": "Synthetic unrelated text",
            },
        ],
    )
    report["checks"].append(
        {
            "check": "reranker",
            "succeeded": True,
            "grades": [{"id": g.chunk_id, "grade": g.grade} for g in grades],
            "seconds": round(perf_counter() - start, 2),
        }
    )
except (RuntimeError, OSError, ValueError, httpx.HTTPError) as exc:
    report["checks"].append(
        {"check": "reranker", "succeeded": False, "error_type": type(exc).__name__}
    )
path = args.output
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
print(json.dumps(report, ensure_ascii=False))
