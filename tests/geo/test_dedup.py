"""Removing the duplicate detections that overlapping tiles create."""

from __future__ import annotations

import pytest
from shapely.geometry import Point, box

from app.services.dedup import Candidate, containment, iou, suppress


def circle(x: float, y: float, radius: float):
    """A synthetic crown: a circle of known radius at a known place."""
    return Point(x, y).buffer(radius, quad_segs=32)


class TestOverlapMeasures:
    def test_identical_polygons_have_an_iou_of_one(self):
        square = box(0, 0, 10, 10)
        assert iou(square, square) == pytest.approx(1.0)

    def test_disjoint_polygons_have_an_iou_of_zero(self):
        assert iou(box(0, 0, 1, 1), box(5, 5, 6, 6)) == 0.0

    def test_half_overlap_gives_the_expected_ratio(self):
        # Intersection 50, union 150.
        assert iou(box(0, 0, 10, 10), box(5, 0, 15, 10)) == pytest.approx(50 / 150)

    def test_containment_is_measured_against_the_smaller_polygon(self):
        # This is the case IoU handles badly: a small crown entirely inside a
        # large merged blob scores an IoU of 0.04 but is plainly the same tree.
        small = box(0, 0, 2, 2)
        large = box(0, 0, 10, 10)
        assert iou(small, large) == pytest.approx(4 / 100)
        assert containment(small, large) == pytest.approx(1.0)

    def test_containment_of_disjoint_polygons_is_zero(self):
        assert containment(box(0, 0, 1, 1), box(5, 5, 6, 6)) == 0.0


class TestSuppression:
    def test_distinct_crowns_all_survive(self):
        candidates = [
            Candidate(circle(0, 0, 5), 0.9),
            Candidate(circle(50, 0, 5), 0.8),
            Candidate(circle(0, 50, 5), 0.7),
        ]
        assert len(suppress(candidates)) == 3

    def test_a_duplicated_crown_collapses_to_one(self):
        # The same tree, found twice by two tiles, one pixel apart.
        candidates = [
            Candidate(circle(10, 10, 5), 0.6),
            Candidate(circle(10.5, 10.2, 5), 0.9),
        ]
        kept = suppress(candidates)
        assert len(kept) == 1

    def test_the_higher_confidence_detection_is_the_survivor(self):
        weak = Candidate(circle(10, 10, 5), 0.4, payload="weak")
        strong = Candidate(circle(10.3, 10.1, 5), 0.95, payload="strong")
        kept = suppress([weak, strong])
        assert [c.payload for c in kept] == ["strong"]

    def test_a_small_crown_inside_a_merged_blob_is_removed_by_containment(self):
        blob = Candidate(box(0, 0, 20, 20), 0.9, payload="blob")
        inside = Candidate(box(2, 2, 6, 6), 0.5, payload="inside")
        # IoU alone is 0.04 and would keep both.
        assert iou(blob.polygon, inside.polygon) < 0.35
        assert [c.payload for c in suppress([blob, inside])] == ["blob"]

    def test_touching_neighbours_are_both_kept(self):
        # Two crowns whose canopies brush: real trees, not duplicates.
        left = Candidate(circle(0, 0, 5), 0.9)
        right = Candidate(circle(9.5, 0, 5), 0.85)
        assert iou(left.polygon, right.polygon) < 0.35
        assert len(suppress([left, right])) == 2

    def test_the_threshold_is_respected(self):
        a = Candidate(box(0, 0, 10, 10), 0.9)
        b = Candidate(box(5, 0, 15, 10), 0.8)  # IoU exactly 1/3
        assert len(suppress([a, b], iou_threshold=0.30, containment_threshold=0.99)) == 1
        assert len(suppress([a, b], iou_threshold=0.40, containment_threshold=0.99)) == 2

    def test_suppression_is_transitive_within_a_cluster(self):
        # Three detections of one tree from three tiles: one survivor, not two.
        candidates = [
            Candidate(circle(10, 10, 5), 0.5),
            Candidate(circle(10.2, 10.1, 5), 0.9),
            Candidate(circle(9.8, 9.9, 5), 0.7),
        ]
        assert len(suppress(candidates)) == 1

    def test_an_empty_input_returns_empty(self):
        assert suppress([]) == []

    def test_a_single_candidate_is_returned_unchanged(self):
        only = Candidate(circle(0, 0, 5), 0.5)
        assert suppress([only]) == [only]

    def test_empty_geometries_are_dropped(self):
        from shapely.geometry import Polygon

        candidates = [Candidate(Polygon(), 0.9), Candidate(circle(0, 0, 5), 0.8)]
        assert len(suppress(candidates)) == 1

    def test_the_result_does_not_depend_on_input_order(self):
        made = [
            Candidate(circle(10, 10, 5), 0.5, payload="a"),
            Candidate(circle(10.2, 10.1, 5), 0.9, payload="b"),
            Candidate(circle(60, 60, 5), 0.7, payload="c"),
        ]
        forwards = {c.payload for c in suppress(made)}
        backwards = {c.payload for c in suppress(list(reversed(made)))}
        assert forwards == backwards == {"b", "c"}
