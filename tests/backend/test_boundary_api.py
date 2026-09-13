"""The boundary upload endpoint.

`POST /api/boundary/preview` exists so a user can see where their KML landed
before committing to an analysis. These tests check two things: that the
numbers it returns are the same ones the service computes, and that every way
of getting it wrong comes back as an explanation rather than as a blank map.
"""

from __future__ import annotations

import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import create_app
from app.services.boundary import load_boundary, measure

from ..conftest import kml_document, kml_polygon, utm_rect, utm_to_lonlat

app = create_app()
client = TestClient(app)

STAND = utm_rect(500000, 4649000, 500100, 4649100)  # a 100 m square in UTM 17N
STAND_KML = kml_polygon(utm_to_lonlat(STAND))


def post(name: str, content: bytes | str, field: str = "file"):
    payload = content.encode("utf-8") if isinstance(content, str) else content
    return client.post("/api/boundary/preview", files={field: (name, io.BytesIO(payload))})


class TestSuccessfulPreview:
    @pytest.fixture(scope="class")
    def body(self) -> dict:
        response = post("stand.kml", STAND_KML)
        assert response.status_code == 200, response.text
        return response.json()

    def test_the_geometry_comes_back_ready_to_draw(self, body):
        assert body["geometry"]["type"] == "Polygon"
        for longitude, latitude in body["geometry"]["coordinates"][0]:
            assert -81.1 < longitude < -80.9
            assert 41.9 < latitude < 42.1

    def test_the_area_is_the_real_measured_one(self, body, tmp_path):
        # Not "a plausible number": the same value the service produces when
        # called directly, and the same value the square's arithmetic gives.
        path = tmp_path / "stand.kml"
        path.write_text(STAND_KML, encoding="utf-8")
        expected = measure(load_boundary(path)).area_m2

        assert body["area_m2"] == pytest.approx(expected, rel=1e-9)
        assert body["area_m2"] == pytest.approx(10_000.0, rel=1e-3)

    def test_hectares_are_the_same_measurement_in_another_unit(self, body):
        assert body["area_hectares"] == pytest.approx(body["area_m2"] / 10_000.0, abs=1e-4)

    def test_the_projection_used_for_the_area_is_named(self, body):
        # The client shows this so nobody has to take the area on trust.
        assert body["measurement_crs"] == "EPSG:32617"
        assert body["source_crs"] == "EPSG:4326"

    def test_the_bounds_can_frame_a_map_view(self, body):
        min_lon, min_lat, max_lon, max_lat = body["bounds_wgs84"]
        assert min_lon < max_lon and min_lat < max_lat
        centroid_lon, centroid_lat = body["centroid_wgs84"]
        assert min_lon <= centroid_lon <= max_lon
        assert min_lat <= centroid_lat <= max_lat

    def test_a_single_solid_stand_reports_itself_as_such(self, body):
        assert body["part_count"] == 1
        assert body["has_holes"] is False
        assert body["source_format"] == "KML"
        assert body["warnings"] == []


class TestOtherFormats:
    def test_a_kmz_is_unzipped_and_measured(self):
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as zipped:
            zipped.writestr("doc.kml", STAND_KML)
        response = post("stand.kmz", archive.getvalue())
        assert response.status_code == 200
        body = response.json()
        assert body["source_format"] == "KMZ"
        assert body["area_m2"] == pytest.approx(10_000.0, rel=1e-3)

    def test_a_geojson_is_accepted(self):
        ring = utm_to_lonlat(STAND)
        payload = {
            "type": "Feature",
            "properties": {},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[list(point) for point in [*ring, ring[0]]]],
            },
        }
        response = post("stand.geojson", json.dumps(payload))
        assert response.status_code == 200
        assert response.json()["source_format"] == "GeoJSON"

    def test_a_projected_geojson_is_reprojected_rather_than_misread(self):
        payload = {
            "type": "Polygon",
            "coordinates": [[list(point) for point in [*STAND, STAND[0]]]],
            "crs": {"type": "name", "properties": {"name": "EPSG:32617"}},
        }
        response = post("stand.geojson", json.dumps(payload))
        assert response.status_code == 200
        body = response.json()
        assert body["source_crs"] == "EPSG:32617"
        assert body["area_m2"] == pytest.approx(10_000.0, abs=0.01)
        # Read as degrees, an easting of 500000 would be nowhere on Earth.
        longitude, _ = body["centroid_wgs84"]
        assert -81.1 < longitude < -80.9

    def test_multiple_stands_are_reported_with_their_count_and_warning(self):
        document = kml_document(
            [
                (utm_to_lonlat(utm_rect(500000, 4649000, 500100, 4649100)), []),
                (utm_to_lonlat(utm_rect(500300, 4649000, 500400, 4649100)), []),
            ]
        )
        body = post("two.kml", document).json()
        assert body["part_count"] == 2
        assert body["geometry"]["type"] == "MultiPolygon"
        assert body["area_m2"] == pytest.approx(20_000.0, rel=1e-3)
        assert any("2 separate areas" in warning for warning in body["warnings"])

    def test_a_clearing_is_excluded_from_the_reported_area(self):
        document = kml_document(
            [
                (
                    utm_to_lonlat(utm_rect(500000, 4649000, 500100, 4649100)),
                    [utm_to_lonlat(utm_rect(500020, 4649020, 500040, 4649040))],
                )
            ]
        )
        body = post("holed.kml", document).json()
        assert body["has_holes"] is True
        assert body["area_m2"] == pytest.approx(10_000.0 - 400.0, rel=1e-3)


