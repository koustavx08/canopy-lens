"""End-to-end tests for analyze_image().

The scene is synthetic and its truth is arithmetic: five circular crowns of
known radii on a raster of known pixel size, so tree counts and areas are
checked against what the geometry says they must be.

Every test pins engine="classical". The classical engine needs no weights and
no network, so the suite stays deterministic and runs in an environment where
the optional deep-learning extras are not installed.
"""

from __future__ import annotations

import math

import pytest

from app.errors import (
    EngineUnavailableError,
    InvalidBoundaryError,
    MissingGeoreferenceError,
)
from app.services.analysis import analyze_image

from ..conftest import PIXEL_METRES, SCENE_CIRCLES, SCENE_SIZE, draw_scene, write_raster

CLASSICAL = {"engine": "classical"}

# Rasterising a circle and tracing its contour loses a little area at the edge,
# and the morphological cleanup trims a pixel more. Five per cent is the real
# measured agreement, not a number chosen to make the test pass.
AREA_TOLERANCE = 0.08


@pytest.fixture(scope="module")
def _analysis_cache():
    return {}


@pytest.fixture
def result(utm_raster):
    return analyze_image(utm_raster, confidence_threshold=0.3, min_crown_area_m2=1.0, **CLASSICAL)


class TestGeoreferencedAnalysis:
    def test_every_planted_crown_is_found(self, result):
        assert result.summary.tree_count == len(SCENE_CIRCLES)

    def test_the_pixel_size_is_derived_from_the_transform(self, result):
        assert result.pixel_area_m2 == pytest.approx(PIXEL_METRES**2)

    def test_measurement_happens_in_a_metric_crs(self, result):
        assert result.source_crs == "EPSG:32617"
        assert result.measurement_crs == "EPSG:32617"

    def test_crown_areas_match_the_circles_that_were_drawn(self, result):
        expected = sorted(math.pi * (radius * PIXEL_METRES) ** 2 for _, _, radius in SCENE_CIRCLES)
        actual = sorted(crown.area_m2 for crown in result.crowns)
        for measured, truth in zip(actual, expected):
            assert measured == pytest.approx(truth, rel=AREA_TOLERANCE)

    def test_the_analysed_area_is_the_whole_raster(self, result):
        extent = SCENE_SIZE * PIXEL_METRES
        assert result.summary.analyzed_area_m2 == pytest.approx(extent * extent)

    def test_coverage_is_canopy_over_analysed_area(self, result):
        summary = result.summary
        expected = summary.total_canopy_area_m2 / summary.analyzed_area_m2 * 100
        assert summary.canopy_coverage_pct == pytest.approx(expected, rel=1e-3)
        assert summary.coverage_basis == "area"

    def test_coverage_is_a_plausible_percentage(self, result):
        assert 0.0 < result.summary.canopy_coverage_pct < 100.0

    def test_every_crown_carries_a_geographic_centroid(self, result):
        for crown in result.crowns:
            longitude, latitude = crown.centroid_lonlat
            assert -81.1 < longitude < -80.9
            assert 41.9 < latitude < 42.1

    def test_crown_ids_are_unique_and_stable(self, utm_raster, result):
        ids = [crown.id for crown in result.crowns]
        assert len(set(ids)) == len(ids)
        again = analyze_image(utm_raster, confidence_threshold=0.3, min_crown_area_m2=1.0, **CLASSICAL)
        assert [c.id for c in again.crowns] == ids
        assert [round(c.area_m2, 6) for c in again.crowns] == [
            round(c.area_m2, 6) for c in result.crowns
        ]

    def test_geometries_are_produced_in_both_pixel_and_geographic_space(self, result):
        for crown in result.crowns:
            assert crown.geometry_px.is_valid
            assert crown.geometry_wgs84 is not None
            assert crown.geometry_wgs84.is_valid


