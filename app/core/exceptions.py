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


class SubmissionNotFoundError(WinnowError):
    """Raised when a submission_id does not exist in the store."""

    def __init__(self, submission_id: object) -> None:
        self.submission_id = submission_id
        super().__init__(f"Submission '{submission_id}' not found.")


class DuplicateVoteError(WinnowError):
    """Raised when the same user_id attempts to vote twice on the same submission."""

    def __init__(self, submission_id: object, user_id: object) -> None:
        self.submission_id = submission_id
        self.user_id = user_id
        super().__init__(
            f"User '{user_id}' has already voted on submission '{submission_id}'."
        )


class AlreadyFinalizedError(WinnowError):
    """Raised when a vote or status change is attempted on an already-finalized submission."""

    def __init__(self, submission_id: object, current_status: str) -> None:
        self.submission_id = submission_id
        self.current_status = current_status
        super().__init__(
            f"Submission '{submission_id}' is already finalized "
            f"with status '{current_status}'."
        )


class NotEligibleError(WinnowError):
    """Raised when a reviewer does not meet the trust/role requirements for a submission."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"Reviewer is not eligible: {reason}")