class TestRejections:
    """Every failure names what is wrong with the file, not just that it failed."""

    def test_malformed_xml_is_a_422_with_a_reason(self):
        response = post("broken.kml", "<kml><unclosed>")
        assert response.status_code == 422
        error = response.json()["error"]
        assert error["code"] == "invalid_boundary"
        assert "xml" in error["message"].lower()

    def test_a_kml_with_no_area_explains_what_to_draw_instead(self):
        response = post(
            "pins.kml",
            '<kml xmlns="http://www.opengis.net/kml/2.2"><Placemark><Point>'
            "<coordinates>-81,42</coordinates></Point></Placemark></kml>",
        )
        assert response.status_code == 422
        assert "polygon tool" in response.json()["error"]["message"].lower()

    def test_an_empty_upload_is_reported_as_empty(self):
        response = post("empty.kml", b"")
        assert response.status_code == 422
        assert "empty" in response.json()["error"]["message"].lower()

    def test_an_unsupported_extension_is_a_415(self):
        response = post("boundary.shp", b"\x00\x01")
        assert response.status_code == 415
        body = response.json()["error"]
        assert body["code"] == "unsupported_file"
        # The message has to say what *would* work.
        assert ".kml" in body["message"]

    def test_a_file_with_no_extension_is_refused_rather_than_guessed_at(self):
        response = post("boundary", STAND_KML)
        assert response.status_code == 415

    def test_a_corrupt_kmz_is_refused_with_advice(self):
        response = post("broken.kmz", b"this is not a zip archive")
        assert response.status_code == 422
        assert ".kml" in response.json()["error"]["message"]

    def test_a_missing_file_field_is_a_validation_error(self):
        response = client.post("/api/boundary/preview")
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_request"

    def test_an_oversized_upload_is_refused_while_it_is_still_arriving(self):
        # The limit is enforced on the bytes read, not on a Content-Length the
        # client asserted, so it is lowered here rather than sending 32 MB.
        app.dependency_overrides[get_settings] = lambda: Settings(max_boundary_upload_mb=0)
        try:
            response = post("stand.kml", STAND_KML)
        finally:
            app.dependency_overrides.pop(get_settings, None)
        assert response.status_code == 413
        assert response.json()["error"]["code"] == "payload_too_large"


class TestNoUploadsAreKept:
    def test_a_preview_leaves_nothing_behind(self):
        settings = get_settings()
        before = set(settings.upload_dir.glob("*")) if settings.upload_dir.exists() else set()
        assert post("stand.kml", STAND_KML).status_code == 200
        after = set(settings.upload_dir.glob("*")) if settings.upload_dir.exists() else set()
        assert after == before


class TestCapabilitiesAgree:
    def test_the_advertised_formats_are_the_ones_the_parser_accepts(self):
        from app.services.boundary import SUPPORTED_EXTENSIONS

        advertised = client.get("/api/capabilities").json()["supported_boundary_formats"]
        assert set(advertised) == SUPPORTED_EXTENSIONS

    def test_the_boundary_size_limit_is_published(self):
        limits = client.get("/api/capabilities").json()["limits"]
        assert limits["max_boundary_upload_mb"] == get_settings().max_boundary_upload_mb
