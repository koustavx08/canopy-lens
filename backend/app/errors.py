"""Typed application errors and the handlers that turn them into JSON.

Design rule for CanopyLens: when something cannot be computed honestly
(missing georeferencing, unavailable model, unreadable raster) the API returns
an explicit error with a machine-readable `code`. It never substitutes a
guess, a default, or a fabricated result.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


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
                    "message": "Request validation failed.",
                    "detail": {"errors": exc.errors()},
                }
            },
        )
