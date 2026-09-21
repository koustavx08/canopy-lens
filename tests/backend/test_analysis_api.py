"""The analysis endpoints, end to end over HTTP.

These tests drive the API the way the browser will: submit imagery, poll the
job, fetch the result, download both exports. The scene is the synthetic one
used throughout the suite, so the figures the API returns can be checked
against the geometry that was drawn rather than against a value recorded from
an earlier run.

Analyses run on a bounded pool, so a submission returns while the work is still
in flight - exactly as it does in production. `await_job` polls until the job
settles, which is what the browser does, and fails loudly rather than hanging if
it never does. The intermediate states are exercised in `test_jobs.py`, where
the store can be driven directly.
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.limitations import LIMITATIONS
from app.main import create_app
from app.routers.analysis import analysis_pool
from app.services.jobs import get_job_store

from . import await_job
from ..conftest import (
    SCENE_CIRCLES,
    SCENE_SIZE,
    UTM_ORIGIN_X,
    UTM_ORIGIN_Y,
    PIXEL_METRES,
    draw_scene,
    kml_polygon,
    utm_to_lonlat,
    write_raster,
)

app = create_app()
client = TestClient(app)


# Analysing the scene takes real work, so the classes that only read one result
# share a raster built once for the module rather than per test.


@pytest.fixture(scope="module")
def scene():
    return draw_scene(SCENE_SIZE, SCENE_SIZE, SCENE_CIRCLES)


@pytest.fixture(scope="module")
def shared_raster(tmp_path_factory, scene) -> Path:
    return write_raster(tmp_path_factory.mktemp("api") / "scene_utm.tif", scene)


@pytest.fixture(scope="module")
def shared_left_half_kml(tmp_path_factory) -> Path:
    half_width_m = SCENE_SIZE / 2 * PIXEL_METRES
    full_height_m = SCENE_SIZE * PIXEL_METRES
    corners = [
        (UTM_ORIGIN_X, UTM_ORIGIN_Y),
        (UTM_ORIGIN_X + half_width_m, UTM_ORIGIN_Y),
        (UTM_ORIGIN_X + half_width_m, UTM_ORIGIN_Y - full_height_m),
        (UTM_ORIGIN_X, UTM_ORIGIN_Y - full_height_m),
    ]
    path = tmp_path_factory.mktemp("api_kml") / "left_half.kml"
    path.write_text(kml_polygon(utm_to_lonlat(corners)), encoding="utf-8")
    return path

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
}


def submit(image: Path, boundary: Path | None = None, **fields):
    """Post an analysis exactly as a browser form would."""
    files = {"image": (image.name, image.read_bytes(), "image/tiff")}
    if boundary is not None:
        files["boundary"] = (boundary.name, boundary.read_bytes(), "application/vnd.google-earth.kml+xml")
    data = {key: str(value) for key, value in fields.items()}
    return client.post("/api/analyze", files=files, data=data)


def run_to_result(image: Path, boundary: Path | None = None, **fields) -> dict:
    """Submit, confirm the job completed, and return the full result body."""
    accepted = submit(image, boundary, **fields)
    assert accepted.status_code == 202, accepted.text
    job_id = accepted.json()["job_id"]

    status = await_job(client, job_id)
    assert status["status"] == "completed", status.get("error")

    response = client.get(f"/api/results/{job_id}")
    assert response.status_code == 200, response.text
    return response.json()


class TestSubmission:
    def test_a_submission_is_accepted_and_hands_back_somewhere_to_look(self, utm_raster):
        response = submit(utm_raster)

        assert response.status_code == 202
        body = response.json()
        assert len(body["job_id"]) == 32
        assert body["status_url"] == f"/api/jobs/{body['job_id']}"
        assert body["result_url"] == f"/api/results/{body['job_id']}"

    def test_the_receipt_reports_no_findings_of_its_own(self, utm_raster):
        # 202 means "accepted", not "analysed". Nothing countable may appear
        # here, because at this point nothing has been counted.
        body = submit(utm_raster).json()

        assert set(body) == {"job_id", "status", "status_url", "result_url"}

    def test_the_job_finishes_and_reports_the_work_it_did(self, utm_raster):
        job_id = submit(utm_raster).json()["job_id"]

        status = await_job(client, job_id)

        assert status["status"] == "completed"
        assert status["progress"] == 1.0
        assert status["error"] is None
        assert status["processing_time_seconds"] >= 0.0
        assert status["result_url"] == f"/api/results/{job_id}"

    def test_the_parameters_that_were_applied_are_echoed_back(self, utm_raster):
        job_id = submit(utm_raster, confidence_threshold=0.4, min_crown_area_m2=2.0).json()["job_id"]

        parameters = await_job(client, job_id)["parameters"]

        assert parameters["confidence_threshold"] == 0.4
        assert parameters["min_crown_area_m2"] == 2.0


class TestResultContent:
    @pytest.fixture(scope="module")
    def body(self, shared_raster) -> dict:
        return run_to_result(shared_raster)

    def test_every_promised_section_is_present(self, body):
        assert RESULT_KEYS <= set(body)

    def test_the_detections_are_the_trees_that_were_counted(self, body):
        assert len(body["detections"]) == body["summary"]["tree_count"]
        assert body["summary"]["tree_count"] > 0

    def test_each_detection_carries_what_the_inspector_needs(self, body):
        crown = body["detections"][0]

        assert crown["id"]
        assert 0.0 <= crown["confidence"] <= 1.0
        assert crown["area_m2"] > 0
        assert crown["centroid_lon"] is not None and crown["centroid_lat"] is not None
        assert crown["geometry"]["type"] == "Polygon"
        assert crown["confidence_kind"] in ("model", "heuristic")

    def test_coverage_is_crown_area_over_analysed_area(self, body):
        summary = body["summary"]
        expected = summary["total_canopy_area_m2"] / summary["analyzed_area_m2"] * 100.0

        # Loose only because the response rounds to two decimals; the
        # relationship itself is exact.
        assert summary["canopy_coverage_pct"] == pytest.approx(expected, abs=0.01)

    def test_the_image_metadata_is_read_from_the_file(self, body):
        metadata = body["image_metadata"]

        assert metadata["georeferenced"] is True
        assert metadata["width_px"] == 600 and metadata["height_px"] == 600
        assert metadata["source_crs"] == "EPSG:32617"
        assert metadata["pixel_area_m2"] == pytest.approx(0.0625, rel=1e-3)

    def test_the_result_names_the_file_the_user_uploaded(self, body):
        # The stored file is named by the server, because an upload's own name
        # is never used as a path. The result must still say "scene_utm.tif"
        # rather than the hex string it was saved under.
        assert body["image_metadata"]["filename"] == "scene_utm.tif"

    def test_limitations_travel_with_the_numbers(self, body):
        assert body["limitations"]["items"] == list(LIMITATIONS)
        assert "estimates" in body["limitations"]["notice"]

    def test_processing_time_separates_the_pipeline_from_the_wait(self, body):
        timing = body["processing_time"]

        assert timing["pipeline_seconds"] > 0
        assert timing["tiles_processed"] >= 1

    def test_no_boundary_means_no_boundary_block_rather_than_an_invented_one(self, body):
        assert body["boundary_metadata"] is None


class TestWithABoundary:
    @pytest.fixture(scope="module")
    def body(self, shared_raster, shared_left_half_kml) -> dict:
        return run_to_result(shared_raster, shared_left_half_kml)

    def test_the_boundary_is_reported_in_its_own_right(self, body):
        boundary = body["boundary_metadata"]

        assert boundary["source_format"] == "KML"
        assert boundary["part_count"] == 1
        assert boundary["area_m2"] > 0

    def test_the_boundary_geometry_comes_back_for_drawing(self, body):
        assert body["boundary_metadata"]["geometry"]["type"] in ("Polygon", "MultiPolygon")

    def test_the_three_areas_are_reported_separately(self, body):
        # This boundary sits wholly inside the imagery, so the boundary area
        # and the analysed area legitimately agree. What matters is that they
        # are separate fields and that coverage divides by the analysed one.
        summary = body["summary"]
        boundary = body["boundary_metadata"]

        assert summary["total_canopy_area_m2"] < summary["analyzed_area_m2"]
        assert boundary["imagery_covers_pct"] == pytest.approx(100.0, abs=0.5)
        assert summary["coverage_basis"] == "area"
        assert summary["canopy_coverage_pct"] == pytest.approx(
            summary["total_canopy_area_m2"] / summary["analyzed_area_m2"] * 100.0, abs=0.01
        )

    def test_a_boundary_wider_than_the_imagery_keeps_the_two_areas_apart(
        self, shared_raster, overhanging_kml
    ):
        # 300 m x 150 m outlined, 150 m x 150 m photographed. The boundary area
        # is the larger figure and must not be used as the coverage denominator.
        result = run_to_result(shared_raster, overhanging_kml)
        summary = result["summary"]
        boundary = result["boundary_metadata"]

        assert boundary["area_m2"] == pytest.approx(45_000.0, rel=0.01)
        assert summary["analyzed_area_m2"] == pytest.approx(22_500.0, rel=0.01)
        assert boundary["imagery_covers_pct"] == pytest.approx(50.0, abs=1.0)
        assert any("covers" in warning for warning in result["warnings"])

    def test_detections_outside_the_boundary_are_excluded_and_counted(self, body):
        assert body["summary"]["excluded_by_boundary"] > 0
        assert body["summary"]["boundary_applied"] is True

    def test_the_half_boundary_analyses_about_half_the_scene(self, body):
        # The scene is 600 x 600 at 0.25 m; the boundary covers its left half.
        summary = body["summary"]
        assert summary["analyzed_area_m2"] == pytest.approx(150.0 * 75.0, rel=0.02)


class TestDownloads:
    @pytest.fixture(scope="module")
    def job_id(self, shared_raster) -> str:
        job_id = submit(shared_raster).json()["job_id"]
        assert await_job(client, job_id)["status"] == "completed"
        return job_id

    def test_csv_arrives_as_a_named_download(self, job_id):
        response = client.get(f"/api/results/{job_id}/csv")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/csv")
        assert f'filename="crowns-{job_id}.csv"' in response.headers["content-disposition"]

    def test_the_csv_has_one_row_per_crown(self, job_id):
        body = client.get(f"/api/results/{job_id}/csv").text
        result = client.get(f"/api/results/{job_id}").json()

        # The export prepends commented provenance lines; pandas skips them
        # with comment='#', and so does this.
        table = "\n".join(line for line in body.splitlines() if not line.startswith("#"))
        rows = [row for row in csv.DictReader(io.StringIO(table)) if row.get("crown_id")]

        assert len(rows) == result["summary"]["tree_count"]

    def test_the_exports_name_the_uploaded_file_too(self, job_id):
        csv_body = client.get(f"/api/results/{job_id}/csv").text
        collection = json.loads(client.get(f"/api/results/{job_id}/geojson").text)

        assert "scene_utm.tif" in csv_body
        assert collection["canopylens"]["source_image"] == "scene_utm.tif"

    def test_geojson_arrives_as_a_feature_collection(self, job_id):
        response = client.get(f"/api/results/{job_id}/geojson")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/geo+json")
        collection = json.loads(response.text)
        assert collection["type"] == "FeatureCollection"
        assert len(collection["features"]) > 0


class TestParameterValidation:
    def test_a_confidence_above_one_is_refused_by_name(self, utm_raster):
        response = submit(utm_raster, confidence_threshold=1.5)

        assert response.status_code == 422
        error = response.json()["error"]
        assert error["code"] == "invalid_request"
        assert "confidence_threshold" in error["message"]

    def test_a_negative_minimum_area_is_refused(self, utm_raster):
        assert submit(utm_raster, min_crown_area_m2=-1).status_code == 422

    def test_an_absurd_minimum_area_is_refused_rather_than_returning_zero_trees(self, utm_raster):
        # Ten hectares is not a crown. Accepting it would filter out every
        # detection and report an honest, useless zero.
        assert submit(utm_raster, min_crown_area_m2=999_999).status_code == 422

    def test_an_unknown_engine_is_named_along_with_the_real_ones(self, utm_raster):
        response = submit(utm_raster, engine="magic")

        assert response.status_code == 422
        error = response.json()["error"]
        assert error["code"] == "invalid_parameter"
        assert "magic" in error["message"]
        assert "auto" in error["detail"]["available"]

    def test_defaults_apply_when_nothing_is_sent(self, utm_raster):
        settings = get_settings()
        job_id = submit(utm_raster).json()["job_id"]

        parameters = await_job(client, job_id)["parameters"]
        assert parameters["confidence_threshold"] == settings.default_confidence
        assert parameters["engine"] == settings.default_engine


class TestFileValidation:
    def test_an_unsupported_image_extension_is_refused(self, tmp_path):
        path = tmp_path / "notes.txt"
        path.write_bytes(b"not an image")

        response = client.post("/api/analyze", files={"image": (path.name, path.read_bytes())})

        assert response.status_code == 415
        assert response.json()["error"]["code"] == "unsupported_file"

    def test_an_unsupported_boundary_extension_is_refused(self, utm_raster, tmp_path):
        shapefile = tmp_path / "stand.shp"
        shapefile.write_bytes(b"\x00\x01")

        response = submit(utm_raster, shapefile)

        assert response.status_code == 415

    def test_a_missing_image_is_refused_before_any_job_exists(self):
        response = client.post("/api/analyze", data={"confidence_threshold": "0.3"})

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_request"

    def test_an_oversized_image_is_refused_while_it_is_still_arriving(self, utm_raster):
        app.dependency_overrides[get_settings] = lambda: Settings(max_upload_mb=0)
        try:
            response = submit(utm_raster)
        finally:
            app.dependency_overrides.pop(get_settings, None)

        assert response.status_code == 413
        assert response.json()["error"]["code"] == "payload_too_large"

    def test_a_rejected_boundary_does_not_strand_the_image_on_disk(self, utm_raster, tmp_path):
        settings = get_settings()
        shapefile = tmp_path / "stand.shp"
        shapefile.write_bytes(b"\x00")
        before = set(settings.upload_dir.glob("*"))

        assert submit(utm_raster, shapefile).status_code == 415

        assert set(settings.upload_dir.glob("*")) == before


class TestFailuresReachTheCaller:
    def test_an_unreadable_boundary_fails_the_job_with_the_parser_s_reason(
        self, utm_raster, tmp_path
    ):
        broken = tmp_path / "broken.kml"
        broken.write_text("<kml><unclosed>", encoding="utf-8")

        job_id = submit(utm_raster, broken).json()["job_id"]

        status = await_job(client, job_id)
        assert status["status"] == "failed"
        assert status["error"]["code"] == "invalid_boundary"

    def test_asking_for_a_failed_result_answers_with_the_original_error(
        self, utm_raster, tmp_path
    ):
        broken = tmp_path / "broken.kml"
        broken.write_text("<kml><unclosed>", encoding="utf-8")
        job_id = submit(utm_raster, broken).json()["job_id"]
        await_job(client, job_id)

        response = client.get(f"/api/results/{job_id}")

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_boundary"

    def test_a_boundary_on_ungeoreferenced_imagery_is_explained(self, plain_png, left_half_kml):
        job_id = submit(plain_png, left_half_kml).json()["job_id"]

        status = await_job(client, job_id)
        assert status["status"] == "failed"
        assert status["error"]["code"] == "missing_georeference"

    def test_ungeoreferenced_imagery_still_analyses_without_inventing_areas(self, plain_png):
        body = run_to_result(plain_png)

        assert body["image_metadata"]["georeferenced"] is False
        assert body["summary"]["total_canopy_area_m2"] is None
        assert body["summary"]["analyzed_area_m2"] is None
        # Coverage still exists, but as a ratio of pixels, and the basis says
        # so. Reporting it as an area figure would be the invented number.
        assert body["summary"]["coverage_basis"] == "pixel"
        assert body["summary"]["total_canopy_area_px"] > 0
        assert all(crown["area_m2"] is None for crown in body["detections"])
        assert all(crown["geometry_px"] is not None for crown in body["detections"])


class TestLookingUpJobs:
    def test_an_unknown_job_is_a_plain_not_found(self):
        response = client.get("/api/jobs/0123456789abcdef0123456789abcdef")

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"

    def test_a_traversal_attempt_is_just_a_key_that_is_not_there(self):
        response = client.get("/api/results/..%2F..%2Fetc%2Fpasswd")

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"

    def test_a_result_asked_for_too_early_says_so_rather_than_404(self):
        store = get_job_store()
        job = store.create(image_name="pending.tif", boundary_name=None, parameters={})

        response = client.get(f"/api/results/{job.id}")

        assert response.status_code == 409
        error = response.json()["error"]
        assert error["code"] == "job_not_finished"
        assert "/api/jobs/" in error["message"]


class TestNothingIsLeftOnDisk:
    def test_the_uploads_are_removed_once_the_analysis_has_read_them(self, utm_raster):
        settings = get_settings()
        before = set(settings.upload_dir.glob("*"))

        run_to_result(utm_raster)

        assert set(settings.upload_dir.glob("*")) == before


class TestErrorsAreUniform:
    def test_a_wrong_url_uses_the_same_envelope_as_everything_else(self):
        response = client.get("/api/nonsense")

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"

    def test_a_wrong_method_is_reported_in_the_same_shape(self):
        response = client.get("/api/analyze")

        assert response.status_code == 405
        assert "error" in response.json()

    def test_health_answers_at_the_root_as_well_as_under_api(self):
        assert client.get("/health").json() == client.get("/api/health").json()


class TestCorsIsRestricted:
    def test_the_configured_frontend_origin_is_allowed(self):
        response = client.options(
            "/api/analyze",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "POST",
            },
        )

        assert response.headers["access-control-allow-origin"] == "http://localhost:5173"

    def test_an_unlisted_origin_gets_no_permission(self):
        # The browser enforces this, not the server, but the server must not be
        # the one handing out the permission.
        response = client.options(
            "/api/analyze",
            headers={
                "Origin": "http://evil.example",
                "Access-Control-Request-Method": "POST",
            },
        )

        assert "access-control-allow-origin" not in response.headers


class TestConcurrencyBound:
    """Analyses queue behind a bounded pool rather than all starting at once."""

    def test_the_pool_is_sized_by_configuration(self):
        # _max_workers is the only view ThreadPoolExecutor offers of its own
        # bound, and the bound is the whole point of the pool being here.
        assert analysis_pool()._max_workers == get_settings().max_concurrent_analyses

    def test_more_submissions_than_workers_all_still_finish(self, shared_raster):
        workers = analysis_pool()._max_workers
        job_ids = [submit(shared_raster).json()["job_id"] for _ in range(workers + 1)]

        assert [await_job(client, job_id)["status"] for job_id in job_ids] == [
            "completed"
        ] * len(job_ids)
