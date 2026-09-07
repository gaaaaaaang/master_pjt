from __future__ import annotations

import hashlib
import json
import math
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.rag.embeddings import AzureEmbeddingClient, EmbeddingClient


def ensure_collection(
    *,
    uri: str | None = None,
    collection_name: str | None = None,
    dimension: int | None = None,
    recreate: bool = False,
    client: Any | None = None,
) -> dict[str, Any]:
    """Ensure the Milvus collection used by RAG exists.

    The initial collection follows the Milvus quickstart path: use the default `id`
    primary key and `vector` field, and allow dynamic scalar fields for chunk metadata.
    """
    settings = get_settings()
    uri = uri or settings.vector_db_url or "./apps/assistant/output/rag/milvus_lite.db"
    collection_name = collection_name or settings.vector_db_collection
    dimension = settings.embedding_dimension if dimension is None else dimension
    if dimension <= 0:
        raise ValueError("Milvus collection dimension must be positive.")

    with _managed_client(uri, client) as active_client:
        existed = bool(active_client.has_collection(collection_name=collection_name))
        if existed and not recreate and hasattr(active_client, "describe_collection"):
            description = active_client.describe_collection(collection_name=collection_name)
            vector_field = next(
                (f for f in description.get("fields", []) if f.get("name") == "vector"), None
            )
            stored_dimension = (vector_field or {}).get("params", {}).get("dim")
            if stored_dimension is None or int(stored_dimension) != dimension:
                raise ValueError("Existing Milvus collection has an incompatible vector dimension.")
        if existed and recreate:
            active_client.drop_collection(collection_name=collection_name)
            existed = False
        if not existed:
            active_client.create_collection(
                collection_name=collection_name,
                schema=_build_schema(dimension),
                index_params=_build_index_params(),
            )

        return {
            "uri": uri,
            "collection_name": collection_name,
            "dimension": dimension,
            "created": not existed,
            "recreated": recreate,
        }


def insert_chunks(
    chunks: list[dict[str, Any]],
    *,
    uri: str | None = None,
    collection_name: str | None = None,
    dimension: int | None = None,
    batch_size: int = 16,
    client: Any | None = None,
    embedding_client: EmbeddingClient | None = None,
    mode: str = "upsert",
    index_version: str | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    uri = uri or settings.vector_db_url or "./apps/assistant/output/rag/milvus_lite.db"
    collection_name = collection_name or settings.vector_db_collection
    dimension = settings.embedding_dimension if dimension is None else dimension
    if batch_size <= 0:
        raise ValueError("batch_size must be positive.")

    if mode not in {"upsert", "insert"}:
        raise ValueError("mode must be 'upsert' or 'insert'.")
    if any(len(str(chunk.get("content", ""))) > 8192 for chunk in chunks):
        raise ValueError("Chunk exceeds the maximum stored content length; split before embedding.")
    with _managed_client(uri, client) as active_client:
        embedding_client = embedding_client or AzureEmbeddingClient()
        ensure_collection(
            uri=uri,
            collection_name=collection_name,
            dimension=dimension,
            client=active_client,
        )

        inserted = 0
        for start in range(0, len(chunks), batch_size):
            batch = chunks[start : start + batch_size]
            vectors = embedding_client.embed_texts([str(chunk["content"]) for chunk in batch])
            for vector in vectors:
                _validate_vector(vector, dimension)
            rows = [
                _chunk_to_row(chunk, vector) for chunk, vector in zip(batch, vectors, strict=True)
            ]
            if index_version:
                for row in rows:
                    row["index_version"] = index_version
                    row["id"] = _int_id(f"{index_version}:{row['chunk_id']}")
            if rows:
                result = _write_rows(active_client, collection_name, rows, mode=mode)
                count = next(
                    (
                        result[key]
                        for key in ("insert_count", "inserted_count", "upsert_count")
                        if key in result
                    ),
                    None,
                )
                if type(count) is not int or not 0 <= count <= len(rows):
                    raise RuntimeError("Milvus did not report a valid written row count.")
                inserted += count

        if inserted:
            active_client.flush(collection_name=collection_name)
        stats = active_client.get_collection_stats(collection_name=collection_name)
        return {
            "collection_name": collection_name,
            "inserted": inserted,
            "mode": mode,
            "row_count": int(stats.get("row_count") or 0),
            "uri": uri,
        }


def search_chunks(
    query: str,
    *,
    knowledge_base: str,
    top_k: int,
    uri: str | None = None,
    collection_name: str | None = None,
    client: Any | None = None,
    embedding_client: EmbeddingClient | None = None,
    index_version: str | None = None,
    dimension: int | None = None,
) -> list[dict[str, Any]]:
    if knowledge_base not in {"incident_playbook", "process_basics"}:
        raise ValueError("Unknown RAG knowledge_base.")
    if top_k <= 0:
        return []
    if top_k > 200:
        raise ValueError("Milvus candidate limit cannot exceed 200.")
    settings = get_settings()
    uri = uri or settings.vector_db_url or "./apps/assistant/output/rag/milvus_lite.db"
    collection_name = collection_name or settings.vector_db_collection
    with _managed_client(uri, client) as active_client:
        embedding_client = embedding_client or AzureEmbeddingClient()
        vectors = embedding_client.embed_texts([query])
        if len(vectors) != 1:
            raise ValueError("Embedding API must return exactly one query vector.")
        vector = vectors[0]
        _validate_vector(vector, dimension if dimension is not None else len(vector))
        filter_expression = f"knowledge_base == {json.dumps(knowledge_base)}"
        if index_version:
            filter_expression += f" and index_version == {json.dumps(index_version)}"
        results = _search_client(
            active_client,
            collection_name=collection_name,
            data=[vector],
            filter=filter_expression,
            limit=top_k,
            timeout=10.0,
            output_fields=[
                "chunk_id",
                "collection",
                "knowledge_base",
                "source",
                "source_document",
                "title",
                "content",
                "metadata_json",
            ],
        )
        return [_hit_to_chunk(hit) for hit in (results[0] if results else [])]


def _create_client(uri: str):
    try:
        from pymilvus import MilvusClient
    except ImportError as exc:
        raise RuntimeError("pymilvus is required to configure a Milvus collection.") from exc
    from pymilvus.exceptions import MilvusException

    try:
        return MilvusClient(uri=uri, timeout=10.0)
    except MilvusException as exc:
        raise RuntimeError("Milvus connection failed.") from exc


def _write_rows(
    client: Any, collection_name: str, rows: list[dict[str, Any]], *, mode: str
) -> dict:
    if mode == "upsert" and hasattr(client, "upsert"):
        return client.upsert(collection_name=collection_name, data=rows)
    if mode == "insert":
        return client.insert(collection_name=collection_name, data=rows)
    if mode != "upsert":
        raise ValueError("mode must be 'upsert' or 'insert'.")
    return client.insert(collection_name=collection_name, data=rows)


def _build_schema(dimension: int):
    from pymilvus import DataType, MilvusClient

    schema = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=True)
    schema.add_field("id", DataType.INT64, is_primary=True)
    schema.add_field("vector", DataType.FLOAT_VECTOR, dim=dimension)
    return schema


