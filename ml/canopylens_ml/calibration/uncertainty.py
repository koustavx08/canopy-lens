"""Image quality diagnostics and uncertainty flags for tree crown detections."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np

try:
    import cv2
    _HAS_CV2 = True
except ImportError:
    _HAS_CV2 = False

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ImageQualityDiagnostics:
    """Quantitative diagnostics of raster quality affecting detection reliability."""

    sharpness_score: float  # Variance of Laplacian (higher is sharper)
    is_blurry: bool
    contrast_ratio: float  # RMS contrast
    is_low_contrast: bool
    nodata_fraction: float
    dynamic_range_used: float  # Fraction of [0, 255] spanned
    warnings: list[str] = field(default_factory=list)

    @classmethod
    def analyze(cls, image_rgb: np.ndarray) -> ImageQualityDiagnostics:
        """Inspect image for blur, contrast, and nodata artifacts."""
        img = np.asarray(image_rgb)
        if img.ndim == 3 and img.shape[2] >= 3:
            rgb = img[:, :, :3]
        elif img.ndim == 2:
            rgb = np.stack([img] * 3, axis=-1)
        else:
            return cls(
                sharpness_score=0.0,
                is_blurry=True,
                contrast_ratio=0.0,
                is_low_contrast=True,
                nodata_fraction=1.0,
                dynamic_range_used=0.0,
                warnings=["Invalid image dimensions."],
            )

        warnings: list[str] = []

        # Nodata check (all-zero pixels)
        zero_pixels = np.all(rgb == 0, axis=-1)
        nodata_fraction = float(np.mean(zero_pixels))
        if nodata_fraction > 0.35:
            warnings.append(
                f"{nodata_fraction * 100:.1f}% of the scene contains blank/nodata pixels."
            )

        # Grayscale representation for sharpness & contrast
        if _HAS_CV2:
            gray = cv2.cvtColor(rgb.astype(np.uint8), cv2.COLOR_RGB2GRAY)
            laplacian_var = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        else:
            # Fallback simple 2nd derivative approximation
            gray = np.mean(rgb, axis=-1)
            diff_x = np.diff(gray, n=2, axis=1)
            diff_y = np.diff(gray, n=2, axis=0)
            laplacian_var = float(np.var(diff_x) + np.var(diff_y))

        is_blurry = laplacian_var < 80.0
        if is_blurry:
            warnings.append(
                f"Image sharpness is low (variance: {laplacian_var:.1f}). "
                "Atmospheric haze or camera defocus may obscure crown boundaries."
            )

        # Contrast: RMS contrast of non-nodata pixels
        valid_gray = gray[~zero_pixels] if np.any(~zero_pixels) else gray
        std_val = float(np.std(valid_gray))
        mean_val = float(np.mean(valid_gray)) if np.mean(valid_gray) > 0 else 1.0
        rms_contrast = std_val / 255.0
        is_low_contrast = rms_contrast < 0.08
        if is_low_contrast:
            warnings.append(
                f"Low image contrast (RMS: {rms_contrast:.2f}). Tree crowns may blend into understory."
            )

        # Dynamic range utilization
        min_v = float(np.min(valid_gray)) if len(valid_gray) > 0 else 0.0
        max_v = float(np.max(valid_gray)) if len(valid_gray) > 0 else 255.0
        dyn_range = (max_v - min_v) / 255.0

        return cls(
            sharpness_score=round(laplacian_var, 2),
            is_blurry=is_blurry,
            contrast_ratio=round(rms_contrast, 3),
            is_low_contrast=is_low_contrast,
            nodata_fraction=round(nodata_fraction, 4),
            dynamic_range_used=round(dyn_range, 3),
            warnings=warnings,
        )


@dataclass
class UncertaintyAssessment:
    requires_human_review: bool
    reasons: list[str] = field(default_factory=list)
    confidence_reliability: str = "high"  # high, moderate, low


def assess_detection_uncertainty(
    confidence: float,
    confidence_kind: str,
    area_px: float,
    circularity: float | None = None,
    image_blurry: bool = False,
    low_confidence_threshold: float = 0.45,
    min_area_px: float = 16.0,
    max_area_px: float = 10000.0,
) -> UncertaintyAssessment:
    """Assess whether a specific crown detection requires human analyst review."""
    reasons: list[str] = []

    if confidence < low_confidence_threshold:
        reasons.append(f"Confidence ({confidence:.2f}) is below {low_confidence_threshold:.2f}")

    if area_px < min_area_px:
        reasons.append(f"Crown area ({area_px:.0f} px) is near minimum detection resolution")
    elif area_px > max_area_px:
        reasons.append(f"Unusually large crown ({area_px:.0f} px); potential merged cluster")

    if circularity is not None and circularity < 0.35:
        reasons.append(f"Irregular crown geometry (circularity: {circularity:.2f})")

    if image_blurry:
        reasons.append("Imagery sharpness is degraded in this scene")

    if confidence_kind == "heuristic":
        # Heuristic scores are plausibility estimates, not probability calibrated
        reliability = "moderate" if len(reasons) == 0 else "low"
    else:
        reliability = "high" if len(reasons) == 0 else ("moderate" if len(reasons) == 1 else "low")

    return UncertaintyAssessment(
        requires_human_review=len(reasons) > 0,
        reasons=reasons,
        confidence_reliability=reliability,
    )
