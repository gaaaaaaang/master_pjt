from functools import lru_cache

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
    db_allowed_schemas: str = "fab10,fab11,fab12,fab13"
    vector_db_url: str | None = None
    langsmith_tracing: bool = False
    langsmith_api_key: str | None = None
    langsmith_project: str = "fab-ai-assistant"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
