"""
SQLAlchemy 2.0 declarative base and shared column mixins.

Every ORM model in Winnow inherits from ``Base`` (for table mapping) plus
the mixins it needs.  The ``MetaData`` naming convention ensures Alembic
autogenerate produces deterministic constraint names across all databases.

References
----------
* Database design: docs/architecture/05_database_design.md §5, §6 step 1
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# ── Deterministic constraint naming convention ────────────────────────────────
# Alembic uses these patterns to auto-name CHECK, UNIQUE, FK, and index
# constraints so that migrations are reproducible and diff-friendly.
NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """
    Project-wide SQLAlchemy declarative base.

    All ORM models must inherit from this class.  The shared ``MetaData``
    object carries the naming convention so every constraint and index is
    named consistently.
    """

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


# ── Reusable column mixins ────────────────────────────────────────────────────

class UUIDPrimaryKeyMixin:
    """
    Adds a ``id`` UUID primary key column with a Python-side default.

    Using ``uuid.uuid4`` as the Python default (rather than a server default)
    ensures the PK is always available immediately after instantiation,
    before any database round-trip, which is important for building
    relationships in the same unit of work.
    """

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )


class TimestampMixin:
    """
    Adds ``created_at`` and ``updated_at`` timezone-aware timestamp columns.

    ``created_at`` is set once by the database on INSERT via ``server_default``.
    ``updated_at`` is refreshed by the database on every UPDATE via
    ``onupdate``; Python-side ``default`` and ``onupdate`` are also set so
    that SQLite (used in tests) — which lacks ``now()`` server functions —
    always receives an explicit value.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(__import__("datetime").timezone.utc),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(__import__("datetime").timezone.utc),
        onupdate=lambda: datetime.now(__import__("datetime").timezone.utc),
        server_default=func.now(),
    )