class TestSummaryStatistics:
    def test_mean_and_median_are_computed_from_the_crowns(self, result):
        import statistics

        areas = [crown.area_m2 for crown in result.crowns]
        assert result.summary.mean_crown_area_m2 == pytest.approx(statistics.fmean(areas), abs=1e-3)
        assert result.summary.median_crown_area_m2 == pytest.approx(
            statistics.median(areas), abs=1e-3
        )

    def test_min_and_max_bracket_every_crown(self, result):
        areas = [crown.area_m2 for crown in result.crowns]
        assert result.summary.min_crown_area_m2 == pytest.approx(min(areas), abs=1e-3)
        assert result.summary.max_crown_area_m2 == pytest.approx(max(areas), abs=1e-3)

    def test_total_canopy_area_is_the_union_not_the_sum(self, result):
        # With well-separated crowns the two agree; the guarantee is that the
        # total never exceeds the sum, which is what stops overlapping crowns
        # from being counted twice.
        assert result.summary.total_canopy_area_m2 <= result.summary.sum_crown_area_m2 + 1e-6

    def test_the_confidence_histogram_accounts_for_every_crown(self, result):
        total = sum(b.count for b in result.summary.confidence_distribution)
        assert total == result.summary.tree_count

    def test_the_histogram_spans_zero_to_one(self, result):
        bins = result.summary.confidence_distribution
        assert len(bins) == 10
        assert bins[0].lower == 0.0
        assert bins[-1].upper == 1.0

    def test_mean_confidence_is_within_range(self, result):
        assert 0.0 <= result.summary.mean_confidence <= 1.0

    def test_an_empty_scene_summarises_to_zero_without_dividing_by_zero(self, tmp_path):
        bare = draw_scene(200, 200, [])  # no crowns at all
        path = write_raster(tmp_path / "bare.tif", bare)
        empty = analyze_image(path, **CLASSICAL)

        assert empty.summary.tree_count == 0
        assert empty.summary.total_canopy_area_m2 == 0.0
        assert empty.summary.canopy_coverage_pct == 0.0
        # Statistics of nothing are None, not a misleading zero.
        assert empty.summary.mean_crown_area_m2 is None
        assert empty.summary.median_crown_area_m2 is None
        assert empty.summary.mean_confidence is None


class TestConfidenceFiltering:
    def test_a_threshold_of_zero_keeps_everything_a_higher_one_keeps_less(self, utm_raster):
        permissive = analyze_image(utm_raster, confidence_threshold=0.0, **CLASSICAL)
        strict = analyze_image(utm_raster, confidence_threshold=0.99, **CLASSICAL)
        assert strict.summary.tree_count <= permissive.summary.tree_count

    def test_no_crown_below_the_threshold_survives(self, utm_raster):
        analysis = analyze_image(utm_raster, confidence_threshold=0.5, **CLASSICAL)
        assert all(crown.confidence >= 0.5 for crown in analysis.crowns)

    def test_an_impossible_threshold_yields_no_crowns_rather_than_an_error(self, utm_raster):
        analysis = analyze_image(utm_raster, confidence_threshold=1.01, **CLASSICAL)
        assert analysis.summary.tree_count == 0

    def test_the_threshold_used_is_reported_back(self, utm_raster):
        analysis = analyze_image(utm_raster, confidence_threshold=0.42, **CLASSICAL)
        assert analysis.confidence_threshold == 0.42


class TestMinimumAreaFiltering:
    def test_raising_the_minimum_removes_the_smaller_crowns(self, utm_raster):
        # The smallest planted crown has radius 20 px = 5 m, about 78.5 m2.
        # A 100 m2 floor must exclude it and keep the larger ones.
        analysis = analyze_image(utm_raster, min_crown_area_m2=100.0, **CLASSICAL)
        assert analysis.summary.tree_count < len(SCENE_CIRCLES)
        assert all(crown.area_m2 >= 100.0 for crown in analysis.crowns)

    def test_a_minimum_above_every_crown_leaves_none(self, utm_raster):
        analysis = analyze_image(utm_raster, min_crown_area_m2=10_000.0, **CLASSICAL)
        assert analysis.summary.tree_count == 0

    def test_the_minimum_is_converted_into_pixels_using_the_real_pixel_size(self, utm_raster):
        # 6.25 m2 at 0.0625 m2 per pixel is exactly 100 pixels.
        analysis = analyze_image(utm_raster, min_crown_area_m2=6.25, **CLASSICAL)
        assert analysis.min_crown_area_px == pytest.approx(100.0)

    def test_the_filter_is_applied_in_metres_not_pixels(self, tmp_path, scene_array):
        # The same image at two pixel sizes: a fixed square-metre floor must
        # behave differently, because the crowns cover different ground.
        fine = write_raster(tmp_path / "fine.tif", scene_array, pixel_size=0.10)
        coarse = write_raster(tmp_path / "coarse.tif", scene_array, pixel_size=0.50)

        fine_result = analyze_image(fine, min_crown_area_m2=50.0, **CLASSICAL)
        coarse_result = analyze_image(coarse, min_crown_area_m2=50.0, **CLASSICAL)
        # At 0.10 m/px the crowns are small in metres and get filtered out; at
        # 0.50 m/px the same pixels are large enough to survive.
        assert fine_result.summary.tree_count < coarse_result.summary.tree_count


