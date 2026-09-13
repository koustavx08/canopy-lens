"""Pixel-space geometry helpers.

These deliberately use nothing but numpy. Engines work in raster pixel
coordinates and must stay installable without shapely, rasterio or pyproj; all
CRS-aware geometry lives in the backend geo layer.

Every function here treats a polygon as an exterior ring given as a sequence of
(x, y) pairs, where x is the column and y is the row of the raster. Rings do
not need to repeat the first point at the end.
"""

from __future__ import annotations

import math

import numpy as np

Ring = list[tuple[float, float]]


def _as_array(ring: Ring) -> np.ndarray:
    arr = np.asarray(ring, dtype=np.float64)
    if arr.ndim != 2 or arr.shape[1] != 2:
        raise ValueError("A ring must be a sequence of (x, y) pairs")
    # Drop an explicitly closed final vertex; the shoelace formula closes the
    # ring itself and a duplicate point would contribute a zero-length edge.
    if len(arr) > 1 and np.allclose(arr[0], arr[-1]):
        arr = arr[:-1]
    return arr


def polygon_area(ring: Ring) -> float:
    """Unsigned area of a simple polygon, in square pixels (shoelace formula)."""
    arr = _as_array(ring)
    if len(arr) < 3:
        return 0.0
    x, y = arr[:, 0], arr[:, 1]
    return float(abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))) / 2.0)


def polygon_perimeter(ring: Ring) -> float:
    """Closed perimeter length of a ring, in pixels."""
    arr = _as_array(ring)
    if len(arr) < 2:
        return 0.0
    diffs = np.roll(arr, -1, axis=0) - arr
    return float(np.hypot(diffs[:, 0], diffs[:, 1]).sum())


def polygon_centroid(ring: Ring) -> tuple[float, float]:
    """Area centroid of a ring, in pixel coordinates.

    Falls back to the mean of the vertices for degenerate (zero-area) rings,
    which is the only sensible answer when the area-weighted formula divides by
    zero.
    """
    arr = _as_array(ring)
    if len(arr) == 0:
        raise ValueError("Cannot take the centroid of an empty ring")
    if len(arr) < 3:
        return float(arr[:, 0].mean()), float(arr[:, 1].mean())

    x, y = arr[:, 0], arr[:, 1]
    x_next, y_next = np.roll(x, -1), np.roll(y, -1)
    cross = x * y_next - x_next * y
    signed_area = cross.sum() / 2.0
    if abs(signed_area) < 1e-12:
        return float(x.mean()), float(y.mean())
    cx = float(((x + x_next) * cross).sum() / (6.0 * signed_area))
    cy = float(((y + y_next) * cross).sum() / (6.0 * signed_area))
    return cx, cy


def bounding_box(ring: Ring) -> tuple[float, float, float, float]:
    """(minx, miny, maxx, maxy) in pixel coordinates."""
    arr = _as_array(ring)
    if len(arr) == 0:
        raise ValueError("Cannot take the bounds of an empty ring")
    return (
        float(arr[:, 0].min()),
        float(arr[:, 1].min()),
        float(arr[:, 0].max()),
        float(arr[:, 1].max()),
    )


def circularity(ring: Ring) -> float:
    """4*pi*area / perimeter^2 — 1.0 for a circle, lower for ragged shapes.

    Tree crowns viewed from above are roughly circular, so this is a useful
    plausibility signal. It is clamped to [0, 1] because pixelated rings can
    produce values slightly above 1.
    """
    area = polygon_area(ring)
    perimeter = polygon_perimeter(ring)
    if perimeter <= 0.0:
        return 0.0
    return float(min(1.0, 4.0 * math.pi * area / (perimeter * perimeter)))


def translate(ring: Ring, dx: float, dy: float) -> Ring:
    """Shift a ring, used to lift tile-local coordinates into raster space."""
    return [(x + dx, y + dy) for x, y in ring]


def boxes_to_ring(minx: float, miny: float, maxx: float, maxy: float) -> Ring:
    """Axis-aligned rectangle as a ring, for engines that only produce boxes."""
    return [(minx, miny), (maxx, miny), (maxx, maxy), (minx, maxy)]
