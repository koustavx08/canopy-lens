"""Advanced contour and morphological instance segmentation engine."""

from __future__ import annotations

import logging
import math
from typing import Any

import numpy as np

try:
    import cv2
    _HAS_CV2 = True
except ImportError:
    _HAS_CV2 = False

try:
    from scipy import ndimage as ndi
    from skimage.feature import peak_local_max
    from skimage.segmentation import watershed
    _HAS_SCIPY = True
except ImportError:
    _HAS_SCIPY = False

from ..base import EngineInfo
from ..geometry import bounding_box, circularity, polygon_area, polygon_centroid
from ..types import CrownDetection, DetectionParams
from ..vegetation import VEGETATION_FLOOR, excess_green
from .base import SegmentationEngine, SegmentedCrown
from .geometry_validator import validate_and_repair_crown_polygon

log = logging.getLogger(__name__)


class ActiveContourSegmentationEngine(SegmentationEngine):
    """Instance segmentation using marker-controlled morphological watershed and boundary contouring.

    Produces real instance masks and topological polygons without heavyweight external deep networks.
    """

    name = "active_contour_segmentation"
    title = "Morphological Instance Segmentation"
    description = (
        "Multi-scale morphological gradient and marker-controlled watershed "
        "yielding pixel-level instance masks and repaired crown boundary polygons."
    )
    confidence_kind = "heuristic"
    produces = "polygon"
    requires_download = False

    def check(self) -> tuple[bool, str]:
        if not _HAS_CV2:
            return False, "OpenCV (cv2) is required for active contour segmentation."
        if not _HAS_SCIPY:
            return False, "scipy and scikit-image are required for active contour segmentation."
        return True, "Morphological instance segmentation engine is runnable."

    def segment_tile(
        self, image: np.ndarray, params: DetectionParams
    ) -> list[SegmentedCrown]:
        image = np.asarray(image)
        if image.ndim != 3 or image.shape[2] < 3:
            return []

        rgb = image[:, :, :3]
        if rgb.dtype != np.uint8:
            rgb = np.clip(rgb, 0, 255).astype(np.uint8)

        height, width = rgb.shape[:2]
        exg = excess_green(rgb)

        # Alpha mask / nodata exclusion
        valid_mask = np.ones((height, width), dtype=bool)
        if image.ndim == 3 and image.shape[2] == 4:
            valid_mask = image[:, :, 3] > 0

        veg_mask = (exg >= VEGETATION_FLOOR) & valid_mask
        if not np.any(veg_mask):
            return []

        # Distance transform to separate connected crowns
        distance = ndi.distance_transform_edt(veg_mask)
        if distance.max() < 2.0:
            return []

        # Find markers (peaks of distance transform)
        min_distance = max(4, int(math.sqrt(params.min_crown_area_px / math.pi) * 0.4))
        coords = peak_local_max(distance, min_distance=min_distance, labels=veg_mask)

        if len(coords) == 0:
            return []

        markers = np.zeros(distance.shape, dtype=np.int32)
        for idx, (r, c) in enumerate(coords, start=1):
            markers[r, c] = idx

        markers = ndi.label(markers > 0)[0]
        labels = watershed(-distance, markers, mask=veg_mask)

        min_area = max(params.min_crown_area_px, 1.0)
        max_area = params.max_crown_area_px or (height * width)

        crowns: list[SegmentedCrown] = []

        for label_id in range(1, labels.max() + 1):
            region_mask = (labels == label_id).astype(np.uint8)
            pixel_count = int(np.sum(region_mask))
            if pixel_count < min_area or pixel_count > max_area:
                continue

            contours, _ = cv2.findContours(
                region_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
            )
            if not contours:
                continue

            largest_cnt = max(contours, key=cv2.contourArea)
            if len(largest_cnt) < 3:
                continue

            pts = [(float(pt[0][0]), float(pt[0][1])) for pt in largest_cnt]
            clean_pts, _ = validate_and_repair_crown_polygon(pts, min_area_px=min_area)
            if clean_pts is None or len(clean_pts) < 3:
                continue

            area = polygon_area(clean_pts)
            if area < min_area or area > max_area:
                continue

            # Bounding box of the region
            x, y, w, h = cv2.boundingRect(largest_cnt)
            local_mask = region_mask[y : y + h, x : x + w]

            # Heuristic confidence based on mean greenness, circularity, and solidity
            mean_green = float(np.mean(exg[region_mask > 0]))
            hull = cv2.convexHull(largest_cnt)
            hull_area = cv2.contourArea(hull)
            solidity = float(area / hull_area) if hull_area > 0 else 0.0
            circ = circularity(clean_pts)

            # Bounded plausibility score [0.0, 1.0]
            confidence = float(
                np.clip(
                    0.4 * min(1.0, mean_green / 0.3)
                    + 0.35 * circ
                    + 0.25 * min(1.0, solidity),
                    0.05,
                    0.99,
                )
            )

            bbox = (float(x), float(y), float(x + w), float(y + h))
            centroid = polygon_centroid(clean_pts)

            crowns.append(
                SegmentedCrown(
                    polygon=clean_pts,
                    confidence=confidence,
                    bbox=bbox,
                    area_px=area,
                    engine=self.name,
                    mask=local_mask,
                    mask_origin=(x, y),
                    segmentation_confidence=confidence,
                    segmentation_method="morphological_watershed",
                    attributes={
                        "centroid_px": centroid,
                        "confidence_kind": self.confidence_kind,
                        "circularity": round(circ, 3),
                        "solidity": round(solidity, 3),
                        "mean_exg": round(mean_green, 3),
                        "refined": True,
                    },
                )
            )

        return crowns
