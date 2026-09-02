from __future__ import annotations

import hashlib
import json
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
    dimension = dimension or settings.embedding_dimension
    if dimension <= 0:
        raise ValueError("Milvus collection dimension must be positive.")

    client = client or _create_client(uri)
    existed = bool(client.has_collection(collection_name=collection_name))
    if existed and recreate:
        client.drop_collection(collection_name=collection_name)
        existed = False
    if not existed:
        client.create_collection(
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
) -> dict[str, Any]:
    settings = get_settings()
    uri = uri or settings.vector_db_url or "./apps/assistant/output/rag/milvus_lite.db"
    collection_name = collection_name or settings.vector_db_collection
    dimension = dimension or settings.embedding_dimension
    if batch_size <= 0:
        raise ValueError("batch_size must be positive.")

    client = client or _create_client(uri)
    embedding_client = embedding_client or AzureEmbeddingClient()
    ensure_collection(
        uri=uri,
        collection_name=collection_name,
        dimension=dimension,
        client=client,
    )

    inserted = 0
    for start in range(0, len(chunks), batch_size):
        batch = chunks[start : start + batch_size]
        vectors = embedding_client.embed_texts([str(chunk["content"]) for chunk in batch])
        rows = [_chunk_to_row(chunk, vector) for chunk, vector in zip(batch, vectors, strict=True)]
        if rows:
            result = _write_rows(client, collection_name, rows, mode=mode)
            inserted += int(
                result.get("insert_count")
                or result.get("inserted_count")
                or result.get("upsert_count")
                or len(rows)
            )
    if inserted:
        client.flush(collection_name=collection_name)
    stats = client.get_collection_stats(collection_name=collection_name)
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
) -> list[dict[str, Any]]:
    settings = get_settings()
    uri = uri or settings.vector_db_url or "./apps/assistant/output/rag/milvus_lite.db"
    collection_name = collection_name or settings.vector_db_collection
    client = client or _create_client(uri)
    embedding_client = embedding_client or AzureEmbeddingClient()
    vector = embedding_client.embed_texts([query])[0]
    results = client.search(
        collection_name=collection_name,
        data=[vector],
        filter=f'knowledge_base == "{knowledge_base}"',
        limit=top_k,
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
    return MilvusClient(uri=uri)


def _write_rows(client: Any, collection_name: str, rows: list[dict[str, Any]], *, mode: str) -> dict:
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
        "content": str(chunk["content"])[:8192],
        "metadata_json": json.dumps(metadata, ensure_ascii=False, sort_keys=True),
    }


def _hit_to_chunk(hit: dict[str, Any]) -> dict[str, Any]:
    entity = hit.get("entity") or hit
    metadata_json = entity.get("metadata_json") or "{}"
    try:
        metadata = json.loads(metadata_json)
    except json.JSONDecodeError:
        metadata = {}
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
