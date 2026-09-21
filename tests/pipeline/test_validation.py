"""Scoring detections against ground truth.

The scenes here are squares on a grid, which is the point: the right answer is
arithmetic, so a wrong score is a bug in the scoring and not a disagreement
about what a tree looks like. Whether the *detector* is any good is a question
about real imagery and real truth, which this repository does not have and does
not pretend to.

Coordinates are small offsets near the equator, where a degree of longitude and
a degree of latitude are close enough to the same length that a square drawn in
degrees is still a square on the ground. That keeps the expected IoUs readable
without making the test depend on a projection.
"""

from __future__ import annotations

import json

import pytest
from shapely.geometry import box

from app.errors import InvalidBoundaryError
from app.services.validation import load_crowns, score

# About 11 m at the equator, so a crown is a plausible size rather than a
# continent - the metric CRS is chosen from the data's own position.
STEP = 0.0001


def crown(column: int, row: int, size: float = 1.0):
    """A square crown on a grid, `size` in units of STEP."""
    x, y = column * STEP * 3, row * STEP * 3
    return box(x, y, x + STEP * size, y + STEP * size)


class TestAPerfectRun:
    @pytest.fixture
    def report(self):
        crowns = [crown(0, 0), crown(1, 0), crown(2, 0)]
        return score(crowns, list(crowns))

    def test_everything_is_matched(self, report):
        assert report.matched == 3
        assert report.false_positives == 0
        assert report.false_negatives == 0

    def test_precision_and_recall_are_one(self, report):
        assert report.precision == 1.0
        assert report.recall == 1.0
        assert report.f1 == 1.0

    def test_the_areas_agree(self, report):
        assert report.area_ratio == pytest.approx(1.0, abs=1e-6)

    def test_it_says_where_it_measured(self, report):
        # IoU would survive being computed in degrees and area would not.
        assert report.measurement_crs.startswith("EPSG:")


class TestWhatWasMissed:
    def test_a_crown_with_no_detection_is_a_false_negative(self):
        truth = [crown(0, 0), crown(1, 0), crown(2, 0)]

        report = score(truth, [crown(0, 0)])

        assert report.matched == 1
        assert report.false_negatives == 2
        assert report.recall == pytest.approx(1 / 3)
        assert report.precision == 1.0

    def test_a_detection_with_no_crown_is_a_false_positive(self):
        report = score([crown(0, 0)], [crown(0, 0), crown(5, 5)])

        assert report.false_positives == 1
        assert report.precision == pytest.approx(0.5)
        assert report.recall == 1.0

    def test_the_indices_of_both_are_reported(self):
        # So a run can be looked at, not only scored: the interesting part of a
        # validation is usually which crowns were missed.
        report = score([crown(0, 0), crown(9, 9)], [crown(0, 0), crown(5, 5)])

        assert report.unmatched_truth == [1]
        assert report.unmatched_detections == [1]


class TestTheOverlapThreshold:
    def test_a_near_miss_below_the_threshold_does_not_count(self):
        truth = [box(0, 0, STEP, STEP)]
        # Shifted by 70% of its width: about 0.18 IoU, plainly the same tree to
        # a human and plainly not a match at 0.5.
        shifted = [box(STEP * 0.7, 0, STEP * 1.7, STEP)]

        assert score(truth, shifted).matched == 0

    def test_and_does_count_when_the_threshold_is_lowered_to_admit_it(self):
        truth = [box(0, 0, STEP, STEP)]
        shifted = [box(STEP * 0.7, 0, STEP * 1.7, STEP)]

        assert score(truth, shifted, iou_threshold=0.15).matched == 1

    def test_the_threshold_used_is_part_of_the_report(self):
        # A precision of 0.9 means nothing without it.
        assert score([crown(0, 0)], [crown(0, 0)], iou_threshold=0.3).iou_threshold == 0.3

    def test_a_threshold_outside_the_range_is_refused(self):
        for bad in (0.0, -0.1, 1.5):
            with pytest.raises(ValueError):
                score([crown(0, 0)], [crown(0, 0)], iou_threshold=bad)


class TestOneDetectionCannotCountTwice:
    def test_a_blob_over_two_crowns_is_credited_to_one_of_them(self):
        # The classical engine's characteristic failure in closed canopy: two
        # neighbouring trees merged into one detection. Counting it against
        # both would report a recall it has not earned, which is exactly what
        # a validation run exists to catch.
        left = box(0, 0, STEP, STEP)
        right = box(STEP * 1.02, 0, STEP * 2.02, STEP)
        blob = box(0, 0, STEP * 2.02, STEP)

        report = score([left, right], [blob], iou_threshold=0.4)

        assert report.matched <= 1
        assert report.false_negatives >= 1

    def test_two_detections_on_one_crown_leave_one_spurious(self):
        truth = [box(0, 0, STEP, STEP)]
        duplicates = [box(0, 0, STEP, STEP), box(0, 0, STEP * 0.99, STEP)]

        report = score(truth, duplicates)

        assert report.matched == 1
        assert report.false_positives == 1

    def test_the_better_overlap_wins_the_match(self):
        truth = [box(0, 0, STEP, STEP)]
        poor = box(STEP * 0.4, 0, STEP * 1.4, STEP)
        exact = box(0, 0, STEP, STEP)

        report = score(truth, [poor, exact], iou_threshold=0.3)

        assert report.matches[0].detection_index == 1


