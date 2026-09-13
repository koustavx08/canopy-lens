"""CanopyLens detection engines.

This package is deliberately independent of the web layer: it takes pixel
arrays in and returns crown detections out, so it can be used from the API,
from tests, or from a CLI script without a running server.
"""

from .availability import EngineAvailability, probe_dependencies
from .base import DetectionEngine, EngineInfo
from .registry import available_engines, get_engine, register_engine, registered_engines
from .types import CrownDetection, DetectionParams

__all__ = [
    "CrownDetection",
    "DetectionEngine",
    "DetectionParams",
    "EngineAvailability",
    "EngineInfo",
    "available_engines",
    "get_engine",
    "probe_dependencies",
    "register_engine",
    "registered_engines",
]

__version__ = "0.1.0"
