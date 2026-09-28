"""Base classes and types for instance segmentation engines."""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from ..base import DetectionEngine
from ..types import CrownDetection, DetectionParams


@dataclass(slots=True)
class SegmentedCrown(CrownDetection):
    """Crown detection with true instance segmentation mask or high-fidelity polygon."""

    mask: np.ndarray | None = None  # 2D boolean or uint8 mask within bounding box
    mask_origin: tuple[int, int] | None = None  # (col_offset, row_offset)
    segmentation_confidence: float | None = None
    segmentation_method: str = "instance_segmentation"


class SegmentationEngine(DetectionEngine, abc.ABC):
    """Abstract base class for models predicting pixel-level crown instance masks."""

    produces = "polygon"

    @abc.abstractmethod
    def segment_tile(
        self, image: np.ndarray, params: DetectionParams
    ) -> list[SegmentedCrown]:
        """Produce true instance segmented crowns with pixel masks and boundaries."""
        ...

    def detect_tile(
        self, image: np.ndarray, params: DetectionParams
    ) -> list[CrownDetection]:
        """Default detection delegation to segment_tile."""
        return list(self.segment_tile(image, params))
