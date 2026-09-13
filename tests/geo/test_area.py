"""Area measurement, and the mistakes it has to be immune to.

These use synthetic geometries of arithmetically known area, so an assertion
failure means the code is wrong rather than that the expectation drifted.
"""

from __future__ import annotations

import math

import pytest
from pyproj import CRS
from shapely.geometry import Polygon, box

from app.services import geo

UTM17N = CRS.from_epsg(32617)
ORIGIN_X, ORIGIN_Y = 500000.0, 4649776.0


class TestAreaInAProjectedCrs:
    def test_a_square_of_known_size_measures_exactly(self):
        # 150 m x 150 m in a metre-based CRS: 22 500 m2, no approximation.
        square = box(ORIGIN_X, ORIGIN_Y - 150, ORIGIN_X + 150, ORIGIN_Y)
        assert geo.area_m2(square, UTM17N, UTM17N) == pytest.approx(22_500.0)

    def test_a_circle_measures_to_pi_r_squared(self):
        circle = Polygon(
            [
                (ORIGIN_X + 10 * math.cos(t), ORIGIN_Y + 10 * math.sin(t))
                for t in [i * math.tau / 512 for i in range(512)]
            ]
        )
        # A 512-gon is very slightly smaller than the circle it inscribes.
        assert geo.area_m2(circle, UTM17N, UTM17N) == pytest.approx(math.pi * 100, rel=1e-4)


class TestDegreesAreNotMetres:
    """The single most dangerous error this application could make."""

    def test_area_from_a_geographic_geometry_is_reprojected_first(self):
        # A square 150 m on a side at latitude 42, expressed in degrees.
        latitude = 42.0
        metres_per_degree_lat = 111_132.0
        metres_per_degree_lon = 111_320.0 * math.cos(math.radians(latitude))
        d_lat = 150.0 / metres_per_degree_lat
        d_lon = 150.0 / metres_per_degree_lon

        square = box(-81.0, latitude - d_lat, -81.0 + d_lon, latitude)
        metric = geo.metric_crs_for(square, geo.WGS84)

        measured = geo.area_m2(square, geo.WGS84, metric)
        assert measured == pytest.approx(22_500.0, rel=0.01)

    def test_the_naive_answer_would_have_been_wrong_by_ten_orders_of_magnitude(self):
        square = box(-81.0, 41.999, -80.999, 42.0)
        naive = square.area  # square degrees, and not a unit of area at all
        honest = geo.area_m2(square, geo.WGS84, geo.metric_crs_for(square, geo.WGS84))

        assert naive < 1e-5
        assert honest > 1_000.0
        # Stated as a ratio so the failure message shows the scale of the trap.
        assert honest / naive > 1e9

    def test_the_same_ground_measures_the_same_from_either_crs(self):
        # A polygon defined in UTM and the same polygon expressed in WGS84 must
        # agree: the measurement must not depend on how the data arrived.
        in_utm = box(ORIGIN_X, ORIGIN_Y - 150, ORIGIN_X + 150, ORIGIN_Y)
        in_wgs84 = geo.to_crs(in_utm, UTM17N, geo.WGS84)

        from_utm = geo.area_m2(in_utm, UTM17N, UTM17N)
        from_wgs84 = geo.area_m2(in_wgs84, geo.WGS84, geo.metric_crs_for(in_wgs84, geo.WGS84))

        assert from_wgs84 == pytest.approx(from_utm, rel=1e-6)

    def test_latitude_changes_how_much_ground_a_degree_covers(self):
        # One degree square of longitude covers far less ground at 60 degrees
        # north than at the equator. Code that multiplied degrees by a constant
        # would report these as equal.
        equator = box(0.0, 0.0, 1.0, 0.001)
        high = box(0.0, 60.0, 1.0, 60.001)

        equator_area = geo.area_m2(equator, geo.WGS84, geo.metric_crs_for(equator, geo.WGS84))
        high_area = geo.area_m2(high, geo.WGS84, geo.metric_crs_for(high, geo.WGS84))

        assert equator.area == pytest.approx(high.area)  # identical in degrees
        assert high_area < equator_area * 0.6  # and very different on the ground


