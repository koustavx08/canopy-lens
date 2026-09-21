"""Core data types exchanged between detection engines and the API."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class DetectionParams:
    """User-controllable detection settings.

    Areas are handled downstream in square metres; engines work in pixels and
    receive `min_crown_area_px` already converted by the caller, because only
    the caller knows the raster's ground sample distance.
    """

    confidence_threshold: float = 0.30
    """Advisory. Engines report what they found and the pipeline discards
    what scores below this, in one place, so that every engine is filtered on
    the same terms. An engine may use it to skip work it knows will be thrown
    away; it must not rely on being the only thing that applies it."""

    min_crown_area_px: float = 0.0
    max_crown_area_px: float | None = None
    tile_size: int = 800
    tile_overlap: int = 160
    iou_threshold: float = 0.35


@dataclass(slots=True)
class CrownDetection:
    """One detected tree crown, in *pixel* coordinates of the source raster.

    `polygon` is an exterior ring [(x, y), ...] in pixel space. Georeferencing
    to a CRS happens in the backend geo layer, never inside an engine.
    """

    polygon: list[tuple[float, float]]
    confidence: float
    bbox: tuple[float, float, float, float]  # (minx, miny, maxx, maxy) in pixels
    area_px: float
    engine: str
    attributes: dict = field(default_factory=dict)