def _build_index_params():
    from pymilvus import MilvusClient

    index_params = MilvusClient.prepare_index_params()
    index_params.add_index(
        field_name="vector",
        index_type="AUTOINDEX",
        metric_type="COSINE",
    )
    return index_params


def _chunk_to_row(chunk: dict[str, Any], vector: list[float]) -> dict[str, Any]:
    metadata = dict(chunk.get("metadata") or {})
    return {
        "id": _int_id(str(chunk["chunk_id"])),
        "vector": vector,
        "chunk_id": str(chunk["chunk_id"]),
        "collection": str(chunk["collection"]),
        "knowledge_base": str(chunk["knowledge_base"]),
        "source": str(chunk["source"]),
        "source_document": Path(str(chunk["source"])).name,
        "title": str(chunk["title"]),
        "content": str(chunk["content"]),
        "metadata_json": json.dumps(metadata, ensure_ascii=False, sort_keys=True),
    }


def _hit_to_chunk(hit: dict[str, Any]) -> dict[str, Any]:
    entity = hit.get("entity") or hit
    metadata_json = entity.get("metadata_json") or "{}"
    try:
        metadata = json.loads(metadata_json)
    except json.JSONDecodeError:
        metadata = {}
    if not isinstance(metadata, dict):
        raise TypeError("Milvus chunk metadata must be a JSON object.")
    metadata["score"] = hit.get("distance", hit.get("score", 0.0))
    source = str(entity.get("source") or "")
    source_document = str(entity.get("source_document") or metadata.get("source_document") or "")
    if not source_document and source:
        source_document = Path(source).name
    metadata["source_document"] = source_document
    return {
        "chunk_id": str(entity.get("chunk_id") or hit.get("id") or entity.get("id") or ""),
        "collection": str(entity.get("collection") or get_settings().vector_db_collection),
        "knowledge_base": str(entity.get("knowledge_base") or ""),
        "source": source,
        "title": str(entity.get("title") or ""),
        "content": str(entity.get("content") or ""),
        "metadata": metadata,
    }


def _int_id(chunk_id: str) -> int:
    digest = hashlib.sha256(chunk_id.encode()).hexdigest()
    return int(digest[:15], 16)


def _validate_vector(vector: list[float], dimension: int) -> None:
    if dimension <= 0 or len(vector) != dimension:
        raise ValueError("Embedding vector dimension does not match the configured index.")
    if any(
        isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
        for value in vector
    ):
        raise ValueError("Embedding vector contains invalid numeric values.")


def _search_client(client: Any, **kwargs):
    from pymilvus.exceptions import MilvusException

    try:
        return client.search(**kwargs)
    except MilvusException as exc:
        raise RuntimeError("Milvus search failed.") from exc


@contextmanager
def _managed_client(uri: str, client: Any | None):
    owned = client is None
    active = _create_client(uri) if owned else client
    try:
        yield active
    finally:
        if owned:
            active.close()
