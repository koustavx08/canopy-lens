"""Topological repair and validation of tree crown polygons."""

from __future__ import annotations

import logging
from typing import Sequence

try:
    from shapely.geometry import MultiPolygon, Polygon
    from shapely.validation import make_valid
    _HAS_SHAPELY = True
except ImportError:
    _HAS_SHAPELY = False

from ..geometry import polygon_area

log = logging.getLogger(__name__)


def validate_and_repair_crown_polygon(
    coordinates: Sequence[tuple[float, float]],
    min_area_px: float = 1.0,
    tolerance_px: float = 0.5,
) -> tuple[list[tuple[float, float]] | None, bool]:
    """Validate a crown polygon, repair self-intersections, and prune spikes/slivers.

    Returns:
        (repaired_ring, was_modified)
    """
    if len(coordinates) < 3:
        return None, False

    ring = [(float(x), float(y)) for x, y in coordinates]

    if not _HAS_SHAPELY:
        # Fallback pure-python check
        area = polygon_area(ring)
        if area < min_area_px:
            return None, False
        return ring, False

    try:
        poly = Polygon(ring)
        was_modified = False

        if not poly.is_valid:
            poly = make_valid(poly)
            was_modified = True

        if isinstance(poly, MultiPolygon):
            # Pick largest polygon part
            polys = [p for p in poly.geoms if isinstance(p, Polygon)]
            if not polys:
                return None, True
            poly = max(polys, key=lambda p: p.area)
            was_modified = True

        if not isinstance(poly, Polygon) or poly.is_empty:
            return None, True

        # Simplify very noisy micro-edges while preserving general crown shape
        if tolerance_px > 0 and len(poly.exterior.coords) > 24:
            poly = poly.simplify(tolerance=tolerance_px, preserve_topology=True)
            was_modified = True

        if poly.area < min_area_px:
            return None, True

        coords = list(poly.exterior.coords)
        # Drop redundant closing point if present
        if len(coords) > 1 and coords[0] == coords[-1]:
            coords = coords[:-1]

        clean_ring = [(float(x), float(y)) for x, y in coords]
        return clean_ring, was_modified
    except Exception as exc:
        log.warning("Polygon repair encountered error: %s", exc)
        return None, True
