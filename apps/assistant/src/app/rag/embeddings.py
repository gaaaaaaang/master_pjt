from __future__ import annotations

from typing import Protocol

import httpx

from app.agents.usage import model_call, reported_usage
from app.config import get_settings


class EmbeddingClient(Protocol):
    def embed_texts(self, texts: list[str]) -> list[list[float]]: ...


class RequestEmbeddingCache:
    """Reuse the identical query across KB searches within one retrieval request only."""

    def __init__(self, client: EmbeddingClient):
        self.client = client
        self._vectors: dict[tuple[str, ...], list[list[float]]] = {}

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        key = tuple(texts)
        if key not in self._vectors:
            self._vectors[key] = self.client.embed_texts(texts)
        return [list(vector) for vector in self._vectors[key]]


class AzureEmbeddingClient:
    def __init__(self, *, timeout_seconds: float = 60.0) -> None:
        settings = get_settings()
        self.api_key = settings.openai_api_key
        self.model = settings.embedding_model
        self.endpoint = settings.openai_endpoint.rstrip("/")
        self.api_version = settings.openai_api_version
        self.timeout_seconds = timeout_seconds

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        with model_call("embeddings", self.model):
            return self._embed_texts(texts)

    def _embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY is not configured.")

        url = (
            f"{self.endpoint}/openai/deployments/{self.model}/embeddings"
            f"?api-version={self.api_version}"
        )
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(
                url,
                headers={"api-key": self.api_key, "Content-Type": "application/json"},
                json={"input": texts, "model": self.model},
            )
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                detail = response.text.strip()
                if len(detail) > 1000:
                    detail = f"{detail[:1000]}..."
                raise RuntimeError(
                    f"Embedding API returned HTTP {response.status_code}: "
                    f"{detail or 'empty response body'}"
                ) from exc

        body = response.json()
        reported_usage(body.get("usage"))
        return ordered_vectors(body, len(texts))


def ordered_vectors(body, expected_count: int) -> list[list[float]]:
    rows = body.get("data") if isinstance(body, dict) else None
    if not isinstance(rows, list) or len(rows) != expected_count:
        raise ValueError("Embedding response count does not match input.")
    if any(not isinstance(row, dict) or type(row.get("index")) is not int for row in rows):
        raise ValueError("Embedding response requires integer input indexes.")
    if sorted(row["index"] for row in rows) != list(range(expected_count)):
        raise ValueError("Embedding response indexes are missing or duplicated.")
    if any(not isinstance(row.get("embedding"), list) for row in rows):
        raise ValueError("Embedding response is missing a vector.")
    return [row["embedding"] for row in sorted(rows, key=lambda row: row["index"])]
