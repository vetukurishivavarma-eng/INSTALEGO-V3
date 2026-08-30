"""Application configuration.

Everything the application needs to know about its environment arrives through
this module. Nothing else reads ``os.environ`` directly, which keeps the set of
knobs discoverable and lets tests override them in one place.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_ROOT.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(PROJECT_ROOT / ".env", BACKEND_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ------------------------------------------------------------ environment
    ENVIRONMENT: Literal["development", "test", "staging", "production"] = "development"
    LOG_LEVEL: str = "INFO"
    API_PREFIX: str = "/api"

    # ------------------------------------------------------------ persistence
    DATABASE_URL: str = "sqlite+pysqlite:///./ldai.db"
    REDIS_URL: str = "redis://localhost:6379/0"
    TASK_QUEUE_BACKEND: Literal["inline", "arq"] = "inline"

    # ------------------------------------------------------------ storage
    STORAGE_BACKEND: Literal["local", "s3"] = "local"
    STORAGE_LOCAL_ROOT: Path = BACKEND_ROOT / "var" / "documents"
    S3_ENDPOINT: str | None = None
    S3_BUCKET: str = "legal-documents"
    S3_ACCESS_KEY: str | None = None
    S3_SECRET_KEY: str | None = None
    S3_REGION: str = "us-east-1"

    # ------------------------------------------------------------ llm
    LLM_BASE_URL: str = "http://localhost:8000/v1"
    LLM_MODEL: str = "Qwen3-VL-8B-Instruct"
    LLM_API_KEY: str = "not-needed-for-local-vllm"
    LLM_TIMEOUT: float = 120.0
    LLM_MAX_TOKENS: int = 2048
    LLM_TEMPERATURE: float = 0.0
    LLM_MAX_RETRIES: int = 3
    LLM_USE_MOCK: bool = False
    # Minimum seconds between requests to the model endpoint. Free tiers cap
    # requests per minute, and spacing calls is far cheaper than discovering
    # the limit through 429s and burning the retry budget on them.
    # 0 disables throttling; 3.0 keeps a 20/minute tier comfortable.
    LLM_MIN_REQUEST_INTERVAL: float = 0.0
    # Sent by some gateways for attribution; harmless elsewhere.
    LLM_EXTRA_HEADERS: dict[str, str] = Field(default_factory=dict)

    # ------------------------------------------------------------ uploads
    MAX_UPLOAD_BYTES: int = 50 * 1024 * 1024

    # ------------------------------------------------------------ versioning
    ANALYSIS_VERSION: str = "1.0.0"
    CONFIG_DIR: Path = PROJECT_ROOT / "configs"
    DEFAULT_BANK_ID: str = "default"

    # ------------------------------------------------------------ security
    AUTH_ENABLED: bool = False
    AUTH_SECRET: str = "change-me-in-production"

    # Extensions accepted at upload. Kept here rather than in the API layer so
    # the worker can validate the same set when it re-reads a stored file.
    ALLOWED_EXTENSIONS: set[str] = Field(
        default={".pdf", ".doc", ".docx", ".xls", ".xlsx", ".jpg", ".jpeg", ".png"}
    )

    @field_validator("STORAGE_LOCAL_ROOT", "CONFIG_DIR", mode="before")
    @classmethod
    def _expand(cls, v: str | Path) -> Path:
        path = Path(v).expanduser()
        return path if path.is_absolute() else (PROJECT_ROOT / path).resolve()

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"

    @property
    def bank_config_dir(self) -> Path:
        return self.CONFIG_DIR / "banks"

    @property
    def report_template_dir(self) -> Path:
        return self.CONFIG_DIR / "report_templates"


@lru_cache
def get_settings() -> Settings:
    """Cached accessor. Tests clear the cache after mutating the environment."""
    return Settings()


settings = get_settings()
