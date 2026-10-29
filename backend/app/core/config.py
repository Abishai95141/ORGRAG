from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")

    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/strata"

    secret_key: str = "dev-only-change-me-strata-secret-key-0123456789"
    session_cookie: str = "strata_session"
    session_days: int = 14
    cookie_secure: bool = False

    cors_origins: list[str] = ["http://localhost:3000"]

    anthropic_api_key: str | None = None
    llm_model: str = "claude-opus-5-5"
    llm_fallbacks: Literal["default", "off"] = "default"

    embedding_provider: Literal["fastembed", "voyage", "hash"] = "fastembed"
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_dim: int = 384
    voyage_api_key: str | None = None

    file_storage_dir: str = "./data/files"
    max_upload_mb: int = 25

    worker_poll_seconds: float = 2.0
    worker_concurrency: int = 2

    chunk_words: int = 350
    chunk_overlap_words: int = 50

    @property
    def llm_configured(self) -> bool:
        return bool(self.anthropic_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