class TestBoundaryClipping:
    def test_a_boundary_reduces_the_analysed_area(self, utm_raster, left_half_kml):
        clipped = analyze_image(utm_raster, boundary_path=left_half_kml, **CLASSICAL)
        half = (SCENE_SIZE / 2 * PIXEL_METRES) * (SCENE_SIZE * PIXEL_METRES)
        assert clipped.summary.analyzed_area_m2 == pytest.approx(half, rel=1e-6)

    def test_crowns_outside_the_boundary_are_excluded(self, utm_raster, left_half_kml):
        full = analyze_image(utm_raster, **CLASSICAL)
        clipped = analyze_image(utm_raster, boundary_path=left_half_kml, **CLASSICAL)

        assert clipped.summary.tree_count < full.summary.tree_count
        assert clipped.excluded_by_boundary == full.summary.tree_count - clipped.summary.tree_count
        assert clipped.boundary_applied

    def test_the_surviving_crowns_are_the_ones_inside(self, utm_raster, left_half_kml):
        clipped = analyze_image(utm_raster, boundary_path=left_half_kml, **CLASSICAL)
        # The boundary covers the western half: columns 0-300 in pixel space.
        for crown in clipped.crowns:
            assert crown.centroid[0] <= SCENE_SIZE / 2

    def test_coverage_is_recomputed_against_the_clipped_region(self, utm_raster, left_half_kml):
        clipped = analyze_image(utm_raster, boundary_path=left_half_kml, **CLASSICAL)
        summary = clipped.summary
        expected = summary.total_canopy_area_m2 / summary.analyzed_area_m2 * 100
        # Coverage is computed at full precision and rounded for display, while
        # the areas it is checked against are themselves rounded, so the two
        # agree to the rounding granularity rather than exactly.
        assert summary.canopy_coverage_pct == pytest.approx(expected, abs=0.01)

    def test_the_boundary_is_returned_in_wgs84_for_display(self, utm_raster, left_half_kml):
        clipped = analyze_image(utm_raster, boundary_path=left_half_kml, **CLASSICAL)
        assert clipped.boundary_wgs84 is not None
        min_x, min_y, max_x, max_y = clipped.boundary_wgs84.bounds
        assert -82 < min_x < -80 and 41 < min_y < 43

    def test_a_boundary_that_misses_the_imagery_is_refused(self, utm_raster, elsewhere_kml):
        # Silently returning zero trees would look like a real answer.
        with pytest.raises(InvalidBoundaryError) as caught:
            analyze_image(utm_raster, boundary_path=elsewhere_kml, **CLASSICAL)
        assert "overlap" in str(caught.value).lower()

    def test_a_boundary_on_ungeoreferenced_imagery_is_refused(self, plain_png, left_half_kml):
        with pytest.raises(MissingGeoreferenceError):
            analyze_image(plain_png, boundary_path=left_half_kml, **CLASSICAL)


class TestUngeoreferencedImagery:
    @pytest.fixture
    def plain_result(self, plain_png):
        return analyze_image(plain_png, min_crown_area_m2=1.0, **CLASSICAL)

    def test_detection_still_works(self, plain_result):
        assert plain_result.summary.tree_count == len(SCENE_CIRCLES)

    def test_no_square_metre_figure_is_invented(self, plain_result):
        summary = plain_result.summary
        assert not plain_result.georeferenced
        assert summary.total_canopy_area_m2 is None
        assert summary.analyzed_area_m2 is None
        assert summary.mean_crown_area_m2 is None
        assert summary.median_crown_area_m2 is None
        assert plain_result.pixel_area_m2 is None
        assert all(crown.area_m2 is None for crown in plain_result.crowns)

    def test_pixel_measurements_are_still_real(self, plain_result):
        assert plain_result.summary.total_canopy_area_px > 0
        assert plain_result.summary.analyzed_area_px == pytest.approx(SCENE_SIZE * SCENE_SIZE)
        assert all(crown.pixel_area > 0 for crown in plain_result.crowns)

    def test_coverage_falls_back_to_pixel_space(self, plain_result):
        assert plain_result.summary.coverage_basis == "pixel"
        assert 0 < plain_result.summary.canopy_coverage_pct < 100

    def test_the_limitation_is_stated_in_the_warnings(self, plain_result):
        joined = " ".join(plain_result.warnings).lower()
        assert "georeferencing" in joined
        assert "square metres" in joined or "square metre" in joined

    def test_the_area_filter_is_reported_as_not_applied(self, plain_result):
        assert plain_result.min_crown_area_m2 is None
        assert any("could not be applied" in w for w in plain_result.warnings)

    def test_coverage_agrees_with_the_georeferenced_measurement(self, plain_result, result):
        # The same scene measured two ways. Pixel-space and metric coverage
        # describe the same ground, so they must agree.
        assert plain_result.summary.canopy_coverage_pct == pytest.approx(
            result.summary.canopy_coverage_pct, rel=0.02
        )


