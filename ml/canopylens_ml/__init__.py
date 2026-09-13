"""CanopyLens detection engines.

This package is deliberately independent of the web layer: it takes pixel
arrays in and returns crown detections out, so it can be used from the API,
from tests, or from a CLI script without a running server.
"""

from .availability import EngineAvailability, probe_dependencies
from .base import DetectionEngine, EngineInfo, EngineUnavailableError
from .registry import available_engines, get_engine, register_engine, registered_engines
from .geometry import (
    bounding_box,
    circularity,
    polygon_area,
    polygon_centroid,
    polygon_perimeter,
    translate,
)
from .types import CrownDetection, DetectionParams
from .vegetation import excess_green, vegetation_mask

__all__ = [
    "CrownDetection",
    "DetectionEngine",
    "DetectionParams",
    "EngineAvailability",
    "EngineInfo",
    "EngineUnavailableError",
    "available_engines",
    "bounding_box",
    "circularity",
    "excess_green",
    "get_engine",
    "polygon_area",
    "polygon_centroid",
    "polygon_perimeter",
    "probe_dependencies",
    "register_engine",
    "registered_engines",
    "resolve_engine",
    "translate",
    "vegetation_mask",
]


def resolve_engine(name: str = "auto") -> DetectionEngine:
    """Return a usable engine, or raise EngineUnavailableError explaining why.

    Importing here rather than at module scope keeps the registry import free
    of the engine modules themselves, which is what allows the factories to
    stay lazy.
    """
    from .engines import resolve_engine as _resolve

    return _resolve(name)

__version__ = "0.1.0"
