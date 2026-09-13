"""Detection engines and the registry that reports on them.

The registry's job is to be honest about what this process can actually do, so
these tests check the reporting as carefully as the detecting.
"""

from __future__ import annotations

import numpy as np
import pytest
from canopylens_ml import (
    DetectionParams,
    EngineUnavailableError,
    available_engines,
    get_engine,
    registered_engines,
    resolve_engine,
)
from canopylens_ml.engines import PREFERENCE_ORDER
from canopylens_ml.engines.classical import ClassicalEngine
from canopylens_ml.refine import clamp_box, refine_box_to_polygon


def scene(height=400, width=400, circles=((100, 100, 30), (250, 120, 40), (150, 300, 25))):
    image = np.zeros((height, width, 3), np.uint8)
    image[..., 0], image[..., 1], image[..., 2] = 120, 110, 95
    rows, cols = np.mgrid[0:height, 0:width]
    for centre_row, centre_col, radius in circles:
        inside = (rows - centre_row) ** 2 + (cols - centre_col) ** 2 <= radius**2
        image[inside] = (45, 135, 40)
    return image


class TestRegistry:
    def test_both_engines_are_registered(self):
        assert set(registered_engines()) == {"classical", "deepforest"}

    def test_the_registry_loads_itself_on_first_use(self):
        # Registration is a side effect of importing the engines package;
        # a caller that imported a different module first must still see them.
        assert available_engines()

    def test_engines_are_cached_rather_than_rebuilt(self):
        assert get_engine("classical") is get_engine("classical")

    def test_an_unknown_name_raises_with_the_available_ones_listed(self):
        with pytest.raises(KeyError) as caught:
            get_engine("imaginary")
        assert "classical" in str(caught.value)

    def test_every_engine_reports_a_reason_when_unavailable(self):
        for info in available_engines():
            if not info.available:
                assert info.reason, f"{info.name} is unavailable but gives no reason"

    def test_availability_is_a_real_check_not_a_static_claim(self, monkeypatch):
        engine = ClassicalEngine()
        available, reason = engine.check()
        assert available and reason is None

        # Simulate the dependency going missing; the verdict must follow.
        import builtins

        real_import = builtins.__import__

        def refuse_cv2(name, *args, **kwargs):
            if name == "cv2":
                raise ImportError("simulated missing OpenCV")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", refuse_cv2)
        available, reason = engine.check()
        assert not available
        assert "opencv" in reason.lower()

    def test_preference_order_puts_the_trained_model_first(self):
        assert PREFERENCE_ORDER[0] == "deepforest"
        assert "classical" in PREFERENCE_ORDER

    def test_auto_returns_an_engine_that_can_actually_run(self):
        engine = resolve_engine("auto")
        assert engine.check()[0]

    def test_naming_an_unknown_engine_raises(self):
        with pytest.raises(EngineUnavailableError) as caught:
            resolve_engine("not-an-engine")
        assert "not-an-engine" in str(caught.value)

    def test_a_named_unavailable_engine_is_not_silently_substituted(self, monkeypatch):
        # Someone who asked for DeepForest and got colour thresholding would be
        # reading a different method's results under the name of the one they
        # chose.
        deepforest = get_engine("deepforest")
        monkeypatch.setattr(deepforest, "check", lambda: (False, "simulated absence"))
        with pytest.raises(EngineUnavailableError) as caught:
            resolve_engine("deepforest")
        assert "simulated absence" in str(caught.value)

    def test_the_classical_engine_declares_its_confidence_as_heuristic(self):
        info = get_engine("classical").info()
        assert info.confidence_kind == "heuristic"
        assert not info.requires_download

    def test_the_deepforest_engine_declares_a_model_confidence_and_a_download(self):
        info = get_engine("deepforest").info()
        assert info.confidence_kind == "model"
        assert info.requires_download


