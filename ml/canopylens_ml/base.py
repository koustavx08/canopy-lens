"""Detection engine interface.

Every engine must be honest about its own availability: `check()` reports
whether the engine can actually run right now (dependencies importable,
weights present). The API surfaces that verdict to the user instead of
silently falling back to fabricated output.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass

import numpy as np

from .types import CrownDetection, DetectionParams


class EngineUnavailableError(RuntimeError):
    """Raised when no detection engine can actually run in this process.

    Deliberately a hard failure: with no engine there is no honest result to
    return, and returning nothing at all is better than returning something
    invented.
    """


@dataclass(slots=True)
class EngineInfo:
    name: str
    title: str
    description: str
    available: bool
    reason: str | None = None
    produces: str = "polygon"  # "polygon" | "bbox"
    requires_download: bool = False
    # How the confidence value should be read: "model" is a score produced by a
    # trained detector, "heuristic" is a rule-based plausibility score computed
    # from shape and colour. The client shows these differently on purpose — a
    # heuristic score must not be presented as a model probability.
    confidence_kind: str = "heuristic"


class DetectionEngine(abc.ABC):
    """Base class for tree crown detectors."""

    name: str = "engine"
    title: str = "Engine"
    description: str = ""
    produces: str = "polygon"
    requires_download: bool = False
    confidence_kind: str = "heuristic"

    @abc.abstractmethod
    def check(self) -> tuple[bool, str | None]:
        """Return (available, reason_if_unavailable)."""

    @abc.abstractmethod
    def detect_tile(self, image: np.ndarray, params: DetectionParams) -> list[CrownDetection]:
        """Detect crowns in a single RGB tile (H, W, 3) uint8.

        Coordinates returned are relative to the tile's own origin; the caller
        offsets them into full-raster pixel space.
        """

    def info(self) -> EngineInfo:
        available, reason = self.check()
        return EngineInfo(
            name=self.name,
            title=self.title,
            description=self.description,
            available=available,
            reason=reason,
            produces=self.produces,
            requires_download=self.requires_download,
            confidence_kind=self.confidence_kind,
        )
