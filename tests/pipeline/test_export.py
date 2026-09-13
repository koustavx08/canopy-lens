"""CSV and GeoJSON export.

Exports leave the application and are read later, often by someone who was not
there when they were produced. These tests check that the files carry their own
context and that nothing unmeasurable is dressed up as a measurement.
"""

from __future__ import annotations

import csv
import io
import json

import pytest

from app.services.analysis import analyze_image
from app.services.export import CSV_COLUMNS, export_csv, export_geojson

CLASSICAL = {"engine": "classical"}


@pytest.fixture
def result(utm_raster):
    return analyze_image(utm_raster, confidence_threshold=0.3, min_crown_area_m2=1.0, **CLASSICAL)


@pytest.fixture
def plain_result(plain_png):
    return analyze_image(plain_png, **CLASSICAL)


def read_rows(text: str) -> list[dict]:
    """Parse exported CSV the way pandas would, skipping the comment header."""
    body = [line for line in text.splitlines() if not line.startswith("#")]
    return list(csv.DictReader(io.StringIO("\n".join(body))))


class TestCsvExport:
    def test_there_is_one_row_per_crown(self, result):
        rows = read_rows(export_csv(result))
        assert len(rows) == result.summary.tree_count

    def test_the_columns_are_the_declared_ones(self, result):
        rows = read_rows(export_csv(result))
        assert list(rows[0].keys()) == CSV_COLUMNS

    def test_column_names_state_their_units(self):
        # "area" alone would be ambiguous once the file is out of the app.
        assert "area_m2" in CSV_COLUMNS
        assert "area_pixels" in CSV_COLUMNS

    def test_values_match_the_crowns_they_came_from(self, result):
        rows = read_rows(export_csv(result))
        for row, crown in zip(rows, result.crowns):
            assert row["crown_id"] == crown.id
            assert float(row["area_m2"]) == pytest.approx(crown.area_m2, abs=0.01)
            assert float(row["confidence"]) == pytest.approx(crown.confidence, abs=1e-4)
            assert float(row["centroid_lon"]) == pytest.approx(crown.centroid_lonlat[0], abs=1e-6)

    def test_the_header_records_the_provenance(self, result):
        text = export_csv(result)
        header = "\n".join(line for line in text.splitlines() if line.startswith("#"))
        assert "CanopyLens" in header
        assert result.raster.path.name in header
        assert "classical" in header
        assert "EPSG:32617" in header

    def test_the_header_states_that_this_is_not_a_field_inventory(self, result):
        header = "\n".join(line for line in export_csv(result).splitlines() if line.startswith("#"))
        assert "not a field-verified tree inventory" in header

    def test_notes_can_be_omitted_for_machine_consumption(self, result):
        text = export_csv(result, include_header_notes=False)
        assert not text.startswith("#")
        assert text.splitlines()[0] == ",".join(CSV_COLUMNS)

    def test_unmeasurable_areas_are_blank_not_zero(self, plain_result):
        # A blank cell reads as "no value"; 0 would read as "a crown with no
        # area", which is a different and false claim.
        rows = read_rows(export_csv(plain_result))
        assert rows
        for row in rows:
            assert row["area_m2"] == ""
            assert row["centroid_lon"] == ""
            assert row["centroid_lat"] == ""
            assert float(row["area_pixels"]) > 0

    def test_the_header_explains_why_areas_are_missing(self, plain_result):
        header = "\n".join(line for line in export_csv(plain_result).splitlines() if line.startswith("#"))
        assert "NOT georeferenced" in header

    def test_an_empty_result_still_produces_a_valid_file_with_headers(self, tmp_path):
        from ..conftest import draw_scene, write_raster

        path = write_raster(tmp_path / "bare.tif", draw_scene(200, 200, []))
        empty = analyze_image(path, **CLASSICAL)
        rows = read_rows(export_csv(empty))
        assert rows == []
        assert ",".join(CSV_COLUMNS) in export_csv(empty)


class TestGeoJsonExport:
    def test_it_is_a_valid_feature_collection(self, result):
        payload = json.loads(export_geojson(result))
        assert payload["type"] == "FeatureCollection"
        assert len(payload["features"]) == result.summary.tree_count

    def test_features_carry_polygon_geometry_and_properties(self, result):
        payload = json.loads(export_geojson(result))
        feature = payload["features"][0]
        assert feature["type"] == "Feature"
        assert feature["geometry"]["type"] in {"Polygon", "MultiPolygon"}
        assert feature["properties"]["crown_id"].startswith("crown_")
        assert feature["properties"]["area_m2"] > 0

    def test_georeferenced_output_is_in_longitude_and_latitude(self, result):
        payload = json.loads(export_geojson(result))
        assert payload["canopylens"]["coordinate_space"] == "EPSG:4326"
        longitude, latitude = payload["features"][0]["geometry"]["coordinates"][0][0]
        assert -82 < longitude < -80
        assert 41 < latitude < 43

    def test_the_metadata_block_records_the_analysis(self, result):
        meta = json.loads(export_geojson(result))["canopylens"]
        assert meta["georeferenced"] is True
        assert meta["engine"] == "classical"
        assert meta["source_crs"] == "EPSG:32617"
        assert meta["tree_count"] == result.summary.tree_count
        assert meta["canopy_coverage_pct"] == result.summary.canopy_coverage_pct

    def test_the_metadata_carries_the_disclaimer(self, result):
        meta = json.loads(export_geojson(result))["canopylens"]
        assert "not a field-verified tree inventory" in meta["disclaimer"]

    def test_ungeoreferenced_output_does_not_claim_to_be_map_data(self, plain_result):
        # RFC 7946 says GeoJSON coordinates are longitude and latitude. Writing
        # pixel positions there without saying so would make the file silently
        # wrong in every GIS that opens it.
        payload = json.loads(export_geojson(plain_result))
        meta = payload["canopylens"]
        assert meta["georeferenced"] is False
        assert meta["coordinate_space"] == "pixel"
        assert "NOT longitude" in meta["warning"]

    def test_ungeoreferenced_coordinates_are_pixel_positions(self, plain_result):
        payload = json.loads(export_geojson(plain_result))
        for feature in payload["features"]:
            for x, y in feature["geometry"]["coordinates"][0]:
                assert 0 <= x <= 600
                assert 0 <= y <= 600
            assert feature["properties"]["area_m2"] is None

    def test_the_warnings_travel_with_the_file(self, plain_result):
        meta = json.loads(export_geojson(plain_result))["canopylens"]
        assert meta["warnings"]
        assert any("georeferencing" in w for w in meta["warnings"])

    def test_indentation_is_optional(self, result):
        compact = export_geojson(result)
        pretty = export_geojson(result, indent=2)
        assert len(pretty) > len(compact)
        assert json.loads(pretty)["features"] == json.loads(compact)["features"]
