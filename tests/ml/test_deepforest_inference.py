"""The DeepForest engine actually running a model.

Every other test of this engine checks how it *reports* itself - that it is
registered, that it is preferred, that it explains its own absence. None of
them make it predict anything, so the path the deployed API takes when the
extras are installed was never executed by the suite.

These do, and they are marked `slow` and excluded by default: the weights are
fetched from Hugging Face on first use and a forward pass on CPU takes the
better part of a minute. Run them with `pytest -m slow`, which CI does in a job
of its own.

What is asserted is deliberately not a tree count. This scene is circles drawn
on flat ground, and DeepForest was trained on NEON airborne imagery of real
forest - its accuracy here is unknown rather than merely lower, which is the
domain-shift limitation the application states in its own interface. Pinning a
number would be pinning that unknown. What must hold is that the model loads,
runs, and returns detections shaped the way the rest of the pipeline expects.
"""

from __future__ import annotations

import numpy as np
import pytest
from canopylens_ml import DetectionParams, get_engine

from ..conftest import SCENE_CIRCLES, SCENE_SIZE, draw_scene

pytestmark = pytest.mark.slow

AVAILABLE, REASON = get_engine("deepforest").check()
needs_deepforest = pytest.mark.skipif(
    not AVAILABLE, reason=f"deep-learning extras not installed: {REASON}"
)


@pytest.fixture(scope="module")
def tile() -> np.ndarray:
    """The shared scene as an (H, W, 3) tile, which is what an engine is given."""
    scene = draw_scene(SCENE_SIZE, SCENE_SIZE, SCENE_CIRCLES)
    return np.transpose(scene, (1, 2, 0))


@pytest.fixture(scope="module")
def detections(tile):
    """One forward pass, shared: loading the model is the expensive part."""
    return get_engine("deepforest").detect_tile(
        tile, DetectionParams(confidence_threshold=0.1)
    )


@needs_deepforest
class TestAForwardPass:
    def test_the_model_loads_and_predicts(self, detections):
        # The claim is that the engine runs, not that it is accurate here.
        assert len(detections) >= 1

    def test_every_detection_carries_a_model_confidence(self, detections):
        for detection in detections:
            assert 0.0 <= detection.confidence <= 1.0

    def test_detections_stay_inside_the_tile_they_came_from(self, detections):
        # They are translated into raster coordinates by their tile's offset,
        # so a polygon outside the tile becomes a crown in the wrong place.
        for detection in detections:
            xs = [point[0] for point in detection.polygon]
            ys = [point[1] for point in detection.polygon]
            assert min(xs) >= -1 and max(xs) <= SCENE_SIZE + 1
            assert min(ys) >= -1 and max(ys) <= SCENE_SIZE + 1

    def test_a_box_that_was_refined_says_so_and_one_that_was_not_says_that(
        self, detections
    ):
        # Reporting box area as canopy area overstates it by roughly a quarter.
        # Whether the refinement worked has to be visible rather than assumed.
        for detection in detections:
            assert isinstance(detection.attributes.get("refined"), bool)

    def test_a_polygon_has_enough_points_to_have_an_area(self, detections):
        for detection in detections:
            assert len(detection.polygon) >= 4


@needs_deepforest
class TestThroughTheWholePipeline:
    @pytest.fixture(scope="class")
    def result(self, tmp_path_factory):
        from app.services.analysis import analyze_image

        from ..conftest import write_raster

        scene = draw_scene(SCENE_SIZE, SCENE_SIZE, SCENE_CIRCLES)
        raster = write_raster(tmp_path_factory.mktemp("deepforest") / "scene.tif", scene)
        return analyze_image(raster, confidence_threshold=0.1, engine="deepforest")

    def test_the_result_names_the_engine_that_produced_it(self, result):
        assert result.engine == "deepforest"
        assert all(crown.engine == "deepforest" for crown in result.crowns)

    def test_the_confidences_are_labelled_as_the_model_s_own(self, result):
        # The classical engine's scores are a heuristic and say so. These are
        # not, and a figure that cannot say which kind it is is a figure a
        # reader will assume is the better one.
        assert all(crown.confidence_kind == "model" for crown in result.crowns)

    def test_the_confidence_threshold_reaches_the_detections(self, result):
        # The engines report everything they found; the pipeline applies the
        # threshold in one place so that both are filtered on the same terms.
        # This is the end of that path, which is where it has to be true.
        assert all(crown.confidence >= 0.1 for crown in result.crowns)

    def test_the_crowns_are_measured_in_square_metres(self, result):
        assert result.summary.total_canopy_area_m2 is not None
        assert all(crown.area_m2 is not None and crown.area_m2 > 0 for crown in result.crowns)

    def test_the_canopy_area_still_never_exceeds_the_sum_of_the_crowns(self, result):
        # The same invariant the classical engine is held to. DeepForest boxes
        # overlap far more often, so this is where double counting would show.
        assert result.summary.total_canopy_area_m2 <= result.summary.sum_crown_area_m2