class TestClassicalDetection:
    @pytest.fixture(scope="class")
    def engine(self):
        return ClassicalEngine()

    @pytest.fixture
    def params(self):
        return DetectionParams(min_crown_area_px=50.0)

    def test_it_finds_the_crowns_that_were_drawn(self, engine, params):
        detections = engine.detect_tile(scene(), params)
        assert len(detections) == 3

    def test_measured_areas_match_the_circles(self, engine, params):
        import math

        detections = engine.detect_tile(scene(), params)
        measured = sorted(d.area_px for d in detections)
        expected = sorted(math.pi * r**2 for r in (30, 40, 25))
        for got, want in zip(measured, expected):
            assert got == pytest.approx(want, rel=0.10)

    def test_a_scene_with_no_vegetation_yields_nothing(self, engine, params):
        bare = np.zeros((200, 200, 3), np.uint8)
        bare[..., 0], bare[..., 1], bare[..., 2] = 130, 125, 150
        assert engine.detect_tile(bare, params) == []

    def test_touching_crowns_are_separated_by_the_watershed(self, engine, params):
        # Two overlapping circles: connected components would call this one
        # tree, which is the failure mode the watershed exists to fix.
        touching = scene(300, 300, circles=((150, 120, 40), (150, 180, 40)))
        assert len(engine.detect_tile(touching, params)) == 2

    def test_detections_below_the_minimum_area_are_not_returned(self, engine):
        detections = engine.detect_tile(scene(), DetectionParams(min_crown_area_px=4000.0))
        assert all(d.area_px >= 4000.0 for d in detections)
        assert len(detections) < 3

    def test_a_maximum_area_excludes_oversized_blobs(self, engine):
        params = DetectionParams(min_crown_area_px=50.0, max_crown_area_px=2500.0)
        assert all(d.area_px <= 2500.0 for d in engine.detect_tile(scene(), params))

    def test_confidence_is_bounded_and_its_parts_are_recorded(self, engine, params):
        for detection in engine.detect_tile(scene(), params):
            assert 0.0 <= detection.confidence <= 1.0
            assert detection.attributes["confidence_kind"] == "heuristic"
            # The components are kept so the score can be explained rather
            # than taken on trust.
            assert set(detection.attributes) >= {"mean_exg", "circularity", "solidity"}

    def test_round_green_shapes_outscore_ragged_grey_ones(self, engine, params):
        image = scene(300, 300, circles=((80, 80, 30),))
        # A dull, jagged patch: still above the vegetation floor, but a poor
        # match for a crown.
        image[200:260, 40:240] = (95, 115, 90)
        image[220:240, 60:100] = (120, 110, 95)

        detections = engine.detect_tile(image, params)
        by_circularity = sorted(detections, key=lambda d: d.attributes["circularity"])
        if len(by_circularity) >= 2:
            assert by_circularity[-1].confidence > by_circularity[0].confidence

    def test_coordinates_are_tile_local(self, engine, params):
        # The caller offsets them into raster space; an engine that returned
        # absolute coordinates would double-shift them.
        detections = engine.detect_tile(scene(200, 200, ((100, 100, 30),)), params)
        for detection in detections:
            for x, y in detection.polygon:
                assert 0 <= x <= 200 and 0 <= y <= 200

    def test_an_alpha_channel_excludes_invalid_pixels(self, engine, params):
        image = scene(300, 300, circles=((80, 80, 30), (220, 220, 30)))
        with_alpha = np.dstack([image, np.full(image.shape[:2], 255, np.uint8)])
        with_alpha[150:, :, 3] = 0  # bottom half marked as nodata

        detections = engine.detect_tile(with_alpha, params)
        assert len(detections) == 1
        assert detections[0].attributes["centroid_px"][1] < 150

    def test_results_are_deterministic(self, engine, params):
        first = engine.detect_tile(scene(), params)
        second = engine.detect_tile(scene(), params)
        assert [round(d.area_px, 6) for d in first] == [round(d.area_px, 6) for d in second]


class TestBoxRefinement:
    def test_a_box_is_clipped_to_the_image(self):
        assert clamp_box((-10.0, -10.0, 50.0, 50.0), 100, 100) == (0, 0, 50, 50)

    def test_a_box_entirely_outside_the_image_is_rejected(self):
        assert clamp_box((200.0, 200.0, 300.0, 300.0), 100, 100) is None

    def test_a_collapsed_box_is_rejected(self):
        assert clamp_box((10.0, 10.0, 10.5, 10.5), 100, 100) is None

    def test_a_box_round_a_crown_refines_to_the_crown(self):
        import math

        image = scene(200, 200, circles=((100, 100, 30),))
        ring, refined = refine_box_to_polygon(image, (70.0, 70.0, 130.0, 130.0))

        assert refined
        from canopylens_ml.geometry import polygon_area

        # The box is 3600 px2; the crown inside it is pi*900 = 2827.
        assert polygon_area(ring) == pytest.approx(math.pi * 900, rel=0.15)
        assert polygon_area(ring) < 3600

    def test_refinement_shrinks_the_box_rather_than_keeping_it(self):
        from canopylens_ml.geometry import polygon_area

        image = scene(200, 200, circles=((100, 100, 30),))
        ring, _ = refine_box_to_polygon(image, (60.0, 60.0, 140.0, 140.0))
        assert polygon_area(ring) < 80 * 80

    def test_a_box_with_no_vegetation_keeps_its_rectangle_and_says_so(self):
        # Falling back to the box is a visible overestimate; emitting a sliver
        # carved out of bare ground would be a quiet wrong answer.
        bare = np.zeros((200, 200, 3), np.uint8)
        bare[..., 0], bare[..., 1], bare[..., 2] = 120, 110, 95
        ring, refined = refine_box_to_polygon(bare, (50.0, 50.0, 100.0, 100.0))

        assert not refined
        from canopylens_ml.geometry import polygon_area

        assert polygon_area(ring) == pytest.approx(50 * 50)