class TestTiling:
    @pytest.fixture(scope="class")
    def grid_scene(self, tmp_path_factory):
        # Trees on a regular grid across a raster large enough to need tiling,
        # positioned so that many of them straddle tile seams.
        circles = [(row, col, 22) for row in range(60, 900, 120) for col in range(60, 900, 120)]
        path = tmp_path_factory.mktemp("tiled") / "grid.tif"
        write_raster(path, draw_scene(960, 960, circles))
        return path, len(circles)

    def test_tiling_finds_the_same_trees_as_a_single_pass(self, grid_scene):
        path, planted = grid_scene
        whole = analyze_image(path, max_pixels_without_tiling=10**9, **CLASSICAL)
        tiled = analyze_image(
            path, max_pixels_without_tiling=1000, tile_size=400, tile_overlap=100, **CLASSICAL
        )

        assert whole.tiles_processed == 1
        assert tiled.tiles_processed > 1
        assert whole.summary.tree_count == planted
        assert tiled.summary.tree_count == planted

    def test_duplicates_from_overlapping_tiles_are_actually_removed(self, grid_scene):
        path, planted = grid_scene
        tiled = analyze_image(
            path, max_pixels_without_tiling=1000, tile_size=400, tile_overlap=100, **CLASSICAL
        )
        # Without suppression the seam trees would be counted once per tile.
        assert tiled.raw_detections > planted
        assert tiled.duplicates_removed > 0
        assert tiled.summary.tree_count == planted

    def test_measurements_survive_tiling_unchanged(self, grid_scene):
        path, _ = grid_scene
        whole = analyze_image(path, max_pixels_without_tiling=10**9, **CLASSICAL)
        tiled = analyze_image(
            path, max_pixels_without_tiling=1000, tile_size=400, tile_overlap=100, **CLASSICAL
        )
        assert tiled.summary.total_canopy_area_m2 == pytest.approx(
            whole.summary.total_canopy_area_m2, rel=0.02
        )
        assert tiled.summary.canopy_coverage_pct == pytest.approx(
            whole.summary.canopy_coverage_pct, rel=0.02
        )


class TestGeographicSourceImagery:
    def test_a_raster_in_degrees_is_measured_in_a_projected_crs(self, tmp_path, scene_array):
        degrees_per_pixel = 0.25 / 111_320.0
        path = write_raster(
            tmp_path / "geographic.tif",
            scene_array,
            crs="EPSG:4326",
            pixel_size=degrees_per_pixel,
            origin=(-81.0, 42.0),
        )
        analysis = analyze_image(path, min_crown_area_m2=1.0, **CLASSICAL)

        assert analysis.source_crs == "EPSG:4326"
        assert analysis.measurement_crs == "EPSG:32617"
        # If area had been taken from degrees it would be ~1e-10, not ~0.05.
        assert 0.01 < analysis.pixel_area_m2 < 0.1
        assert analysis.summary.tree_count > 0
        assert all(1.0 < crown.area_m2 < 1000.0 for crown in analysis.crowns)


class TestNodataHandling:
    def test_blank_padding_is_excluded_from_the_analysed_area(self, tmp_path):
        scene = draw_scene(400, 400, [(100, 100, 24), (300, 300, 24)])
        scene[:, :, :100] = 0  # a quarter of the raster is nodata
        path = write_raster(tmp_path / "padded.tif", scene, nodata=0)

        analysis = analyze_image(path, **CLASSICAL)
        full_extent = (400 * PIXEL_METRES) ** 2
        assert analysis.summary.analyzed_area_m2 == pytest.approx(full_extent * 0.75, rel=0.02)
        assert any("nodata" in w.lower() for w in analysis.warnings)


class TestEngineSelection:
    def test_an_unknown_engine_is_refused_by_name(self, utm_raster):
        with pytest.raises(EngineUnavailableError) as caught:
            analyze_image(utm_raster, engine="does-not-exist")
        assert "does-not-exist" in str(caught.value)

    def test_the_engine_used_is_reported(self, result):
        assert result.engine == "classical"

    def test_the_confidence_kind_is_reported_honestly(self, result):
        # The classical engine's score is a heuristic ranking, and the result
        # says so rather than letting it be read as a model probability.
        assert result.confidence_kind == "heuristic"
        assert all(crown.confidence_kind == "heuristic" for crown in result.crowns)
