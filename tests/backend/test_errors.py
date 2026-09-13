"""Error handling contract: failures are explicit and machine-readable."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.errors import MissingGeoreferenceError, register_error_handlers


def _app_that_raises(exc: Exception) -> TestClient:
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/boom")
    def boom() -> None:
        raise exc

    return TestClient(app, raise_server_exceptions=False)


def test_domain_error_becomes_structured_json() -> None:
    client = _app_that_raises(
        MissingGeoreferenceError(
            "Raster has no CRS.", detail={"filename": "drone.png"}
        )
    )
    response = client.get("/boom")
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "missing_georeference"
    assert error["message"] == "Raster has no CRS."
    assert error["detail"]["filename"] == "drone.png"
