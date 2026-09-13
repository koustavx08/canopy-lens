"""Removing duplicate detections from overlapping tiles.

Tiles overlap so that no crown is cut in half by a tile boundary. The cost of
that is duplication: a tree in the overlap strip is detected once by each
neighbouring tile. Without this step, a large raster would report roughly
`1 / (1 - overlap_fraction)` times too many trees along every seam - with the
default 20% overlap, a systematic overcount.

Two relationships count as duplication:

**Overlap.** Intersection over union above `iou_threshold`. This catches the
ordinary case of two tiles finding the same crown in slightly different places.

**Containment.** One polygon almost entirely inside another. IoU handles this
badly when the two differ a lot in size: a small crown wholly inside a large
merged blob can score an IoU of 0.2 while plainly being the same tree seen
twice. Containment is measured against the *smaller* polygon's area, so it is
sensitive to exactly that case.

The survivor of each cluster is the higher-confidence detection, which is why
the engines' confidence only ever needs to be a consistent ranking.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from shapely.geometry import Polygon
from shapely.strtree import STRtree

log = logging.getLogger(__name__)

DEFAULT_IOU_THRESHOLD = 0.35

# Fraction of the smaller polygon that must lie inside the larger before the
# two are treated as the same crown.
DEFAULT_CONTAINMENT_THRESHOLD = 0.70


@dataclass
class Candidate:
    """A detection competing for survival, in one consistent coordinate space."""

    polygon: Polygon
    confidence: float
    payload: object = None


def iou(a: Polygon, b: Polygon) -> float:
    """Intersection over union of two polygons. 0.0 when they do not touch."""
    if not a.intersects(b):
        return 0.0
    intersection = a.intersection(b).area
    if intersection <= 0.0:
        return 0.0
    union = a.area + b.area - intersection
    return float(intersection / union) if union > 0 else 0.0


def containment(a: Polygon, b: Polygon) -> float:
    """Fraction of the *smaller* polygon that lies inside the other."""
    if not a.intersects(b):
        return 0.0
    intersection = a.intersection(b).area
    smaller = min(a.area, b.area)
    return float(intersection / smaller) if smaller > 0 else 0.0


def suppress(
    candidates: list[Candidate],
    iou_threshold: float = DEFAULT_IOU_THRESHOLD,
    containment_threshold: float = DEFAULT_CONTAINMENT_THRESHOLD,
) -> list[Candidate]:
    """Non-maximum suppression over polygons.

    An R-tree narrows each comparison to candidates whose bounding boxes
    actually overlap, so cost grows with the number of genuine neighbours
    rather than with the square of the detection count. A 100-megapixel scene
    can produce tens of thousands of crowns, and the quadratic version of this
    function is the difference between two seconds and twenty minutes.
    """
    usable = [c for c in candidates if c.polygon is not None and not c.polygon.is_empty]
    if len(usable) <= 1:
        return list(usable)

    ordered = sorted(usable, key=lambda c: c.confidence, reverse=True)
    polygons = [c.polygon for c in ordered]
    tree = STRtree(polygons)

    suppressed = [False] * len(ordered)
    kept: list[Candidate] = []

    for index, candidate in enumerate(ordered):
        if suppressed[index]:
            continue
        kept.append(candidate)

        # Every later index is of equal or lower confidence, so the current
        # candidate always wins its cluster.
        for other_index in tree.query(candidate.polygon):
            other_index = int(other_index)
            if other_index <= index or suppressed[other_index]:
                continue
            other = polygons[other_index]
            if (
                iou(candidate.polygon, other) >= iou_threshold
                or containment(candidate.polygon, other) >= containment_threshold
            ):
                suppressed[other_index] = True

    removed = len(ordered) - len(kept)
    if removed:
        log.info("Deduplication removed %d overlapping detection(s)", removed)
    return kept
