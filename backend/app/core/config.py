"""
core/config.py — Application Settings Management

WHY THIS EXISTS:
    Hard-coding config values (URLs, ports, passwords) is a red flag in every
    code review. Pydantic Settings reads values from environment variables (or
    a .env file), validates their types, and provides defaults.

    This follows the 12-Factor App methodology:
    https://12factor.net/config

    Benefit: The exact same code runs in local dev (reading from .env) and in
    AWS production (reading from ECS Task environment variables) with ZERO code
    changes.

HOW IT WORKS:
    1. Pydantic reads each field from the matching environment variable.
    2. If the variable is missing and there's no default, it raises an error
       at startup — catching config mistakes before they cause runtime failures.
    3. All settings are available via `from app.core.config import settings`.
"""

from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    """
    All application configuration, loaded from environment variables.
    Override any value by setting the environment variable or adding it to .env.
    """

    model_config = SettingsConfigDict(
        env_file=".env",           # Load from .env file in the working directory
        env_file_encoding="utf-8",
        case_sensitive=False,      # ELASTICSEARCH_URL == elasticsearch_url
        extra="ignore",            # Ignore unknown env vars (don't crash)
    )

    # ── Application ──────────────────────────────────────────────────────────
    app_env: str = Field(default="development", description="Environment: development | production")
    app_version: str = Field(default="1.0.0", description="Application version")
    debug: bool = Field(default=True, description="Enable debug mode")

    # ── API ───────────────────────────────────────────────────────────────────
    api_host: str = Field(default="0.0.0.0")
    api_port: int = Field(default=8000)
    api_prefix: str = Field(default="/api/v1")

    # ── CORS ──────────────────────────────────────────────────────────────────
    # In production, replace "*" with your actual frontend domain
    cors_origins: list[str] = Field(
        default=["http://localhost:3000", "http://localhost:5173"],
        description="Allowed CORS origins for the React frontend",
    )

    # ── Elasticsearch ─────────────────────────────────────────────────────────
    elasticsearch_url: str = Field(
        default="http://localhost:9200",
        description="Elasticsearch connection URL",
    )
    elasticsearch_index_prefix: str = Field(
        default="logs",
        description="Prefix for daily index names, e.g. logs-2024.01.15",
    )
    elasticsearch_username: str = Field(default="", description="ES username (leave blank for local)")
    elasticsearch_password: str = Field(default="", description="ES password (leave blank for local)")

    # ── Kafka ─────────────────────────────────────────────────────────────────
    kafka_bootstrap_servers: str = Field(
        default="localhost:9092",
        description="Comma-separated list of Kafka broker addresses",
    )
    kafka_topic_raw_logs: str = Field(default="raw-logs")
    kafka_topic_processed_logs: str = Field(default="processed-logs")
    kafka_topic_alerts: str = Field(default="alerts")
    kafka_consumer_group: str = Field(default="logforge-consumers")

    # ── PostgreSQL ────────────────────────────────────────────────────────────
    postgres_host: str = Field(default="localhost")
    postgres_port: int = Field(default=5432)
    postgres_db: str = Field(default="logforge")
    postgres_user: str = Field(default="logforge")
    postgres_password: str = Field(default="logforge_secret")

    @property
    def database_url(self) -> str:
        """Build async SQLAlchemy connection string."""
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    # ── ML Model ─────────────────────────────────────────────────────────────
    ml_model_path: str = Field(default="./ml/models/isolation_forest.pkl")
    anomaly_threshold: float = Field(
        default=0.7,
        description="Anomaly score threshold (0-1). Scores above this are flagged.",
    )
    ml_retrain_interval_hours: int = Field(default=24)

    # ── Alerting ─────────────────────────────────────────────────────────────
    smtp_host: str = Field(default="smtp.gmail.com")
    smtp_port: int = Field(default=587)
    smtp_user: str = Field(default="")
    smtp_password: str = Field(default="")
    alert_from_email: str = Field(default="alerts@logforge.io")

    # ── Computed helpers ──────────────────────────────────────────────────────
    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def kafka_use_ssl(self) -> bool:
        """AWS MSK requires SSL; local Kafka does not."""
        return self.is_production


@lru_cache()
def get_settings() -> Settings:
    """
    Return a cached singleton Settings instance.

    WHY lru_cache?
        Without it, every call to get_settings() would re-read the .env file
        from disk. With it, the file is read ONCE at startup and cached forever.
        This is the standard FastAPI pattern for settings.

    Usage in route handlers:
        from fastapi import Depends
        from app.core.config import get_settings, Settings

        @router.get("/example")
        async def example(settings: Settings = Depends(get_settings)):
            return {"version": settings.app_version}
    """
    return Settings()


# Module-level singleton for import convenience
# Usage: from app.core.config import settings
settings = get_settings()
