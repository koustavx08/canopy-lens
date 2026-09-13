"""Classical computer-vision crown segmentation.

Pipeline, per tile:

    RGB -> Excess Green index -> vegetation mask -> morphological cleanup
        -> distance transform -> local maxima as seeds -> watershed
        -> per-region contour -> polygon + heuristic confidence

This engine has no trained weights, so it runs anywhere the core requirements
are installed. That makes it the dependable baseline: the application always
has a real detection path, and never has to choose between a missing model and
inventing output.

Its confidence is explicitly a *heuristic plausibility score*, not a model
probability. It combines three measurable properties of a candidate region:

    greenness    how strongly the region reads as vegetation (mean ExG)
    circularity  4*pi*A/P^2 - crowns are roughly round seen from above
    solidity     region area / convex hull area, penalising ragged blobs

The weights below were chosen to rank candidates sensibly; they are not
calibrated against ground truth, and `confidence_kind` reports "heuristic" so
the interface can say so. Ranking is what the value is used for: thresholding
and non-maximum suppression both need only a consistent ordering.
"""

from __future__ import annotations

import math

import numpy as np

from ..base import DetectionEngine
from ..geometry import bounding_box, circularity, polygon_area, polygon_centroid
from ..types import CrownDetection, DetectionParams
from ..vegetation import vegetation_mask

# Mean ExG at which the greenness component of the score saturates. Strongly
# green canopy sits around 0.4-0.8, so 0.40 means "unambiguous foliage".
GREENNESS_SATURATION = 0.40

# Score weights; they sum to 1.0 so the result is bounded by [0, 1].
WEIGHT_GREENNESS = 0.50
WEIGHT_CIRCULARITY = 0.30
WEIGHT_SOLIDITY = 0.20

# Contour simplification, as a fraction of the contour's own perimeter. Small
# enough to preserve crown shape, large enough to drop the staircase artefacts
# of the pixel grid.
SIMPLIFY_FRACTION = 0.008

# Standard deviation of the blur applied to the distance transform before
# seeking seeds. Without it, every rough mask edge produces its own local
# maximum and a single crown is shattered into slivers.
DISTANCE_BLUR_SIGMA = 2.0


