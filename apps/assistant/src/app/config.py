from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "local"
    mock_mode: bool = True
    openai_api_key: str | None = None
    openai_model: str = "gpt-4.1"
    openai_endpoint: str = "https://skax.ai-talentlab.com"
    openai_api_version: str = "2024-12-01-preview"
    mysql_dsn: str | None = None
    postgres_dsn: str | None = None
    db_query_timeout_seconds: int = 5
    db_max_rows: int = 200
    stream_timeout_seconds: int = 120
    db_allowed_schemas: str = "fab10,fab11,fab12,fab13"
    vector_db_url: str | None = None
    vector_db_collection: str = "master_pjt"
    rag_local_store_path: str = str(
        Path(__file__).resolve().parents[2] / "output/rag/master_pjt_v2.jsonl"
    )
    rag_candidate_k: int = Field(default=30, ge=1, le=200)
    rag_context_chars: int = Field(default=14000, ge=1000, le=100000)
    rag_reranker: Literal["feature", "llm"] = "feature"
    rag_rerank_limit: int = Field(default=12, ge=1, le=40)
    embedding_model: str = "text-embedding-3-large"
    embedding_dimension: int = Field(default=3072, ge=1)
    embedding_revision: str = "text-embedding-3-large.v1"
    rag_index_manifest_path: str | None = None
    langsmith_tracing: bool = False
    langsmith_api_key: str | None = None
    langsmith_project: str = "fab-ai-assistant"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
