"""
Application settings loaded from environment variables via pydantic-settings.

All values are read from environment variables (or a .env file) at startup.
No magic numbers live here — project-specific thresholds belong in the
registry (``ProjectRegistryEntry``), not in application settings.

References
----------
* Architecture: docs/architecture/02_architecture_patterns.md §3
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Application-wide settings resolved from environment variables.

    All fields with defaults are safe to omit in development; fields without
    defaults (``API_KEY``) must be supplied via the environment or a .env file.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    APP_NAME: str = Field(default="winnow", description="Human-readable application name.")
    DEBUG: bool = Field(default=False, description="Enable debug mode (verbose logging, reloader).")
    DATABASE_URL: str = Field(
        default="postgresql+asyncpg://winnow:winnow@localhost:5432/winnow",
        description="SQLAlchemy-compatible async database URL.",
    )
    API_KEY: str = Field(
        default="dev-insecure-key",
        description="Shared API key expected in the X-API-Key header (prototype auth).",
    )
    PROBLEM_BASE_URI: str = Field(
        default="https://winnow.example.com",
        description=(
            "Base URI prepended to all RFC 7807 problem type strings, "
            "e.g. 'https://winnow.example.com/errors/validation-error'. "
            "Override per environment to match the deployed hostname."
        ),
    )
    TASK_PAGE_SIZE_MAX: int = Field(
        default=100,
        ge=1,
        description="Maximum allowed value for the per_page query parameter on task list endpoints.",
    )


# ── Module-level singleton — import this in services and API modules ──────────
settings = Settings()
