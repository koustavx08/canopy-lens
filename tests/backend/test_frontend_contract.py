"""The fields the browser reads, named in one place.

`frontend/src/types.ts` declares the shape of every response the UI consumes,
and TypeScript will happily compile against a declaration that the server no
longer honours — the mismatch only shows up as a blank metric or a map with no
crowns on it. These tests are the other half of that contract: if a payload key
is renamed or dropped here, this file fails rather than the interface quietly
going empty.

They assert the presence and meaning of keys, not their values. What the
numbers should be is covered in `test_analysis_api.py`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import create_app
from app.services.jobs import get_job_store

from . import await_job
from ..conftest import (
    PIXEL_METRES,
    SCENE_CIRCLES,
    SCENE_SIZE,
    UTM_ORIGIN_X,
    UTM_ORIGIN_Y,
    draw_scene,
    kml_polygon,
    utm_to_lonlat,
    write_raster,
)

app = create_app()
client = TestClient(app)

# Read by frontend/src/components/Sidebar.tsx and App.tsx.
CAPABILITY_KEYS = {
    "version",
    "environment",
    "dependencies",
    "engines",
    "limits",
    "defaults",
    "supported_raster_formats",
    "supported_boundary_formats",
}

# Read by frontend/src/lib/useAnalysis.ts.
ACCEPTED_KEYS = {"job_id", "status", "status_url", "result_url"}

JOB_KEYS = {
    "job_id",
    "status",
    "stage",
    "progress",
    "image_name",
    "boundary_name",
    "parameters",
    "created_at",
    "started_at",
    "finished_at",
    "processing_time_seconds",
    "error",
    "result_url",
}

# Read by frontend/src/components/ResultsPanel.tsx and MapView.tsx.
RESULT_KEYS = {
    "job_id",
    "status",
    "image_metadata",
    "boundary_metadata",
    "summary",
    "detections",
    "processing_time",
    "warnings",
    "limitations",
    "downloads",
}

IMAGE_KEYS = {"filename", "width_px", "height_px", "georeferenced", "source_crs"}

SUMMARY_KEYS = {
    "tree_count",
    "total_canopy_area_m2",
    "analyzed_area_m2",
    "canopy_coverage_pct",
    "coverage_basis",
    "mean_crown_area_m2",
    "median_crown_area_m2",
    "mean_confidence",
    "median_confidence",
    "confidence_distribution",
    "confidence_kind",
    "confidence_threshold",
    "engine",
    "raw_detections",
    "duplicates_removed",
    "excluded_by_boundary",
    "boundary_applied",
}

DETECTION_KEYS = {
    "id",
    "confidence",
    "confidence_kind",
    "pixel_area",
    "area_m2",
    "centroid_x_px",
    "centroid_y_px",
    "centroid_lon",
    "centroid_lat",
    "engine",
    "geometry",
    "geometry_px",
}

BOUNDARY_KEYS = {
    "source_format",
    "part_count",
    "area_m2",
    "area_within_imagery_m2",
    "imagery_covers_pct",
    "detections_excluded",
    "geometry",
}


@pytest.fixture(scope="module")
def scene():
    return draw_scene(SCENE_SIZE, SCENE_SIZE, SCENE_CIRCLES)


@pytest.fixture(scope="module")
def raster(tmp_path_factory, scene) -> Path:
    return write_raster(tmp_path_factory.mktemp("contract") / "scene.tif", scene)


@pytest.fixture(scope="module")
def boundary(tmp_path_factory) -> Path:
    """The left half of the scene, so the boundary genuinely clips something."""
    half_width_m = SCENE_SIZE / 2 * PIXEL_METRES
    full_height_m = SCENE_SIZE * PIXEL_METRES
    corners = [
        (UTM_ORIGIN_X, UTM_ORIGIN_Y),
        (UTM_ORIGIN_X + half_width_m, UTM_ORIGIN_Y),
        (UTM_ORIGIN_X + half_width_m, UTM_ORIGIN_Y - full_height_m),
        (UTM_ORIGIN_X, UTM_ORIGIN_Y - full_height_m),
    ]
    path = tmp_path_factory.mktemp("contract-kml") / "stand.kml"
    path.write_text(kml_polygon(utm_to_lonlat(corners)), encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def analysed(tmp_path_factory, raster, boundary) -> dict:
    """One analysis, reused: running the detector per test is minutes wasted."""
    settings = Settings(data_dir=tmp_path_factory.mktemp("contract-data"))
    app.dependency_overrides[get_settings] = lambda: settings
    get_job_store().clear()
    try:
        with raster.open("rb") as image, boundary.open("rb") as outline:
            accepted = client.post(
                "/api/analyze",
                files={"image": (raster.name, image), "boundary": (boundary.name, outline)},
            )
        assert accepted.status_code == 202, accepted.text
        job_id = accepted.json()["job_id"]
        job = await_job(client, job_id)
        assert job["status"] == "completed", job.get("error")
        return {
            "accepted": accepted.json(),
            "job": job,
            "result": client.get(f"/api/results/{job_id}").json(),
        }
    finally:
        app.dependency_overrides.clear()


def test_capabilities_carry_what_the_controls_are_built_from():
    body = client.get("/api/capabilities").json()
    assert CAPABILITY_KEYS <= set(body)
    # The sliders take their defaults from here rather than hardcoding them,
    # so the UI and the server cannot drift apart.
    assert {"confidence_threshold", "min_crown_area_m2", "engine"} <= set(body["defaults"])
    assert {"max_upload_mb", "max_boundary_upload_mb"} <= set(body["limits"])
    for engine in body["engines"]:
        assert {"name", "title", "description", "available", "reason"} <= set(engine)


def test_submitting_returns_the_urls_the_client_polls(analysed):
    assert ACCEPTED_KEYS <= set(analysed["accepted"])


def test_the_job_carries_everything_the_progress_display_reads(analysed):
    job = analysed["job"]
    assert JOB_KEYS <= set(job)
    assert 0.0 <= job["progress"] <= 1.0


def test_the_result_carries_every_panel_the_ui_renders(analysed):
    assert RESULT_KEYS <= set(analysed["result"])


def test_the_six_headline_metrics_exist(analysed):
    summary = analysed["result"]["summary"]
    assert SUMMARY_KEYS <= set(summary)
    for bin_ in summary["confidence_distribution"]:
        assert {"lower", "upper", "count"} <= set(bin_)


def test_the_inspector_fields_exist_on_every_crown(analysed):
    detections = analysed["result"]["detections"]
    assert detections, "the scene should produce at least one crown"
    for crown in detections:
        assert DETECTION_KEYS <= set(crown)
        # Pixel geometry is what the imagery view draws, and it is the only
        # geometry imagery without a coordinate system can offer.
        assert crown["geometry_px"] is not None


def test_the_image_and_boundary_metadata_the_map_needs(analysed):
    result = analysed["result"]
    assert IMAGE_KEYS <= set(result["image_metadata"])
    assert BOUNDARY_KEYS <= set(result["boundary_metadata"])
    # The map draws the boundary the analysis actually clipped to rather than
    # re-parsing the uploaded file.
    assert result["boundary_metadata"]["geometry"] is not None


def test_the_limitations_travel_with_the_numbers(analysed):
    limitations = analysed["result"]["limitations"]
    assert limitations["notice"]
    assert len(limitations["items"]) > 0


def test_the_download_buttons_have_urls_to_call(analysed):
    downloads = analysed["result"]["downloads"]
    assert downloads["csv"].endswith("/csv")
    assert downloads["geojson"].endswith("/geojson")
    for url in downloads.values():
        response = client.get(url)
        assert response.status_code == 200
        assert "attachment" in response.headers["content-disposition"]
