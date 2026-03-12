"""
FastAPI exception handlers — register all error handlers on the application.

All handlers return RFC 7807 ``ProblemDetail`` JSON so clients receive a
consistent, machine-readable error shape regardless of what went wrong.

Error type URIs follow the contract in docs/architecture/03_api_contracts.md §4.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.schemas.errors import FieldError, ProblemDetail

logger = logging.getLogger(__name__)


def _problem_response(problem: ProblemDetail) -> JSONResponse:
    """Serialise a ``ProblemDetail`` as a JSON response with the correct status code."""
    return JSONResponse(
        status_code=problem.status,
        content=problem.model_dump(),
        media_type="application/problem+json",
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Attach all Winnow exception handlers to the given FastAPI application."""

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(
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
            type="/errors/validation-error",
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
            type="/errors/validation-error",
            title="Payload Validation Failed",
            status=422,
            detail=f"Stage 1 validation failed: {len(field_errors)} error(s) in payload.",
            instance=str(request.url.path),
            errors=field_errors,
        )
        return _problem_response(problem)

    @app.exception_handler(KeyError)
    async def _handle_key_error(
        request: Request,
        exc: KeyError,
    ) -> JSONResponse:
        """422 — project_id not registered in the registry."""
        problem = ProblemDetail(
            type="/errors/unknown-project",
            title="Unknown Project",
            status=422,
            detail=str(exc).strip("'\""),
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
            type="/errors/internal",
            title="Internal Server Error",
            status=500,
            detail="An unexpected error occurred. Please try again later.",
            instance=str(request.url.path),
        )
        return _problem_response(problem)
