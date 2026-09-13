"""Smoke tests for the system endpoints.

These assert the *shape and honesty* of the capability payload: it must report
real installed versions, not a hardcoded list.
"""

from __future__ import annotations

import importlib.metadata as metadata

from fastapi.testclient import TestClient

from app import __version__
from app.main import create_app

client = TestClient(create_app())


def test_health_ok() -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == __version__


def test_root_returns_service_metadata() -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["version"] == __version__


def test_capabilities_reports_real_dependency_versions() -> None:
    response = client.get("/api/capabilities")
    assert response.status_code == 200
    body = response.json()

    deps = {d["module"]: d for d in body["dependencies"]}
    assert deps["rasterio"]["installed"] is True
    assert deps["rasterio"]["version"] == metadata.version("rasterio")
    assert deps["shapely"]["installed"] is True

    # Optional ML stack must be reported truthfully, never assumed present.
    assert "torch" in deps
    assert isinstance(deps["torch"]["installed"], bool)

    assert body["limits"]["tile_overlap"] > 0
    assert ".tif" in body["supported_raster_formats"]
    assert ".kml" in body["supported_boundary_formats"]


def test_unknown_route_is_a_clean_404() -> None:
    assert client.get("/api/does-not-exist").status_code == 404
