"""Content-addressed corpus/embedding identity and atomic local publication."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class IndexManifest:
    schema_version: int
    corpus_digest: str
    embedding_model: str
    embedding_revision: str
    dimension: int
    chunk_count: int
    index_version: str


def make_manifest(
    chunks: list[dict[str, Any]], *, embedding_model: str, embedding_revision: str, dimension: int
) -> IndexManifest:
    if not embedding_model or not embedding_revision or dimension <= 0:
        raise ValueError(
            "An explicit embedding model, revision and positive dimension are required."
        )
    ids = [c.get("chunk_id") for c in chunks]
    if any(not isinstance(cid, str) or not cid for cid in ids) or len(set(ids)) != len(ids):
        raise ValueError("Corpus chunk IDs must be nonempty and unique.")
    # Absolute source paths are intentionally excluded: same corpus on another host is identical.
    records = [
        {
            "chunk_id": c["chunk_id"],
            "knowledge_base": c["knowledge_base"],
            "content": c["content"],
            "title": c.get("title", ""),
            "document_version": (c.get("metadata") or {}).get("document_version", ""),
        }
        for c in sorted(chunks, key=lambda c: c["chunk_id"])
    ]
    digest = hashlib.sha256(
        json.dumps(records, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    identity = f"{digest}:{embedding_model}:{embedding_revision}:{dimension}"
    version = hashlib.sha256(identity.encode()).hexdigest()[:24]
    return IndexManifest(
        1, digest, embedding_model, embedding_revision, dimension, len(chunks), version
    )


def write_manifest(path: Path, manifest: IndexManifest) -> None:
    atomic_write(
        path, json.dumps(asdict(manifest), ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    )


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def load_manifest(path: Path) -> IndexManifest:
    value = json.loads(path.read_text())
    manifest = IndexManifest(**value)
    if manifest.schema_version != 1 or not manifest.index_version or manifest.dimension <= 0:
        raise ValueError("Unsupported or invalid RAG index manifest.")
    return manifest


def verify_manifest(
    manifest: IndexManifest,
    chunks: list[dict[str, Any]],
    *,
    embedding_model: str,
    embedding_revision: str,
    dimension: int,
) -> None:
    actual = make_manifest(
        chunks,
        embedding_model=embedding_model,
        embedding_revision=embedding_revision,
        dimension=dimension,
    )
    if manifest != actual:
        raise ValueError(
            "Corpus and serving embedding configuration do not match the index manifest."
        )
