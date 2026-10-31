import logging
from functools import lru_cache
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_CLAUDE_MODEL = "claude-opus-5-5"
DEFAULT_NVIDIA_MODEL = "nvidia/nemotron-3-ultra-550b-a55b"


DEV_SECRET_KEY = "dev-only-change-me-strata-secret-key-0123456789"
log = logging.getLogger("strata.config")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")

    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/strata"

    secret_key: str = DEV_SECRET_KEY
    # Fernet keys for connector secrets at rest, comma-separated; the first encrypts.
    # Empty derives one from SECRET_KEY (see app.core.crypto).
    secrets_encryption_keys: str | None = None
    session_cookie: str = "strata_session"
    session_days: int = 14
    cookie_secure: bool = False
    login_attempts_per_15_min: int = 10

    cors_origins: list[str] = ["http://localhost:3000"]
    # Where people reach Strata (the web app); shown in the MCP setup instructions.
    public_url: str = "http://localhost:3000"

    # LLM providers. An org's model setting picks the provider: "claude-*" models use
    # Anthropic, "vendor/model" ids use NVIDIA's OpenAI-compatible API.
    anthropic_api_key: str | None = None
    nvidia_api_key: str | None = None
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"
    # Experiential Labs gateway (OpenAI-compatible); models are named "explabs:<id>".
    explabs_api_key: str | None = None
    explabs_base_url: str = "https://api.experientiallabs.ai/v1"
    # Most requests in flight per provider per process; more wait their turn.
    nvidia_max_concurrency: int = 4
    # Default model for new orgs; empty picks the best one for the configured key.
    llm_model: str | None = None
    llm_fallbacks: Literal["default", "off"] = "default"

    embedding_provider: Literal["fastembed", "voyage", "hash"] = "fastembed"
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_dim: int = 384
    voyage_api_key: str | None = None

    # Second-stage reranking of hybrid-search candidates. "auto" uses NVIDIA when a
    # key is set, otherwise the local fastembed cross-encoder when installed.
    rerank_provider: Literal["auto", "nvidia", "fastembed", "none"] = "auto"
    nvidia_rerank_model: str = "nvidia/nemotron-3.5-rerank-8b"
    nvidia_rerank_url: str = "https://ai.api.nvidia.com/v1/retrieval"
    fastembed_rerank_model: str = "Xenova/ms-marco-MiniLM-L-6-v2"

    # Model for knowledge-graph extraction; empty uses each org's own model.
    graph_extraction_model: str | None = None
    # Used while the primary is rate limited or out of quota (e.g. a free tier's daily
    # cap): primary free model, fallback paid model.
    graph_extraction_fallback_model: str | None = None

    # Web search tool. Uses whichever key is set (Tavily first).
    tavily_api_key: str | None = None
    brave_api_key: str | None = None

    file_storage_dir: str = "./data/files"
    max_upload_mb: int = 25  # per file (source uploads and chat attachments)
    max_upload_files: int = 20  # per upload request
    # Request body caps: multipart uploads, and everything else (JSON).
    max_multipart_mb: int = 100
    max_json_kb: int = 1024
    max_attachment_chars: int = 400_000

    # Longest a chat answer may run before it's stopped (and its spend with it).
    chat_run_timeout_seconds: int = 900

    worker_poll_seconds: float = 2.0
    worker_concurrency: int = 2

    chunk_words: int = 350
    chunk_overlap_words: int = 50

    @model_validator(mode="after")
    def _check_secret_key(self) -> "Settings":
        """Sessions and stored secrets are signed with SECRET_KEY: a deployment served over
        HTTPS (COOKIE_SECURE=true) must not run with the public default or a short key."""
        weak = self.secret_key == DEV_SECRET_KEY or len(self.secret_key) < 32
        if weak and self.cookie_secure:
            raise ValueError(
                "SECRET_KEY must be set to a random value of at least 32 characters "
                "(e.g. `openssl rand -hex 32`) when COOKIE_SECURE=true"
            )
        if weak:
            log.warning("Using a development SECRET_KEY; set SECRET_KEY before deploying")
        return self

    @property
    def llm_providers(self) -> list[str]:
        return [
            name
            for name, key in (
                ("anthropic", self.anthropic_api_key),
                ("nvidia", self.nvidia_api_key),
                ("explabs", self.explabs_api_key),
            )
            if key
        ]

    @property
    def llm_configured(self) -> bool:
        """A provider with a default chat model is set. The Experiential Labs gateway
        has none of its own (it serves models named explicitly, e.g. for extraction)."""
        return any(p in ("anthropic", "nvidia") for p in self.llm_providers)

    @property
    def default_model(self) -> str:
        if self.llm_model:
            return self.llm_model
        if not self.anthropic_api_key and self.nvidia_api_key:
            return DEFAULT_NVIDIA_MODEL
        return DEFAULT_CLAUDE_MODEL

    @property
    def web_search_provider(self) -> str | None:
        if self.tavily_api_key:
            return "tavily"
        if self.brave_api_key:
            return "brave"
        return None


@lru_cache
def get_settings() -> Settings:
    return Settings()
