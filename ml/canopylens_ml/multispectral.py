"""Multispectral and RGB vegetation index calculation and crown zonal statistics.

Supports:
- Multispectral indices: NDVI, GNDVI, NDRE, SAVI (requires NIR and/or RedEdge bands)
- Visible spectrum RGB indices: VARI, GLI, ExG (native for standard 3-band imagery)
- Zonal crown statistics (mean, median, std, min, max, percentiles per crown)
- Strict validation preventing RGB imagery from being treated as multispectral
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Sequence

import numpy as np

try:
    import cv2
    _HAS_CV2 = True
except ImportError:
    _HAS_CV2 = False

log = logging.getLogger(__name__)

VEGETATION_INDEX_NOTICE = (
    "Vegetation indices measure relative canopy vigor, chlorophyll absorption, and greenness. "
    "They do NOT provide definitive clinical diagnoses of specific tree pathogens, insect infestations, "
    "or chemical soil deficiencies without ground truth leaf/soil sampling."
)


@dataclass(frozen=True)
class BandMapping:
    """Zero-indexed band positions in the raster array."""

    red: int = 0
    green: int = 1
    blue: int = 2
    nir: int | None = None
    red_edge: int | None = None

    @classmethod
    def from_band_count(cls, band_count: int) -> BandMapping:
        """Heuristic band mapping from channel count."""
        if band_count == 3:
            return cls(red=0, green=1, blue=2, nir=None, red_edge=None)
        elif band_count == 4:
            # Common 4-band order: Red, Green, Blue, NIR (or RGBN)
            return cls(red=0, green=1, blue=2, nir=3, red_edge=None)
        elif band_count >= 5:
            # 5-band MicaSense RedEdge style: Blue, Green, Red, RedEdge, NIR
            # Or RGB, RedEdge, NIR
            return cls(red=0, green=1, blue=2, red_edge=3, nir=4)
        return cls()


@dataclass
class ZonalCrownStats:
    crown_id: str
    index_name: str
    mean: float
    median: float
    std: float
    min: float
    max: float
    pixel_count: int

    def to_dict(self) -> dict:
        return {
            "crown_id": self.crown_id,
            "index_name": self.index_name,
            "mean": round(self.mean, 4),
            "median": round(self.median, 4),
            "std": round(self.std, 4),
            "min": round(self.min, 4),
            "max": round(self.max, 4),
            "pixel_count": self.pixel_count,
        }


@dataclass
class VegetationAnalysisResult:
    index_name: str
    band_count: int
    mean: float
    median: float
    std: float
    min: float
    max: float
    valid_pixel_count: int
    crown_stats: list[ZonalCrownStats] = field(default_factory=list)
    distribution_bins: list[dict] = field(default_factory=list)
    limitations: str = VEGETATION_INDEX_NOTICE

    def to_dict(self) -> dict:
        return {
            "index_name": self.index_name,
            "band_count": self.band_count,
            "scene_statistics": {
                "mean": round(self.mean, 4),
                "median": round(self.median, 4),
                "std": round(self.std, 4),
                "min": round(self.min, 4),
                "max": round(self.max, 4),
                "valid_pixels": self.valid_pixel_count,
            },
            "crown_count": len(self.crown_stats),
            "crown_statistics": [c.to_dict() for c in self.crown_stats],
            "distribution": self.distribution_bins,
            "notice": self.limitations,
        }


def compute_vegetation_index(
    data: np.ndarray,
    index_name: str,
    band_mapping: BandMapping | None = None,
    nodata: float | None = None,
) -> np.ndarray:
    """Compute 2D vegetation index grid from raster bands.

    Supported indices:
    - Multispectral (requires NIR): NDVI, GNDVI, NDRE, SAVI
    - RGB native: VARI, GLI, ExG
    """
    arr = np.asarray(data, dtype=np.float32)
    # Ensure shape (C, H, W)
    if arr.ndim == 2:
        raise ValueError("Raster must have at least 3 channels to compute vegetation indices.")
    if arr.ndim == 3 and arr.shape[0] > arr.shape[2]:
        # Handle (H, W, C) -> transpose to (C, H, W)
        arr = np.transpose(arr, (2, 0, 1))

    c, h, w = arr.shape
    mapping = band_mapping or BandMapping.from_band_count(c)
    index_key = index_name.upper().strip()

    # Nodata masking
    valid_mask = np.ones((h, w), dtype=bool)
    if nodata is not None:
        valid_mask = ~np.any(arr == nodata, axis=0)

    # 1. Multispectral Index validation
    if index_key in ("NDVI", "GNDVI", "SAVI"):
        if mapping.nir is None or mapping.nir >= c:
            raise ValueError(
                f"Index {index_key} requires a Near-Infrared (NIR) band, but this raster "
                f"only has {c} channels. For standard 3-band visible imagery, use RGB-native "
                f"indices such as VARI, GLI, or ExG."
            )
    elif index_key == "NDRE":
        if mapping.nir is None or mapping.red_edge is None or max(mapping.nir, mapping.red_edge) >= c:
            raise ValueError(
                f"Index NDRE requires both Near-Infrared (NIR) and RedEdge bands, but this raster "
                f"has {c} channels. Check sensor specifications."
            )

    eps = 1e-7

    if index_key == "NDVI":
        nir = arr[mapping.nir]
        red = arr[mapping.red]
        denom = nir + red
        denom[denom == 0] = eps
        result = (nir - red) / denom

    elif index_key == "GNDVI":
        nir = arr[mapping.nir]
        green = arr[mapping.green]
        denom = nir + green
        denom[denom == 0] = eps
        result = (nir - green) / denom

    elif index_key == "NDRE":
        nir = arr[mapping.nir]
        red_edge = arr[mapping.red_edge]
        denom = nir + red_edge
        denom[denom == 0] = eps
        result = (nir - red_edge) / denom

    elif index_key == "SAVI":
        nir = arr[mapping.nir]
        red = arr[mapping.red]
        L = 0.5
        denom = nir + red + L
        denom[denom == 0] = eps
        result = ((nir - red) / denom) * (1.0 + L)

    elif index_key == "VARI":
        # Visible Atmospherically Resistant Index: (Green - Red) / (Green + Red - Blue)
        green = arr[mapping.green]
        red = arr[mapping.red]
        blue = arr[mapping.blue]
        denom = green + red - blue
        denom[denom == 0] = eps
        result = (green - red) / denom

    elif index_key == "GLI":
        # Green Leaf Index: (2*Green - Red - Blue) / (2*Green + Red + Blue)
        green = arr[mapping.green]
        red = arr[mapping.red]
        blue = arr[mapping.blue]
        denom = (2.0 * green) + red + blue
        denom[denom == 0] = eps
        result = ((2.0 * green) - red - blue) / denom

    elif index_key == "EXG":
        # Excess Green: 2g - r - b using chromatic coordinates
        red = arr[mapping.red]
        green = arr[mapping.green]
        blue = arr[mapping.blue]
        total = red + green + blue
        total[total == 0] = eps
        r = red / total
        g = green / total
        b = blue / total
        result = 2.0 * g - r - b

    else:
        raise ValueError(
            f"Unsupported vegetation index {index_name!r}. "
            "Supported: NDVI, GNDVI, NDRE, SAVI, VARI, GLI, ExG."
        )

    # Set invalid pixels to NaN
    result[~valid_mask] = np.nan
    return result


def compute_zonal_crown_statistics(
    index_grid: np.ndarray,
    crowns_px: Sequence[dict],
) -> list[ZonalCrownStats]:
    """Compute per-crown zonal statistics across an index raster grid."""
    h, w = index_grid.shape[:2]
    stats_list: list[ZonalCrownStats] = []

    for crown in crowns_px:
        crown_id = crown.get("id", "crown")
        pts = crown.get("polygon_px", [])
        if len(pts) < 3:
            continue

        # Fast bounding box rasterization
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        x1 = max(0, int(min(xs)))
        y1 = max(0, int(min(ys)))
        x2 = min(w, int(max(xs)) + 1)
        y2 = min(h, int(max(ys)) + 1)

        if x2 <= x1 or y2 <= y1:
            continue

        patch = index_grid[y1:y2, x1:x2]

        # Local polygon mask
        local_pts = [(p[0] - x1, p[1] - y1) for p in pts]
        mask = np.zeros((y2 - y1, x2 - x1), dtype=np.uint8)

        if _HAS_CV2:
            cnt = np.asarray(local_pts, dtype=np.int32).reshape((-1, 1, 2))
            cv2.fillPoly(mask, [cnt], 1)
        else:
            # Bounding box fallback if cv2 absent
            mask.fill(1)

        values = patch[(mask == 1) & (~np.isnan(patch))]
        if len(values) == 0:
            continue

        stats_list.append(
            ZonalCrownStats(
                crown_id=crown_id,
                index_name=crown.get("index_name", "index"),
                mean=float(np.mean(values)),
                median=float(np.median(values)),
                std=float(np.std(values)),
                min=float(np.min(values)),
                max=float(np.max(values)),
                pixel_count=len(values),
            )
        )

    return stats_list


def analyze_vegetation(
    raster_data: np.ndarray,
    index_name: str = "auto",
    crowns_px: Sequence[dict] | None = None,
    band_mapping: BandMapping | None = None,
    nodata: float | None = None,
) -> VegetationAnalysisResult:
    """End-to-end vegetation analysis on raster imagery and crowns."""
    arr = np.asarray(raster_data)
    if arr.ndim == 3 and arr.shape[0] > arr.shape[2]:
        arr = np.transpose(arr, (2, 0, 1))

    c = arr.shape[0] if arr.ndim == 3 else 1

    # Auto index selection based on available bands
    if index_name == "auto":
        index_name = "NDVI" if c >= 4 else "VARI"

    index_grid = compute_vegetation_index(
        arr, index_name=index_name, band_mapping=band_mapping, nodata=nodata
    )

    valid_vals = index_grid[~np.isnan(index_grid)]
    if len(valid_vals) == 0:
        valid_vals = np.array([0.0], dtype=np.float32)

    # Compute 10-bin histogram distribution
    min_v = float(np.min(valid_vals))
    max_v = float(np.max(valid_vals))
    hist, bin_edges = np.histogram(valid_vals, bins=10, range=(min_v, max_v if max_v > min_v else min_v + 1.0))
    bins_data = [
        {"lower": round(float(bin_edges[i]), 3), "upper": round(float(bin_edges[i + 1]), 3), "count": int(hist[i])}
        for i in range(len(hist))
    ]

    crown_results = []
    if crowns_px:
        # Inject index_name into crown dicts
        annotated_crowns = [{**c, "index_name": index_name} for c in crowns_px]
        crown_results = compute_zonal_crown_statistics(index_grid, annotated_crowns)

    return VegetationAnalysisResult(
        index_name=index_name.upper(),
        band_count=c,
        mean=float(np.mean(valid_vals)),
        median=float(np.median(valid_vals)),
        std=float(np.std(valid_vals)),
        min=min_v,
        max=max_v,
        valid_pixel_count=len(valid_vals),
        crown_stats=crown_results,
        distribution_bins=bins_data,
    )
