"""
FastAPI exception handlers — register all error handlers on the application.

All handlers return RFC 7807 ``ProblemDetail`` JSON so clients receive a
consistent, machine-readable error shape regardless of what went wrong.

Error type URIs are absolute URIs constructed from ``settings.PROBLEM_BASE_URI``
following the contract in docs/architecture/03_api_contracts.md §4.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.core.config import settings
from app.core.exceptions import (
    AlreadyFinalizedError,
    ConflictError,
    DuplicateVoteError,
    InvalidEntityTypeError,
    NotEligibleError,
    NotImplementedYetError,
    ProjectNotFoundError,
    SubmissionNotFoundError,
)
from app.schemas.errors import FieldError, ProblemDetail

logger = logging.getLogger(__name__)


def _problem_response(problem: ProblemDetail) -> JSONResponse:
    """Serialise a ``ProblemDetail`` as a JSON response with the correct status code.

    ``None`` fields (e.g. ``instance`` when not applicable) are omitted from
    the response body in accordance with RFC 7807 §3.3.
    """
    return JSONResponse(
        status_code=problem.status,
        content=problem.model_dump(exclude_none=True),
        media_type="application/problem+json",
    )


def _type_uri(slug: str) -> str:
    """Return an absolute RFC 7807 type URI for the given slug."""
    return f"{settings.PROBLEM_BASE_URI}/errors/{slug}"


def register_exception_handlers(app: FastAPI) -> None:
    """Attach all Winnow exception handlers to the given FastAPI application."""

    @app.exception_handler(RequestValidationError)
    async def _handle_request_validation_error(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        """422 — envelope or payload failed Pydantic validation."""
        field_errors = [
            FieldError(
                field=".".join(str(loc) for loc in err["loc"]),
                message=err["msg"],
                type=err["type"],
            )
            for err in exc.errors()
        ]
        problem = ProblemDetail(
            type=_type_uri("validation-error"),
            title="Payload Validation Failed",
            status=422,
            detail=f"Validation failed: {len(field_errors)} error(s) in request.",
            instance=str(request.url.path),
            errors=field_errors,
        )
        return _problem_response(problem)

    @app.exception_handler(ValidationError)
    async def _handle_pydantic_validation_error(
        request: Request,
        exc: ValidationError,
    ) -> JSONResponse:
        """422 — Stage 1 payload validation failed inside a service function."""
        field_errors = [
            FieldError(
                field=".".join(str(loc) for loc in err["loc"]),
                message=err["msg"],
                type=err["type"],
            )
            for err in exc.errors()
        ]
        problem = ProblemDetail(
            type=_type_uri("validation-error"),
            title="Payload Validation Failed",
            status=422,
            detail=f"Stage 1 validation failed: {len(field_errors)} error(s) in payload.",
            instance=str(request.url.path),
            errors=field_errors,
        )
        return _problem_response(problem)

    @app.exception_handler(ProjectNotFoundError)
    async def _handle_project_not_found(
        request: Request,
        exc: ProjectNotFoundError,
    ) -> JSONResponse:
        """422 — project_id not registered in the registry."""
        problem = ProblemDetail(
            type=_type_uri("unknown-project"),
            title="Unknown Project",
            status=422,
            detail=str(exc),
            instance=str(request.url.path),
        )
        return _problem_response(problem)

    @app.exception_handler(NotImplementedYetError)
    async def _handle_not_implemented_yet(
        request: Request,
        exc: NotImplementedYetError,
    ) -> JSONResponse:
        """501 — endpoint exists but requires the DB persistence layer."""
        problem = ProblemDetail(
            type=_type_uri("not-implemented"),
            title="Not Implemented",
            status=501,
            detail=str(exc),
            instance=str(request.url.path),
        )
        return _problem_response(problem)

    @app.exception_handler(SubmissionNotFoundError)
    async def _handle_submission_not_found(
        request: Request,
        exc: SubmissionNotFoundError,
    ) -> JSONResponse:
        """404 — submission_id not found in the store."""
        problem = ProblemDetail(
            type=_type_uri("submission-not-found"),
            title="Submission Not Found",
            status=404,
            detail=str(exc),
            instance=str(request.url.path),
        )
        return _problem_response(problem)

    @app.exception_handler(DuplicateVoteError)
    async def _handle_duplicate_vote(
        request: Request,
        exc: DuplicateVoteError,
    ) -> JSONResponse:
        """409 — user has already voted on this submission."""
        problem = ProblemDetail(
            type=_type_uri("duplicate-vote"),
            title="Duplicate Vote",
            status=409,
            detail=str(exc),
            instance=str(request.url.path),
        )
        return _problem_response(problem)

    @app.exception_handler(AlreadyFinalizedError)
    async def _handle_already_finalized(
        request: Request,
        exc: AlreadyFinalizedError,
    ) -> JSONResponse:
        """409 — submission already finalized."""
        problem = ProblemDetail(
            type=_type_uri("already-finalized"),
            title="Already Finalized",
            status=409,
            detail=str(exc),
            instance=str(request.url.path),
        )
        return _problem_response(problem)

    @app.exception_handler(InvalidEntityTypeError)
    async def _handle_invalid_entity_type(
        request: Request,
        exc: InvalidEntityTypeError,
    ) -> JSONResponse:
        """422 — entity_type not in the project's valid_entity_types allowlist."""
        problem = ProblemDetail(
            type=_type_uri("invalid-entity-type"),
            title="Invalid Entity Type",
            status=422,
            detail=str(exc),
            instance=str(request.url.path),
            errors=[
                FieldError(
                    field="metadata.entity_type",
                    message=(
                        f"'{exc.entity_type}' is not a recognised entity type "
                        f"for project '{exc.project_id}'. "
                        f"Valid types: {exc.valid_types}"
                    ),
                    type="invalid_entity_type",
                )
            ],
        )
        return _problem_response(problem)

    @app.exception_handler(ConflictError)
    async def _handle_conflict(
        request: Request,
        exc: ConflictError,
    ) -> JSONResponse:
        """409 — new submission conflicts with a terminal-state prior submission."""
        problem = ProblemDetail(
            type=_type_uri("submission-conflict"),
            title="Submission Conflict",
            status=409,
            detail=str(exc),
            instance=str(request.url.path),
        )
        return _problem_response(problem)

    @app.exception_handler(NotEligibleError)
    async def _handle_not_eligible(
        request: Request,
        exc: NotEligibleError,
    ) -> JSONResponse:
        """403 — reviewer does not meet eligibility requirements."""
        problem = ProblemDetail(
            type=_type_uri("not-eligible"),
            title="Not Eligible",
            status=403,
            detail=str(exc),
            instance=str(request.url.path),
        )
        return _problem_response(problem)

    @app.exception_handler(400)
    async def _handle_bad_request(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        """400 — malformed request (e.g. invalid JSON body, truncated payload)."""
        problem = ProblemDetail(
            type=_type_uri("bad-request"),
            title="Bad Request",
            status=400,
            detail="The request body is malformed or could not be parsed.",
            instance=str(request.url.path),
        )
        return _problem_response(problem)

    @app.exception_handler(Exception)
    async def _handle_generic_error(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        """500 — unhandled exception."""
        logger.exception("Unhandled exception for %s %s", request.method, request.url.path)
        problem = ProblemDetail(
            type=_type_uri("internal"),
            title="Internal Server Error",
            status=500,
            detail="An unexpected error occurred. Please try again later.",
            instance=str(request.url.path),
        )
        return _problem_response(problem)
