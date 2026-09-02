from __future__ import annotations

from typing import Protocol

import httpx

from app.config import get_settings


class EmbeddingClient(Protocol):
    def embed_texts(self, texts: list[str]) -> list[list[float]]: ...


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
        vectors = [item["embedding"] for item in sorted(body["data"], key=lambda item: item["index"])]
        return vectors
