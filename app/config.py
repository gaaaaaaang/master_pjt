from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "local"
    mock_mode: bool = True
    openai_api_key: str | None = None
    openai_model: str = "gpt-4.1-mini"
    mysql_dsn: str | None = None
    vector_db_url: str | None = None
    langsmith_tracing: bool = False
    langsmith_api_key: str | None = None
    langsmith_project: str = "fab-ai-assistant"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()

