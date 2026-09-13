"""Choosing the coordinate system in which to measure.

Picking the projection is not a detail. Measuring a polygon in the wrong
projected CRS produces a number with the right units and the wrong value,
which is far harder to notice than an outright failure.
"""

from __future__ import annotations

import pytest
from pyproj import CRS
from shapely.geometry import box

from app.services import geo


class TestUtmZoneSelection:
    @pytest.mark.parametrize(
        ("longitude", "latitude", "expected_epsg"),
        [
            (-81.0, 42.0, 32617),  # Ontario -> zone 17 north
            (-122.4, 37.8, 32610),  # San Francisco -> zone 10 north
            (2.35, 48.85, 32631),  # Paris -> zone 31 north
            (151.2, -33.9, 32756),  # Sydney -> zone 56 south
            (-43.2, -22.9, 32723),  # Rio de Janeiro -> zone 23 south
            (-179.9, 0.5, 32601),  # first zone, just north of the equator
            (179.9, -0.5, 32760),  # last zone, just south of it
        ],
    )
    def test_picks_the_containing_zone(self, longitude, latitude, expected_epsg):
        assert geo.utm_crs_for(longitude, latitude).to_epsg() == expected_epsg

    def test_hemisphere_changes_the_zone_at_the_equator(self):
        north = geo.utm_crs_for(-81.0, 0.001).to_epsg()
        south = geo.utm_crs_for(-81.0, -0.001).to_epsg()
        assert north == 32617
        assert south == 32717

    @pytest.mark.parametrize(("latitude", "expected_epsg"), [(86.0, 32661), (-86.0, 32761)])
    def test_polar_latitudes_fall_back_to_polar_stereographic(self, latitude, expected_epsg):
        # UTM is undefined beyond 84 degrees; returning a zone there would be a
        # confident answer from outside the projection's domain.
        assert geo.utm_crs_for(10.0, latitude).to_epsg() == expected_epsg


class TestMetricCrsSelection:
    def test_a_metric_projected_source_is_kept_as_is(self):
        # Reprojecting a CRS that is already in metres would add resampling
        # error without adding any accuracy.
        utm = CRS.from_epsg(32617)
        geometry = box(500000, 4649626, 500150, 4649776)
        assert geo.metric_crs_for(geometry, utm) == utm

    def test_a_geographic_source_is_projected_to_utm(self):
        geometry = box(-81.001, 41.999, -80.999, 42.001)
        chosen = geo.metric_crs_for(geometry, geo.WGS84)
        assert chosen.to_epsg() == 32617
        assert geo.is_metric(chosen)

    def test_a_wide_extent_uses_an_equal_area_projection(self):
        # Wider than a UTM zone: a single zone would distort the edges badly,
        # and area is the quantity being preserved, so equal-area wins.
        geometry = box(-100.0, 30.0, -80.0, 45.0)
        chosen = geo.metric_crs_for(geometry, geo.WGS84)
        assert "laea" in chosen.to_proj4()
        assert geo.is_metric(chosen)

    def test_a_projected_source_in_feet_is_not_treated_as_metric(self):
        # NAD83 / Ohio North (ftUS). Its numbers look like a projected CRS but
        # its unit is not the metre, so measuring in it would report square
        # feet as square metres.
        feet = CRS.from_epsg(3734)
        assert not geo.is_metric(feet)

    def test_web_mercator_is_not_accepted_as_a_measurement_crs(self):
        # EPSG:3857 is projected and nominally metre-based, but it is
        # conformal rather than equal-area. is_metric() is about units; the
        # guard that matters is that nothing in the codebase selects 3857.
        mercator = CRS.from_epsg(3857)
        geometry = box(-81.001, 41.999, -80.999, 42.001)
        assert geo.metric_crs_for(geometry, geo.WGS84) != mercator


class TestReprojection:
    def test_transforming_to_the_same_crs_returns_the_geometry_unchanged(self):
        geometry = box(0, 0, 10, 10)
        assert geo.to_crs(geometry, geo.WGS84, geo.WGS84) is geometry

    def test_a_round_trip_returns_to_the_starting_coordinates(self):
        original = box(500000, 4649626, 500150, 4649776)
        utm = CRS.from_epsg(32617)
        there = geo.to_crs(original, utm, geo.WGS84)
        back = geo.to_crs(there, geo.WGS84, utm)
        assert back.equals_exact(original, tolerance=1e-6)

    def test_reprojection_actually_moves_the_coordinates(self):
        utm = CRS.from_epsg(32617)
        original = box(500000, 4649626, 500150, 4649776)
        projected = geo.to_crs(original, utm, geo.WGS84)
        # Degrees, not metres: longitude near -81, latitude near 42.
        assert -82 < projected.bounds[0] < -80
        assert 41 < projected.bounds[1] < 43
