"""
Application settings loaded from environment variables via pydantic-settings.
All values are read from environment variables (or a .env file) at startup.
No magic numbers live here — project-specific thresholds belong in the
registry (``ProjectRegistryEntry``), not in application settings.

DATABASE_URL is built dynamically from individual POSTGRES_* variables so
that Docker Compose, Kubernetes, and CI environments can inject credentials
without constructing a DSN manually. The ``asyncpg`` driver is used in
production; tests substitute ``aiosqlite`` by overriding the FastAPI
``get_db`` dependency directly — the URL is never overridden in code.

References
----------
* Architecture: docs/architecture/02_architecture_patterns.md §3
* Database:     docs/architecture/05_database_design.md §6 (step 6)
"""
from __future__ import annotations

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Application-wide settings resolved from environment variables.

    All fields with defaults are safe to omit in development. Fields without
    defaults must be supplied via the environment or a .env file.

    DATABASE_URL is a computed property assembled from the individual
    POSTGRES_* fields so that no DSN needs to be constructed outside this
    class.  Override POSTGRES_HOST, POSTGRES_PORT, POSTGRES_DB,
    POSTGRES_USER, and POSTGRES_PASSWORD to point at any PostgreSQL instance.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Application ───────────────────────────────────────────────────────────
    APP_NAME: str = Field(default="winnow", description="Human-readable application name.")
    DEBUG: bool = Field(default=False, description="Enable debug mode (verbose logging, reloader).")

    # ── Database connection components ────────────────────────────────────────
    # Individual variables match those set by the ``db`` service in both
    # compose.yaml and compose.dev.yaml, enabling zero-config local dev.
    POSTGRES_HOST: str = Field(
        default="localhost",
        description="PostgreSQL host (service name in Docker Compose, IP/hostname elsewhere).",
    )
    POSTGRES_PORT: int = Field(
        default=5432,
        description="PostgreSQL port.",
    )
    POSTGRES_DB: str = Field(
        default="winnow",
        description="PostgreSQL database name.",
    )
    POSTGRES_USER: str = Field(
        default="winnow",
        description="PostgreSQL user.",
    )
    POSTGRES_PASSWORD: str = Field(
        default="winnow",
        description=(
            "PostgreSQL password. "
            "Must be overridden in production via POSTGRES_PASSWORD env var or .env file."
        ),
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def DATABASE_URL(self) -> str:
        """
        Async SQLAlchemy DSN assembled from individual POSTGRES_* variables.

        Uses the ``asyncpg`` driver for PostgreSQL. Tests substitute their
        own engine via FastAPI's ``dependency_overrides`` — this URL is
        never used in the test suite.
        """
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    # ── API auth ──────────────────────────────────────────────────────────────
    API_KEY: str = Field(
        default="dev-insecure-key",
        description="Shared API key expected in the X-API-Key header (prototype auth).",
    )

    # ── RFC 7807 problem base URI ─────────────────────────────────────────────
    PROBLEM_BASE_URI: str = Field(
        default="https://winnow.example.com",
        description=(
            "Base URI prepended to all RFC 7807 problem type strings, "
            "e.g. 'https://winnow.example.com/errors/validation-error'. "
            "Override per environment to match the deployed hostname."
        ),
    )

    # ── Pagination ────────────────────────────────────────────────────────────
    TASK_PAGE_SIZE_MAX: int = Field(
        default=100,
        ge=1,
        description="Maximum allowed value for the per_page query parameter on task list endpoints.",
    )


# ── Module-level singleton — import this in services and API modules ──────────
settings = Settings()
