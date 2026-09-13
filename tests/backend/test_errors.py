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


def test_an_unexpected_bug_becomes_a_plain_500_with_no_traceback() -> None:
    # A stack trace in a response tells an attacker the file layout and the
    # library versions, and tells the user nothing they can act on.
    client = _app_that_raises(ZeroDivisionError("division by zero"))

    response = client.get("/boom")

    assert response.status_code == 500
    body = response.text
    assert response.json()["error"]["code"] == "internal_error"
    assert "Traceback" not in body
    assert "ZeroDivisionError" not in body
    assert "app/errors.py" not in body


def test_a_wrong_url_uses_the_same_envelope_as_a_domain_error() -> None:
    client = _app_that_raises(ValueError("unused"))

    response = client.get("/no-such-route")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
