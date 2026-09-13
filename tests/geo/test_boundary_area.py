"""Measuring a boundary's area in square metres.

The boundary area is the first number a user sees after uploading a KML, and
it is the easiest one in the whole application to get silently wrong: shapely
will happily return `.area` for a polygon in degrees, the result looks like a
number, and nothing complains. These tests are written against arithmetic -
squares of known size in metres - so that a regression shows up as a wrong
answer rather than as a plausible one.
"""

from __future__ import annotations

import json

import pytest

from app.services.boundary import load_boundary, measure
from app.services.geo import WGS84, to_crs

from ..conftest import kml_polygon, utm_rect, utm_to_lonlat, write_utm_kml

UTM_EPSG = 32617


def geojson_in_utm(ring: list[tuple[float, float]], holes: list | None = None) -> str:
    """A GeoJSON polygon in UTM 17N, declared with the legacy `crs` member.

    Written in metres rather than degrees so that the expected area is exactly
    the rectangle's own arithmetic and does not depend on a projection at all.
    """
    rings = [[list(point) for point in [*ring, ring[0]]]]
    for hole in holes or []:
        rings.append([list(point) for point in [*hole, hole[0]]])
    return json.dumps(
        {
            "type": "Polygon",
            "coordinates": rings,
            "crs": {"type": "name", "properties": {"name": f"EPSG:{UTM_EPSG}"}},
        }
    )


class TestExactAreas:
    """Squares whose area is known before the code runs."""

    def test_a_hundred_metre_square_measures_ten_thousand_square_metres(self, tmp_path):
        path = tmp_path / "square.geojson"
        path.write_text(geojson_in_utm(utm_rect(500000, 4649000, 500100, 4649100)), "utf-8")
        assert measure(load_boundary(path)).area_m2 == pytest.approx(10_000.0, abs=0.01)

    def test_a_rectangle_measures_its_own_arithmetic(self, tmp_path):
        path = tmp_path / "rect.geojson"
        path.write_text(geojson_in_utm(utm_rect(500000, 4649000, 500075, 4649150)), "utf-8")
        assert measure(load_boundary(path)).area_m2 == pytest.approx(75 * 150, abs=0.01)

    def test_a_hole_is_subtracted_not_ignored(self, tmp_path):
        # A clearing inside a stand is not stand. Counting it would inflate the
        # boundary area and deflate every coverage figure computed against it.
        path = tmp_path / "holed.geojson"
        path.write_text(
            geojson_in_utm(
                utm_rect(500000, 4649000, 500100, 4649100),
                holes=[utm_rect(500020, 4649020, 500040, 4649040)],
            ),
            "utf-8",
        )
        measurement = measure(load_boundary(path))
        assert measurement.has_holes is True
        assert measurement.area_m2 == pytest.approx(10_000.0 - 400.0, abs=0.01)

    def test_separate_parts_are_summed(self, tmp_path):
        path = write_utm_kml(
            tmp_path / "two.kml",
            [
                utm_rect(500000, 4649000, 500100, 4649100),
                utm_rect(500300, 4649000, 500400, 4649100),
            ],
        )
        measurement = measure(load_boundary(path))
        assert measurement.part_count == 2
        assert measurement.area_m2 == pytest.approx(20_000.0, rel=1e-4)

    def test_a_kml_in_degrees_measures_the_same_ground_as_one_in_metres(self, tmp_path):
        """The round trip through WGS84 must not change how much ground there is."""
        corners = utm_rect(500000, 4649000, 500100, 4649100)

        metric_path = tmp_path / "metres.geojson"
        metric_path.write_text(geojson_in_utm(corners), "utf-8")

        degrees_path = tmp_path / "degrees.kml"
        degrees_path.write_text(kml_polygon(utm_to_lonlat(corners)), "utf-8")

        in_metres = measure(load_boundary(metric_path)).area_m2
        in_degrees = measure(load_boundary(degrees_path)).area_m2
        assert in_degrees == pytest.approx(in_metres, rel=1e-6)