class TestAreaAgreement:
    def test_overclaiming_shows_as_a_ratio_above_one(self):
        truth = [box(0, 0, STEP, STEP)]
        generous = [box(0, 0, STEP * 1.2, STEP * 1.2)]

        assert score(truth, generous, iou_threshold=0.5).area_ratio > 1.0

    def test_area_is_the_ground_covered_and_not_the_sum_of_the_parts(self):
        # Two detections over one patch of ground cover one patch of ground.
        # Summing would report twice the canopy that is there.
        truth = [box(0, 0, STEP, STEP)]
        overlapping = [box(0, 0, STEP, STEP), box(0, 0, STEP, STEP)]

        assert score(truth, overlapping).area_ratio == pytest.approx(1.0, abs=1e-6)


class TestWhereThereIsNothingToCompare:
    def test_finding_nothing_in_an_empty_scene_is_not_a_perfect_score(self):
        # Arithmetically defensible and completely misleading.
        report = score([], [])

        assert report.precision is None
        assert report.recall is None
        assert report.f1 is None

    def test_no_detections_at_all_has_a_recall_but_no_precision(self):
        report = score([crown(0, 0)], [])

        assert report.recall == 0.0
        assert report.precision is None

    def test_no_truth_at_all_has_a_precision_but_no_recall(self):
        report = score([], [crown(0, 0)])

        assert report.precision == 0.0
        assert report.recall is None

    def test_f1_needs_both_halves(self):
        assert score([crown(0, 0)], []).f1 is None


class TestReadingCrownsFromAFile:
    def _write(self, path, payload):
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_a_feature_collection_gives_one_crown_per_feature(self, tmp_path):
        # Not one merged geometry: two truth crowns whose canopies touch would
        # otherwise become one, and the count everything rests on is wrong
        # before any scoring starts.
        payload = {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "properties": {}, "geometry": _square(0)},
                {"type": "Feature", "properties": {}, "geometry": _square(1)},
            ],
        }
        crowns, _ = load_crowns(self._write(tmp_path / "truth.geojson", payload))

        assert len(crowns) == 2

    def test_touching_crowns_stay_separate(self, tmp_path):
        payload = {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "properties": {}, "geometry": _square(0)},
                {"type": "Feature", "properties": {}, "geometry": _square(0, offset=STEP)},
            ],
        }
        crowns, _ = load_crowns(self._write(tmp_path / "touching.geojson", payload))

        assert len(crowns) == 2

    def test_a_bare_geometry_is_accepted_too(self, tmp_path):
        crowns, _ = load_crowns(self._write(tmp_path / "one.geojson", _square(0)))

        assert len(crowns) == 1

    def test_a_declared_projected_crs_is_honoured(self, tmp_path):
        # QGIS still writes the GeoJSON 2008 `crs` member. Reading an easting
        # of 500000 as a longitude is a silent, enormous error.
        payload = {
            "type": "FeatureCollection",
            "crs": {"type": "name", "properties": {"name": "EPSG:32617"}},
            "features": [{"type": "Feature", "properties": {}, "geometry": _square(0)}],
        }
        _, crs = load_crowns(self._write(tmp_path / "utm.geojson", payload))

        assert crs.to_epsg() == 32617

    def test_a_file_with_no_polygons_says_so(self, tmp_path):
        payload = {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "properties": {}, "geometry": {
                    "type": "Point", "coordinates": [0, 0]
                }}
            ],
        }
        with pytest.raises(InvalidBoundaryError):
            load_crowns(self._write(tmp_path / "points.geojson", payload))

    def test_a_file_that_is_not_geojson_at_all_says_so(self, tmp_path):
        path = tmp_path / "broken.geojson"
        path.write_text("this is not JSON", encoding="utf-8")

        with pytest.raises(InvalidBoundaryError):
            load_crowns(path)


def _square(column: int, offset: float = 0.0) -> dict:
    x = column * STEP * 3 + offset
    return {
        "type": "Polygon",
        "coordinates": [[[x, 0], [x + STEP, 0], [x + STEP, STEP], [x, STEP], [x, 0]]],
    }
