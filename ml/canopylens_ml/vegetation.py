"""Vegetation indices and masking for RGB imagery.

CanopyLens targets ordinary RGB aerial and satellite imagery, which has no
near-infrared band. NDVI is therefore not available, so the classical engine
uses Excess Green (ExG), the standard RGB-only greenness index:

    ExG = 2g - r - b      where r, g, b are chromatic coordinates

Working in chromatic coordinates (each channel divided by the sum of all three)
rather than raw DNs makes the index largely insensitive to overall brightness,
which matters because canopy is frequently in shadow.

Reference values: neutral grey scores 0.0, moderately green vegetation around
0.15, and strongly green vegetation 0.5 and above. The index is bounded by
[-1, 2].
"""

from __future__ import annotations

import numpy as np

# ExG value below which a pixel is not considered vegetation at all. Applied as
# a hard floor so that a scene with no vegetation in it yields an empty mask
# rather than whatever Otsu's method decides to call "the greener half".
VEGETATION_FLOOR = 0.05

# If a floor-only mask already covers more than this fraction of a tile, the
# tile is mostly canopy. Otsu would then split the canopy itself into a bright
# and a dark half and silently discard half the trees, so the floor is used on
# its own and crown separation is left to the watershed step.
DENSE_COVERAGE_FRACTION = 0.5


def as_rgb_float(image: np.ndarray) -> np.ndarray:
    """Validate an (H, W, 3) image and return it as float32.

    Accepts any numeric dtype; uint8 and uint16 imagery both work because the
    index is computed from channel ratios rather than absolute values.
    """
    arr = np.asarray(image)
    if arr.ndim != 3 or arr.shape[2] < 3:
        raise ValueError(f"Expected an (H, W, 3) RGB image, got shape {arr.shape}")
    return arr[:, :, :3].astype(np.float32)


def excess_green(image: np.ndarray) -> np.ndarray:
    """Excess Green index of an RGB image, as float32 in [-1, 2]."""
    arr = as_rgb_float(image)
    total = arr.sum(axis=2)
    # Pure black pixels carry no colour information; give them a neutral
    # denominator so they end up at ExG 0 instead of dividing by zero.
    total = np.where(total <= 0.0, 1.0, total)
    r = arr[:, :, 0] / total
    g = arr[:, :, 1] / total
    b = arr[:, :, 2] / total
    return (2.0 * g - r - b).astype(np.float32)


def otsu_threshold(values: np.ndarray) -> float:
    """Otsu's threshold on the ExG array, returned in ExG units.

    Implemented directly rather than via OpenCV so that this module keeps
    working on float data without a lossy round-trip through uint8, and so the
    vegetation index stays usable when OpenCV is not installed.
    """
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return VEGETATION_FLOOR
    lo, hi = float(finite.min()), float(finite.max())
    if hi - lo < 1e-6:
        return lo

    counts, edges = np.histogram(finite, bins=256, range=(lo, hi))
    centres = (edges[:-1] + edges[1:]) / 2.0
    weights = counts.astype(np.float64)
    total = weights.sum()
    if total == 0:
        return lo

    w0 = np.cumsum(weights)
    w1 = total - w0
    sum_all = np.cumsum(weights * centres)
    mean_total = sum_all[-1]

    valid = (w0 > 0) & (w1 > 0)
    if not valid.any():
        return lo
    mean0 = np.divide(sum_all, w0, out=np.zeros_like(sum_all), where=w0 > 0)
    mean1 = np.divide(mean_total - sum_all, w1, out=np.zeros_like(sum_all), where=w1 > 0)
    between = w0 * w1 * (mean0 - mean1) ** 2
    between[~valid] = -1.0

    # When the two populations are cleanly separated, every bin in the empty
    # gap between them scores identically, because none of w0, w1, mean0 or
    # mean1 changes across it. np.argmax would return the lowest such bin,
    # putting the threshold right at the top edge of the darker population
    # instead of in the middle of the gap. Taking the centre of the tied
    # plateau keeps the cut where the separation is widest, which is what
    # makes the mask robust to a little noise on either side.
    best = between.max()
    tied = np.flatnonzero(between >= best - 1e-12)
    return float(centres[int(np.median(tied))])


def vegetation_threshold(exg: np.ndarray, floor: float = VEGETATION_FLOOR) -> float:
    """Choose the ExG cut-off for a tile.

    The threshold is adaptive (Otsu) but never drops below the floor, and is
    skipped entirely for tiles that are predominantly vegetation — see
    DENSE_COVERAGE_FRACTION.
    """
    coarse = exg > floor
    if coarse.size == 0 or not coarse.any():
        return floor
    if coarse.mean() > DENSE_COVERAGE_FRACTION:
        return floor
    return max(floor, otsu_threshold(exg))


def vegetation_mask(
    image: np.ndarray,
    floor: float = VEGETATION_FLOOR,
    valid_mask: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Return (boolean vegetation mask, ExG array) for an RGB image.

    `valid_mask` excludes nodata pixels from both the threshold decision and
    the result, so a partially filled tile is not judged against its own blank
    padding.
    """
    exg = excess_green(image)
    considered = exg if valid_mask is None else exg[valid_mask.astype(bool)]
    threshold = vegetation_threshold(
        considered if considered.size else exg,
        floor=floor,
    )
    mask = exg > threshold
    if valid_mask is not None:
        mask &= valid_mask.astype(bool)
    return mask, exg