class TestTheDegreesTrap:
    """The failure this module exists to prevent."""

    def test_the_naive_answer_is_wrong_by_orders_of_magnitude(self, tmp_path):
        # Taking `.area` of a WGS84 polygon returns square degrees. For a
        # hundred-metre square that is about 8e-7, against a true 10000 m2 -
        # wrong by ten orders of magnitude. Asserting the size of the mistake
        # means a regression reads as absurd rather than as merely off.
        corners = utm_to_lonlat(utm_rect(500000, 4649000, 500100, 4649100))
        path = tmp_path / "square.kml"
        path.write_text(kml_polygon(corners), "utf-8")
        loaded = load_boundary(path)

        measured = measure(loaded).area_m2
        naive = loaded.geometry.area  # square degrees, not square metres

        assert measured == pytest.approx(10_000.0, rel=1e-3)
        assert measured / naive > 1e9

    def test_the_same_degree_box_encloses_less_ground_further_north(self, tmp_path):
        # A degree of longitude shrinks with the cosine of latitude. Two boxes
        # identical in degrees are not identical in ground, and a tool that
        # measured in degrees could never tell them apart.
        def box_area(name: str, latitude: float) -> float:
            ring = [
                (0.0, latitude),
                (0.01, latitude),
                (0.01, latitude + 0.01),
                (0.0, latitude + 0.01),
            ]
            path = tmp_path / f"{name}.kml"
            path.write_text(kml_polygon(ring), "utf-8")
            return measure(load_boundary(path)).area_m2

        at_equator = box_area("equator", 0.0)
        at_sixty = box_area("sixty", 60.0)
        assert 0.45 < at_sixty / at_equator < 0.55

    def test_measurement_always_happens_in_a_projected_crs(self, tmp_path):
        path = tmp_path / "square.kml"
        path.write_text(kml_polygon(utm_to_lonlat(utm_rect(500000, 4649000, 500100, 4649100))), "utf-8")
        measurement = measure(load_boundary(path))
        assert measurement.measurement_crs == "EPSG:32617"
        assert measurement.measurement_crs != "EPSG:4326"


class TestProjectionChoice:
    def test_a_stand_sized_boundary_uses_its_utm_zone(self, tmp_path):
        path = tmp_path / "stand.kml"
        path.write_text(kml_polygon(utm_to_lonlat(utm_rect(500000, 4649000, 500100, 4649100))), "utf-8")
        assert measure(load_boundary(path)).measurement_crs == "EPSG:32617"

    def test_a_continental_boundary_uses_an_equal_area_projection(self, tmp_path):
        # Ten degrees is wider than any UTM zone. The exact area of a lat/lon
        # box is R^2 * dlon * (sin lat2 - sin lat1); for this one that is about
        # 1.235e12 m2, and an equal-area projection should land on it.
        path = tmp_path / "wide.kml"
        path.write_text(kml_polygon([(-5.0, -5.0), (5.0, -5.0), (5.0, 5.0), (-5.0, 5.0)]), "utf-8")
        measurement = measure(load_boundary(path))
        assert measurement.measurement_crs != "EPSG:4326"
        assert measurement.area_m2 == pytest.approx(1.2351e12, rel=0.02)

    def test_an_explicit_measurement_crs_is_used_when_given(self, tmp_path):
        # The analysis pipeline passes the CRS the imagery is measured in, so
        # boundary area and analysed area come from the same projection.
        from pyproj import CRS

        path = tmp_path / "stand.kml"
        path.write_text(kml_polygon(utm_to_lonlat(utm_rect(500000, 4649000, 500100, 4649100))), "utf-8")
        loaded = load_boundary(path)
        measurement = measure(loaded, CRS.from_epsg(32618))
        assert measurement.measurement_crs == "EPSG:32618"
        # A neighbouring zone still measures the same ground to within its own
        # distortion - it is a projection, not a different planet.
        assert measurement.area_m2 == pytest.approx(10_000.0, rel=0.01)


class TestReportedFigures:
    def test_bounds_and_centroid_describe_the_same_geometry(self, tmp_path):
        path = tmp_path / "stand.kml"
        corners = utm_rect(500000, 4649000, 500100, 4649100)
        path.write_text(kml_polygon(utm_to_lonlat(corners)), "utf-8")
        loaded = load_boundary(path)
        measurement = measure(loaded)

        in_wgs84 = to_crs(loaded.geometry, loaded.crs, WGS84)
        assert measurement.bounds_wgs84 == pytest.approx(in_wgs84.bounds, abs=1e-7)

    def test_the_source_format_is_carried_through(self, tmp_path):
        path = tmp_path / "stand.kml"
        path.write_text(kml_polygon(utm_to_lonlat(utm_rect(500000, 4649000, 500100, 4649100))), "utf-8")
        assert measure(load_boundary(path)).source_format == "KML"

    def test_every_reported_number_is_finite_and_positive(self, tmp_path):
        import math

        path = write_utm_kml(tmp_path / "stand.kml", [utm_rect(500000, 4649000, 500100, 4649100)])
        measurement = measure(load_boundary(path))
        assert math.isfinite(measurement.area_m2) and measurement.area_m2 > 0
        assert all(math.isfinite(v) for v in measurement.bounds_wgs84)
        assert all(math.isfinite(v) for v in measurement.centroid_wgs84)
