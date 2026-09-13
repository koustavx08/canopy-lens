"""The pixel-space primitives the engines are built on.

These are pure functions over synthetic shapes, so the expected values are
arithmetic rather than recorded output.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from canopylens_ml.geometry import (
    bounding_box,
    boxes_to_ring,
    circularity,
    polygon_area,
    polygon_centroid,
    polygon_perimeter,
    translate,
)
from canopylens_ml.vegetation import (
    VEGETATION_FLOOR,
    excess_green,
    otsu_threshold,
    vegetation_mask,
    vegetation_threshold,
)

UNIT_SQUARE = [(0, 0), (10, 0), (10, 10), (0, 10)]


def polygon_circle(cx: float, cy: float, radius: float, points: int = 256):
    return [
        (cx + radius * math.cos(i * math.tau / points), cy + radius * math.sin(i * math.tau / points))
        for i in range(points)
    ]


class TestPolygonArea:
    def test_a_square_measures_side_squared(self):
        assert polygon_area(UNIT_SQUARE) == pytest.approx(100.0)

    def test_a_triangle_measures_half_base_times_height(self):
        assert polygon_area([(0, 0), (10, 0), (0, 10)]) == pytest.approx(50.0)

    def test_a_circle_measures_pi_r_squared(self):
        assert polygon_area(polygon_circle(0, 0, 10)) == pytest.approx(math.pi * 100, rel=1e-3)

    def test_winding_direction_does_not_change_the_area(self):
        # Contour tracing can return either winding; area is unsigned.
        assert polygon_area(list(reversed(UNIT_SQUARE))) == pytest.approx(100.0)

    def test_an_explicitly_closed_ring_measures_the_same(self):
        assert polygon_area(UNIT_SQUARE + [(0, 0)]) == pytest.approx(100.0)

    def test_a_degenerate_ring_has_no_area(self):
        assert polygon_area([(0, 0), (1, 1)]) == 0.0
        assert polygon_area([]) == 0.0


class TestPerimeterAndCentroid:
    def test_a_square_has_perimeter_four_times_its_side(self):
        assert polygon_perimeter(UNIT_SQUARE) == pytest.approx(40.0)

    def test_a_circle_has_perimeter_two_pi_r(self):
        assert polygon_perimeter(polygon_circle(0, 0, 10)) == pytest.approx(math.tau * 10, rel=1e-3)

    def test_the_centroid_of_a_square_is_its_middle(self):
        assert polygon_centroid(UNIT_SQUARE) == pytest.approx((5.0, 5.0))

    def test_the_centroid_is_area_weighted_not_a_vertex_average(self):
        # An L shape: the mean of the vertices is not the centre of mass, so a
        # naive average would land in the wrong place.
        shape = [(0, 0), (6, 0), (6, 2), (2, 2), (2, 6), (0, 6)]
        cx, cy = polygon_centroid(shape)
        vertex_mean_x = sum(p[0] for p in shape) / len(shape)
        assert cx != pytest.approx(vertex_mean_x)
        assert 0 < cx < 6 and 0 < cy < 6

    def test_a_degenerate_ring_falls_back_to_the_vertex_mean(self):
        assert polygon_centroid([(0, 0), (4, 4)]) == pytest.approx((2.0, 2.0))

    def test_an_empty_ring_is_an_error_rather_than_a_silent_zero(self):
        with pytest.raises(ValueError):
            polygon_centroid([])


class TestShapeDescriptors:
    def test_a_circle_is_maximally_circular(self):
        assert circularity(polygon_circle(0, 0, 10)) == pytest.approx(1.0, rel=1e-3)

    def test_a_square_is_less_circular_than_a_circle(self):
        # 4*pi*A/P^2 for a square is pi/4.
        assert circularity(UNIT_SQUARE) == pytest.approx(math.pi / 4, rel=1e-6)

    def test_a_long_thin_sliver_is_barely_circular(self):
        assert circularity([(0, 0), (100, 0), (100, 1), (0, 1)]) < 0.1

    def test_circularity_is_clamped_to_one(self):
        assert circularity(polygon_circle(0, 0, 50, points=1024)) <= 1.0

    def test_a_zero_perimeter_ring_scores_zero(self):
        assert circularity([(0, 0), (0, 0), (0, 0)]) == 0.0

    def test_bounds_are_the_extremes_of_the_ring(self):
        assert bounding_box(UNIT_SQUARE) == (0.0, 0.0, 10.0, 10.0)

    def test_a_box_ring_round_trips_through_its_bounds(self):
        ring = boxes_to_ring(2.0, 3.0, 8.0, 11.0)
        assert bounding_box(ring) == (2.0, 3.0, 8.0, 11.0)
        assert polygon_area(ring) == pytest.approx(6.0 * 8.0)


class TestTranslate:
    def test_a_ring_shifts_by_the_tile_origin(self):
        # This is what lifts tile-local coordinates into full-raster space.
        assert translate([(0, 0), (1, 2)], 400, 800) == [(400, 800), (401, 802)]

    def test_translation_preserves_area(self):
        assert polygon_area(translate(UNIT_SQUARE, 1234, -567)) == pytest.approx(100.0)


class TestExcessGreen:
    def test_neutral_grey_scores_zero(self):
        grey = np.full((4, 4, 3), 128, np.uint8)
        assert excess_green(grey) == pytest.approx(0.0)

    def test_foliage_scores_clearly_positive(self):
        green = np.zeros((4, 4, 3), np.uint8)
        green[..., 0], green[..., 1], green[..., 2] = 45, 135, 40
        assert float(excess_green(green).mean()) > 0.3

    def test_bare_ground_scores_at_or_below_zero(self):
        soil = np.zeros((4, 4, 3), np.uint8)
        soil[..., 0], soil[..., 1], soil[..., 2] = 120, 110, 95
        assert float(excess_green(soil).mean()) < VEGETATION_FLOOR

    def test_brightness_barely_changes_the_index(self):
        # Chromatic coordinates are used precisely so that canopy in shadow
        # still reads as canopy.
        bright = np.zeros((4, 4, 3), np.uint8)
        bright[..., 0], bright[..., 1], bright[..., 2] = 90, 230, 80
        dim = (bright.astype(np.float32) * 0.4).astype(np.uint8)
        assert float(excess_green(bright).mean()) == pytest.approx(
            float(excess_green(dim).mean()), abs=0.05
        )

    def test_pure_black_does_not_divide_by_zero(self):
        black = np.zeros((4, 4, 3), np.uint8)
        values = excess_green(black)
        assert np.isfinite(values).all()
        assert values == pytest.approx(0.0)

    def test_uint16_imagery_gives_the_same_index_as_uint8(self):
        eight = np.zeros((4, 4, 3), np.uint8)
        eight[..., 0], eight[..., 1], eight[..., 2] = 40, 120, 40
        sixteen = eight.astype(np.uint16) * 257
        assert float(excess_green(eight).mean()) == pytest.approx(
            float(excess_green(sixteen).mean()), abs=1e-3
        )

    def test_a_non_rgb_array_is_rejected(self):
        with pytest.raises(ValueError):
            excess_green(np.zeros((4, 4), np.uint8))


class TestThresholding:
    def test_otsu_separates_two_populations(self):
        # The contract is separation, not a particular position in the gap:
        # thresholding at the returned value must put each population on its
        # own side.
        values = np.concatenate([np.full(500, 0.0), np.full(500, 1.0)])
        threshold = otsu_threshold(values)
        assert (values[:500] <= threshold).all()
        assert (values[500:] > threshold).all()

    def test_otsu_finds_the_valley_of_a_spread_bimodal_distribution(self):
        rng = np.random.default_rng(0)
        low = rng.normal(0.0, 0.05, 2000)
        high = rng.normal(1.0, 0.05, 2000)
        threshold = otsu_threshold(np.concatenate([low, high]))
        assert 0.2 < threshold < 0.8

    def test_a_uniform_array_does_not_produce_a_spurious_split(self):
        assert otsu_threshold(np.full(100, 0.3)) == pytest.approx(0.3)

    def test_the_floor_is_never_undercut(self):
        # A scene with nothing green in it must not have Otsu manufacture a
        # "greener half" out of bare ground.
        ground = np.full((50, 50), -0.2, np.float32)
        assert vegetation_threshold(ground) >= VEGETATION_FLOOR

    def test_a_predominantly_green_tile_keeps_the_floor(self):
        # Otsu here would split the canopy into bright and dark halves and
        # discard half the trees, so the floor is used instead.
        dense = np.full((50, 50), 0.6, np.float32)
        dense[:5] = -0.1
        assert vegetation_threshold(dense) == pytest.approx(VEGETATION_FLOOR)


class TestVegetationMask:
    def test_foliage_is_masked_and_ground_is_not(self):
        image = np.zeros((40, 40, 3), np.uint8)
        image[..., 0], image[..., 1], image[..., 2] = 120, 110, 95
        image[10:30, 10:30] = (45, 135, 40)

        mask, _ = vegetation_mask(image)
        assert mask[20, 20]
        assert not mask[2, 2]
        assert mask.sum() == pytest.approx(400, rel=0.1)

    def test_a_scene_with_no_vegetation_masks_nothing(self):
        soil = np.zeros((40, 40, 3), np.uint8)
        soil[..., 0], soil[..., 1], soil[..., 2] = 120, 110, 95
        mask, _ = vegetation_mask(soil)
        assert not mask.any()

    def test_invalid_pixels_are_excluded_from_the_mask(self):
        image = np.zeros((20, 20, 3), np.uint8)
        image[..., 0], image[..., 1], image[..., 2] = 45, 135, 40
        valid = np.ones((20, 20), bool)
        valid[:, :10] = False

        mask, _ = vegetation_mask(image, valid_mask=valid)
        assert not mask[:, :10].any()
        assert mask[:, 10:].all()