class TestPixelAreaFromATransform:
    def test_a_north_up_metric_transform_gives_the_squared_resolution(self):
        transform = (0.25, 0.0, ORIGIN_X, 0.0, -0.25, ORIGIN_Y)
        assert geo.pixel_area_m2(transform, UTM17N, UTM17N) == pytest.approx(0.0625)

    def test_a_rotated_transform_is_handled_by_projecting_the_pixel(self):
        # A 45-degree rotated transform with 0.25 m scale still covers
        # 0.0625 m2 per pixel. Multiplying transform[0] by transform[4] would
        # give a different, wrong answer here.
        angle = math.radians(45)
        scale = 0.25
        transform = (
            scale * math.cos(angle),
            -scale * math.sin(angle),
            ORIGIN_X,
            scale * math.sin(angle),
            scale * math.cos(angle),
            ORIGIN_Y,
        )
        # Trigonometry in the rotation costs a few parts per billion.
        assert geo.pixel_area_m2(transform, UTM17N, UTM17N) == pytest.approx(0.0625, rel=1e-6)

    def test_a_geographic_transform_gives_a_sensible_ground_size(self):
        # Degrees per pixel, at latitude 42. The answer must be in square
        # metres and must reflect the longitude compression at that latitude.
        degrees = 0.25 / 111_320.0
        transform = (degrees, 0.0, -81.0, 0.0, -degrees, 42.0)
        metric = geo.utm_crs_for(-81.0, 42.0)

        area = geo.pixel_area_m2(transform, geo.WGS84, metric)
        # Longitude is compressed by cos(42) ~ 0.743, latitude barely at all.
        expected = (0.25 * math.cos(math.radians(42))) * 0.25
        assert area == pytest.approx(expected, rel=0.02)


class TestGeometryRepair:
    def test_a_valid_polygon_is_returned_untouched(self):
        square = box(0, 0, 1, 1)
        assert geo.repair(square) is square

    def test_a_self_intersecting_ring_is_repaired(self):
        # A bow tie: the kind of ring a watershed contour produces where a
        # crown pinches. shapely reports it invalid.
        bow_tie = Polygon([(0, 0), (2, 2), (2, 0), (0, 2)])
        assert not bow_tie.is_valid

        repaired = geo.repair(bow_tie)
        assert repaired is not None
        assert repaired.is_valid
        assert repaired.area > 0

    def test_an_empty_geometry_repairs_to_nothing(self):
        assert geo.repair(Polygon()) is None

    def test_largest_polygon_keeps_the_main_body_of_a_pinched_crown(self):
        # make_valid turns a pinched ring into two polygons; a crown is one
        # object, so the larger survives and the sliver is discarded.
        pinched = Polygon([(0, 0), (10, 0), (10, 10), (0, 10), (5, 5), (0, 0)])
        largest = geo.largest_polygon(pinched)
        assert largest is not None
        assert largest.is_valid

    def test_all_polygons_keeps_every_part_of_a_multi_part_boundary(self):
        # Boundaries legitimately have several parts; dropping the smaller ones
        # would silently shrink the analysed region.
        from shapely.ops import unary_union

        two_fields = unary_union([box(0, 0, 1, 1), box(5, 5, 7, 7)])
        kept = geo.all_polygons(two_fields)
        assert kept is not None
        assert kept.geom_type == "MultiPolygon"
        assert kept.area == pytest.approx(1.0 + 4.0)

    def test_an_unmeasurable_geometry_contributes_zero_rather_than_raising(self):
        degenerate = Polygon([(0, 0), (1, 1), (2, 2)])  # zero-width sliver
        assert geo.area_m2(degenerate, UTM17N, UTM17N) == pytest.approx(0.0)


class TestPixelToCrs:
    def test_the_affine_transform_is_applied_to_the_ring(self):
        transform = (0.25, 0.0, ORIGIN_X, 0.0, -0.25, ORIGIN_Y)
        ring = [(0, 0), (4, 0), (4, 4), (0, 4)]
        polygon = geo.pixel_ring_to_crs(ring, transform)

        assert polygon.bounds == pytest.approx(
            (ORIGIN_X, ORIGIN_Y - 1.0, ORIGIN_X + 1.0, ORIGIN_Y)
        )
        assert polygon.area == pytest.approx(1.0)  # 4 px x 0.25 m = 1 m square

    def test_the_footprint_covers_the_whole_raster(self):
        transform = (0.25, 0.0, ORIGIN_X, 0.0, -0.25, ORIGIN_Y)
        footprint = geo.raster_footprint(600, 600, transform)
        assert footprint.area == pytest.approx(150.0 * 150.0)
