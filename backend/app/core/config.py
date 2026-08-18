"""Application settings, loaded from environment / .env."""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    app_env: str = "development"
    log_level: str = "INFO"

    #: The ZIP the aggregator is currently serving.
    target_zip: str = "33009"

    #: Defaults to SQLite so the suite runs without a database server.
    #: Production sets postgresql+psycopg://...
    database_url: str = "sqlite+pysqlite:///./retailscout.db"
    redis_url: str = "redis://localhost:6379/0"

    #: "fixture" (default, offline) or "live". Never defaults to live.
    retailscout_source: str = "fixture"

    http_timeout_seconds: float = 15.0
    http_max_retries: int = 3
    per_retailer_rate_limit_rps: float = 0.5
    user_agent: str = "RetailScout/0.1"

    @property
    def is_postgres(self) -> bool:
        return self.database_url.startswith("postgresql")


@lru_cache
def get_settings() -> Settings:
    return Settings()
