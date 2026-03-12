"""
Domain exception hierarchy for Winnow.

All application services raise these domain-specific exceptions exclusively.
The API layer (``app/api/errors.py``) catches them and translates each to the
correct RFC 7807 ``ProblemDetail`` HTTP response.

This enforces the layer contract: services never import from ``fastapi`` and
never know about HTTP status codes.

References
----------
* Architecture: docs/architecture/02_architecture_patterns.md §3
"""

from __future__ import annotations


class WinnowError(Exception):
    """Base class for all Winnow domain exceptions."""


class ProjectNotFoundError(WinnowError):
    """
    Raised when a ``project_id`` is not registered in the registry.

    Replaces bare ``KeyError`` so the API error handler can target this
    specific exception without accidentally catching unrelated key misses.
    """

    def __init__(self, project_id: str) -> None:
        self.project_id = project_id
        super().__init__(f"Project '{project_id}' is not registered.")


class NotImplementedYetError(WinnowError):
    """
    Raised by stub service methods that require the DB persistence layer.

    Replaces ``fastapi.HTTPException(501)`` in service code so that services
    remain fully decoupled from the HTTP transport layer.
    """

    def __init__(self, feature: str) -> None:
        self.feature = feature
        super().__init__(f"{feature} is not yet implemented (requires DB persistence layer).")
