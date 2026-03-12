"""
Shared pytest configuration and fixtures for the Winnow test suite.

bootstrap() is called once per test session so that every test module
has access to a fully populated registry without relying on import-time
side effects.
"""
from __future__ import annotations

import pytest

from app.bootstrap import bootstrap


@pytest.fixture(scope="session", autouse=True)
def _populate_registry() -> None:
    """Populate the project registry before any test runs."""
    bootstrap()