class ClassicalEngine(DetectionEngine):
    name = "classical"
    title = "Classical CV (vegetation index + watershed)"
    description = (
        "Segments canopy with the Excess Green index and separates touching "
        "crowns with a distance-transform watershed. No trained model, so it "
        "runs everywhere. Confidence is a heuristic plausibility score derived "
        "from greenness, circularity and solidity, not a learned probability."
    )
    produces = "polygon"
    requires_download = False
    confidence_kind = "heuristic"

    def check(self) -> tuple[bool, str | None]:
        missing = []
        for module, package in (
            ("cv2", "opencv-python-headless"),
            ("scipy", "scipy"),
            ("skimage", "scikit-image"),
        ):
            try:
                __import__(module)
            except ImportError:
                missing.append(package)
        if missing:
            return False, (
                "Missing required packages: "
                + ", ".join(missing)
                + ". Install them with: pip install -r backend/requirements.txt"
            )
        return True, None

    def detect_tile(self, image: np.ndarray, params: DetectionParams) -> list[CrownDetection]:
        import cv2

        available, reason = self.check()
        if not available:
            raise RuntimeError(reason)

        image = np.asarray(image)
        valid_mask = None
        if image.ndim == 3 and image.shape[2] == 4:
            # A fourth band is an alpha/validity channel. Nodata must not be
            # scored as though it were very dark vegetation.
            valid_mask = image[:, :, 3] > 0
            image = image[:, :, :3]

        mask, exg = vegetation_mask(image, valid_mask=valid_mask)
        if not mask.any():
            return []

        mask_u8 = mask.astype(np.uint8)
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        mask_u8 = cv2.morphologyEx(mask_u8, cv2.MORPH_OPEN, kernel)
        mask_u8 = cv2.morphologyEx(mask_u8, cv2.MORPH_CLOSE, kernel)
        if not mask_u8.any():
            return []

        labels = self._split_crowns(mask_u8, params)
        detections: list[CrownDetection] = []
        minimum_area = max(params.min_crown_area_px, 1.0)

        for label_id in range(1, int(labels.max()) + 1):
            region = labels == label_id
            pixel_count = int(region.sum())
            if pixel_count < 4:
                continue

            ring = self._region_contour(region.astype(np.uint8))
            if ring is None:
                continue

            area_px = polygon_area(ring)
            if area_px < minimum_area:
                continue
            if params.max_crown_area_px is not None and area_px > params.max_crown_area_px:
                continue

            confidence, components = self._score(ring, region, exg, area_px)
            detections.append(
                CrownDetection(
                    polygon=ring,
                    confidence=confidence,
                    bbox=bounding_box(ring),
                    area_px=area_px,
                    engine=self.name,
                    attributes={
                        "pixel_count": pixel_count,
                        "centroid_px": polygon_centroid(ring),
                        "confidence_kind": self.confidence_kind,
                        **components,
                    },
                )
            )

        return detections

    # -- internals --------------------------------------------------------

    def _split_crowns(self, mask_u8: np.ndarray, params: DetectionParams) -> np.ndarray:
        """Label the mask, splitting touching crowns with a watershed.

        Seed spacing is derived from the caller's minimum crown area rather
        than being a magic constant: a crown of the smallest acceptable area
        has radius sqrt(A/pi), and two seeds closer together than that would be
        cutting a single crown in half.
        """
        import cv2
        from scipy import ndimage as ndi
        from skimage.feature import peak_local_max
        from skimage.segmentation import watershed

        distance = cv2.distanceTransform(mask_u8, cv2.DIST_L2, 5)
        distance = cv2.GaussianBlur(distance, (0, 0), DISTANCE_BLUR_SIGMA)

        min_radius = math.sqrt(max(params.min_crown_area_px, 1.0) / math.pi)
        min_distance = max(3, int(round(min_radius)))

        mask_bool = mask_u8.astype(bool)
        coordinates = peak_local_max(
            distance,
            min_distance=min_distance,
            labels=mask_bool,
            exclude_border=False,
        )

        if len(coordinates) == 0:
            # No interior maximum survived. Fall back to plain connected
            # components, so the canopy is still reported - just unseparated.
            labels, _ = ndi.label(mask_bool)
            return labels.astype(np.int32)

        markers = np.zeros(distance.shape, dtype=np.int32)
        markers[tuple(coordinates.T)] = np.arange(1, len(coordinates) + 1)
        return watershed(-distance, markers, mask=mask_bool).astype(np.int32)

    def _region_contour(self, region_u8: np.ndarray) -> list[tuple[float, float]] | None:
        """Largest external contour of a labelled region, as a simplified ring."""
        import cv2

        contours, _ = cv2.findContours(region_u8, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return None
        contour = max(contours, key=cv2.contourArea)
        perimeter = cv2.arcLength(contour, True)
        if perimeter > 0:
            contour = cv2.approxPolyDP(contour, SIMPLIFY_FRACTION * perimeter, True)
        ring = [(float(point[0][0]), float(point[0][1])) for point in contour]
        return ring if len(ring) >= 3 else None

    def _score(
        self,
        ring: list[tuple[float, float]],
        region: np.ndarray,
        exg: np.ndarray,
        area_px: float,
    ) -> tuple[float, dict]:
        import cv2

        greenness_raw = float(exg[region].mean()) if region.any() else 0.0
        greenness = float(np.clip(greenness_raw / GREENNESS_SATURATION, 0.0, 1.0))

        shape = circularity(ring)

        points = np.array(ring, dtype=np.float32).reshape(-1, 1, 2)
        hull_area = float(cv2.contourArea(cv2.convexHull(points)))
        solidity = float(np.clip(area_px / hull_area, 0.0, 1.0)) if hull_area > 0 else 0.0

        confidence = (
            WEIGHT_GREENNESS * greenness
            + WEIGHT_CIRCULARITY * shape
            + WEIGHT_SOLIDITY * solidity
        )
        components = {
            "mean_exg": round(greenness_raw, 4),
            "circularity": round(shape, 4),
            "solidity": round(solidity, 4),
        }
        return float(np.clip(confidence, 0.0, 1.0)), components
