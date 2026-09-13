"""Refining detector bounding boxes into crown polygons.

Box-based detectors such as DeepForest return an axis-aligned rectangle per
tree. A rectangle overestimates canopy area substantially - a circle inscribed
in its bounding square covers only pi/4, about 79%, of it - so reporting box
area as canopy area would inflate every coverage figure.

This module recovers the crown outline inside each box by thresholding the
vegetation index locally and taking the largest connected component. The box
supplies *where* the tree is, which is what the trained model is good at; the
index supplies *which pixels are foliage*, which is a straightforward colour
question.

When refinement fails - the box contains no vegetation above the floor, or the
resulting shape is implausibly small compared with the box - the caller is told
so and keeps the rectangle. Falling back to the box is a visible
overestimate; silently emitting a sliver would be a quiet wrong answer.
"""

from __future__ import annotations

import numpy as np

from .geometry import boxes_to_ring, polygon_area
from .vegetation import VEGETATION_FLOOR, excess_green, otsu_threshold

# A refined shape smaller than this fraction of the box is treated as a failed
# refinement rather than a very thin crown.
MIN_REFINED_FRACTION = 0.05


def clamp_box(
    box: tuple[float, float, float, float],
    width: int,
    height: int,
) -> tuple[int, int, int, int] | None:
    """Clip a float box to integer pixel bounds inside the image.

    Returns None when the box lies entirely outside the image or collapses to
    nothing after clipping.
    """
    minx, miny, maxx, maxy = box
    x0 = max(0, int(np.floor(min(minx, maxx))))
    y0 = max(0, int(np.floor(min(miny, maxy))))
    x1 = min(width, int(np.ceil(max(minx, maxx))))
    y1 = min(height, int(np.ceil(max(miny, maxy))))
    if x1 - x0 < 2 or y1 - y0 < 2:
        return None
    return x0, y0, x1, y1


def refine_box_to_polygon(
    image: np.ndarray,
    box: tuple[float, float, float, float],
    floor: float = VEGETATION_FLOOR,
) -> tuple[list[tuple[float, float]], bool]:
    """Return (ring in image pixel coordinates, refined).

    `refined` is False when the box was kept as-is, which the caller should
    record on the detection so the area can be reported as an upper bound.
    """
    import cv2

    height, width = image.shape[:2]
    clipped = clamp_box(box, width, height)
    if clipped is None:
        return boxes_to_ring(*box), False
    x0, y0, x1, y1 = clipped

    patch = image[y0:y1, x0:x1]
    box_ring = boxes_to_ring(float(x0), float(y0), float(x1), float(y1))
    box_area = polygon_area(box_ring)

    exg = excess_green(patch)
    # A local threshold: the box is known to contain a tree, so the only
    # question is which pixels inside it are canopy. Otsu splits the patch at
    # the valley between its foliage and background modes, which is exactly the
    # two-population case it is designed for. The patch mean was tried first
    # and consistently eroded crowns by around 15%, because in a tight box the
    # mean sits well inside the foliage mode rather than between the two.
    # The global floor still applies, so a box containing no vegetation at all
    # yields nothing rather than a shape carved out of bare ground.
    local = max(floor, otsu_threshold(exg))
    mask = (exg > local).astype(np.uint8)
    if not mask.any():
        return box_ring, False

    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return box_ring, False

    contour = max(contours, key=cv2.contourArea)
    perimeter = cv2.arcLength(contour, True)
    if perimeter > 0:
        contour = cv2.approxPolyDP(contour, 0.01 * perimeter, True)

    ring = [(float(p[0][0]) + x0, float(p[0][1]) + y0) for p in contour]
    if len(ring) < 3:
        return box_ring, False
    if box_area > 0 and polygon_area(ring) < MIN_REFINED_FRACTION * box_area:
        return box_ring, False
    return ring, True
