"""Typed application errors and the handlers that turn them into JSON.

Design rule for CanopyLens: when something cannot be computed honestly
(missing georeferencing, unavailable model, unreadable raster) the API returns
an explicit error with a machine-readable `code`. It never substitutes a
guess, a default, or a fabricated result.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

log = logging.getLogger(__name__)


class CanopyLensError(Exception):
    """Base class for errors that are safe to show to an end user."""

    status_code = 400
    code = "canopylens_error"

    def __init__(self, message: str, *, detail: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail or {}

    def to_payload(self) -> dict:
        return {"error": {"code": self.code, "message": self.message, "detail": self.detail}}


class UnsupportedFileError(CanopyLensError):
    status_code = 415
    code = "unsupported_file"


class InvalidRasterError(CanopyLensError):
    status_code = 422
    code = "invalid_raster"


class MissingGeoreferenceError(CanopyLensError):
    status_code = 422
    code = "missing_georeference"


class InvalidBoundaryError(CanopyLensError):
    status_code = 422
    code = "invalid_boundary"


class InvalidParameterError(CanopyLensError):
    """A well-formed request naming something that does not exist.

    Distinct from `invalid_request`, which is a value out of range, and from
    `engine_unavailable`, which is a real engine that cannot run here.
    """

    status_code = 422
    code = "invalid_parameter"


class EngineUnavailableError(CanopyLensError):
    status_code = 503
    code = "engine_unavailable"


class ResourceNotFoundError(CanopyLensError):
    status_code = 404
    code = "not_found"


class PayloadTooLargeError(CanopyLensError):
    status_code = 413
    code = "payload_too_large"


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(CanopyLensError)
    async def _handle_app_error(_: Request, exc: CanopyLensError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_payload())

    @app.exception_handler(RequestValidationError)
    async def _handle_validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "invalid_request",
                    "message": _validation_message(exc),
                    "detail": {"errors": jsonable_encoder(exc.errors())},
                }
            },
        )

    @app.exception_handler(HTTPException)
    async def _handle_http(_: Request, exc: HTTPException) -> JSONResponse:
        """Put framework errors in the same envelope as application errors.

        Without this, a wrong URL answers `{"detail": "Not Found"}` while every
        other failure answers `{"error": {...}}`, and the client needs two ways
        to read one API. Registered against Starlette's class rather than
        FastAPI's subclass, so routing errors - which Starlette raises itself -
        are caught too.
        """
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": _HTTP_CODES.get(exc.status_code, "http_error"),
                    "message": str(exc.detail),
                    "detail": {},
                }
            },
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        """The last line: a bug becomes a plain 500, never a traceback.

        A stack trace in an HTTP response tells an attacker the file layout and
        library versions, and tells the user nothing they can act on. It is
        logged in full on the server, where it is useful, and the caller is told
        only that the request failed.
        """
        log.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "internal_error",
                    "message": (
                        "The server failed to handle this request. Nothing "
                        "partial was returned. The details are in the server log."
                    ),
                    "detail": {},
                }
            },
        )


_HTTP_CODES = {404: "not_found", 405: "method_not_allowed", 413: "payload_too_large"}


def _validation_message(exc: RequestValidationError) -> str:
    """Name the field that was wrong, rather than saying only that one was.

    "Request validation failed" sends a user back to the documentation;
    "confidence_threshold: Input should be less than or equal to 1" sends them
    to the field.
    """
    errors = exc.errors()
    if not errors:
        return "Request validation failed."
    first = errors[0]
    location = ".".join(str(part) for part in first.get("loc", ()) if part != "body")
    message = first.get("msg", "is invalid")
    prefix = f"{location}: " if location else ""
    suffix = f" ({len(errors) - 1} more problem(s))" if len(errors) > 1 else ""
    return f"{prefix}{message}.{suffix}"
