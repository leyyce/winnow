"""
Alembic environment — async SQLAlchemy 2.0 configuration.

Migrations run in "online" mode only (no offline SQL generation needed for
this prototype).  The async engine is built from ``app/core/config.py``
which assembles ``DATABASE_URL`` from individual POSTGRES_* environment
variables.  No credentials are hardcoded here.

``target_metadata`` points at the shared ``Base.metadata`` that all ORM
models populate via ``app/models/__init__.py`` (import side-effect registers
every table with the mapper registry).

References
----------
* Database design: docs/architecture/05_database_design.md §4, §6 step 7
"""
from __future__ import annotations

import asyncio
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

# ── Make the project root importable when running alembic from any directory ──
# Alembic changes cwd to the script_location; add the project root so that
# ``app.*`` imports resolve correctly.
PROJECT_ROOT = Path(__file__).resolve().parents[3]  # …/winnow
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Import ALL models so their tables are registered on Base.metadata
import app.models  # noqa: F401  (side-effect: populates mapper registry)
from app.core.config import settings
from app.models.base import Base

# ── Alembic config object ─────────────────────────────────────────────────────
config = context.config

# Set up Python logging from the [loggers] section of alembic.ini
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# The metadata object that autogenerate compares against the live DB schema
target_metadata = Base.metadata


# ── Async online migration ────────────────────────────────────────────────────

def do_run_migrations(connection):
    """Execute migrations synchronously on the given connection."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # Render CHECK constraints emitted by SQLAlchemy models in diffs
        render_as_batch=False,
        # Include schema-level objects (indexes, constraints) in autogenerate
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """
    Create the async engine from settings and run migrations online.

    Using ``connect()`` rather than ``begin()`` so that Alembic controls
    the transaction boundary via ``context.begin_transaction()``.
    """
    connectable = create_async_engine(
        settings.DATABASE_URL,
        pool_pre_ping=True,
        echo=False,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    """Entry point called by Alembic's runtime when running ``alembic upgrade``."""
    asyncio.run(run_async_migrations())


run_migrations_online()
