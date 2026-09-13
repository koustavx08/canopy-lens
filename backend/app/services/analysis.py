"""The analysis pipeline: imagery in, measured tree crowns out.

`analyze_image` is the one entry point. It is a plain function over file paths
so it can be called from the API, from a test, or from a script, and it returns
a structured result rather than writing anything.

Order of operations, and why each step is where it is:

1. **Describe the raster.** Everything downstream depends on whether the file
   is georeferenced, so that question is settled first and once.
2. **Resolve the engine.** Done before any imagery is read, so an environment
   with no working detector fails immediately instead of after a long read.
3. **Choose the measurement CRS.** Once per analysis, from the raster's own
   footprint - see `geo.metric_crs_for`.
4. **Convert the area threshold into pixels.** The user thinks in square
   metres; engines work in pixels. This is the only place the two meet before
   detection.
5. **Tile and detect**, offsetting each tile's local coordinates into the
   full-raster pixel grid.
6. **Deduplicate** in pixel space, where every detection is directly
   comparable and no projection has happened yet.
7. **Filter** by confidence, then by area.
8. **Georeference and measure**, reprojecting each crown into the metric CRS.
9. **Clip to the boundary**, if one was given.
10. **Summarise.**

What this function will not do is invent a number. If the raster has no
georeferencing, every square-metre field is None and the pixel fields carry the
real measurements; the caller is expected to say so rather than print a
plausible-looking area.
"""

from __future__ import annotations

import logging
import math
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import rasterio
from affine import Affine
from canopylens_ml import CrownDetection, DetectionParams
from canopylens_ml import EngineUnavailableError as MlEngineUnavailable
from canopylens_ml import resolve_engine, translate
from pyproj import CRS
from shapely.geometry import Polygon, box
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from ..errors import (
    EngineUnavailableError,
    InvalidBoundaryError,
    MissingGeoreferenceError,
)
from . import geo, raster
from .boundary import load_boundary
from .dedup import DEFAULT_IOU_THRESHOLD, Candidate, suppress

log = logging.getLogger(__name__)

CONFIDENCE_BINS = 10


@dataclass(frozen=True)
class Crown:
    """One measured tree crown.

    Every geographic field is None for imagery without georeferencing. The
    pixel fields are always populated, because they are measured directly from
    the image and are true regardless of what metadata the file carries.
    """

    id: str
    confidence: float
    pixel_area: float
    area_m2: float | None
    centroid: tuple[float, float]
    centroid_lonlat: tuple[float, float] | None
    geometry_px: Polygon
    geometry_wgs84: Polygon | None
    engine: str
    confidence_kind: str
    attributes: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Flat representation for JSON and CSV, geometry excluded."""
        return {
            "id": self.id,
            "confidence": round(self.confidence, 4),
            "pixel_area": round(self.pixel_area, 2),
            "area_m2": None if self.area_m2 is None else round(self.area_m2, 3),
            "centroid_x_px": round(self.centroid[0], 2),
            "centroid_y_px": round(self.centroid[1], 2),
            "centroid_lon": None
            if self.centroid_lonlat is None
            else round(self.centroid_lonlat[0], 8),
            "centroid_lat": None
            if self.centroid_lonlat is None
            else round(self.centroid_lonlat[1], 8),
            "engine": self.engine,
            "confidence_kind": self.confidence_kind,
        }


@dataclass(frozen=True)
class ConfidenceBin:
    lower: float
    upper: float
    count: int


@dataclass(frozen=True)
class AnalysisSummary:
    """Scene-level figures.

    `total_canopy_area` is the area of the *union* of crown polygons clipped to
    the analysed region, not the sum of their areas. Overlapping crowns would
    otherwise be counted twice and coverage could exceed 100%. The sum is
    reported separately as `sum_crown_area_m2` because it is the figure that
    matches adding up the export's area column.
    """

    tree_count: int
    total_canopy_area_m2: float | None
    total_canopy_area_px: float
    sum_crown_area_m2: float | None
    analyzed_area_m2: float | None
    analyzed_area_px: float
    canopy_coverage_pct: float | None
    coverage_basis: str
    mean_crown_area_m2: float | None
    median_crown_area_m2: float | None
    min_crown_area_m2: float | None
    max_crown_area_m2: float | None
    mean_crown_area_px: float | None
    median_crown_area_px: float | None
    mean_confidence: float | None
    median_confidence: float | None
    confidence_distribution: list[ConfidenceBin]


@dataclass(frozen=True)
class AnalysisResult:
    crowns: list[Crown]
    summary: AnalysisSummary
    raster: raster.RasterInfo
    georeferenced: bool
    source_crs: str | None
    measurement_crs: str | None
    pixel_area_m2: float | None
    engine: str
    confidence_kind: str
    confidence_threshold: float
    min_crown_area_m2: float | None
    min_crown_area_px: float
    tiles_processed: int
    raw_detections: int
    duplicates_removed: int
    excluded_by_boundary: int
    boundary_applied: bool
    boundary_wgs84: BaseGeometry | None
    elapsed_seconds: float
    warnings: list[str]


def analyze_image(
    image_path: str | Path,
    boundary_path: str | Path | None = None,
    confidence_threshold: float = 0.30,
    min_crown_area_m2: float | None = 1.0,
    *,
    min_crown_area_px: float = 0.0,
    engine: str = "auto",
    tile_size: int = 800,
    tile_overlap: int = 160,
    max_pixels_without_tiling: int = 4_000_000,
    iou_threshold: float = DEFAULT_IOU_THRESHOLD,
) -> AnalysisResult:
    """Detect and measure tree crowns in an image.

    Args:
        image_path: GeoTIFF, PNG or JPEG to analyse.
        boundary_path: Optional KML, KMZ or GeoJSON restricting the analysis.
            Requires georeferenced imagery - a boundary in degrees cannot be
            related to an image that is not on the map.
        confidence_threshold: Detections scoring below this are discarded.
        min_crown_area_m2: Minimum crown area in square metres. Ignored, with a
            warning, for imagery that has no georeferencing.
        min_crown_area_px: Minimum crown area in pixels. Used on its own for
            imagery without georeferencing, and as a floor otherwise.
        engine: Engine name, or "auto" for the best one actually available.

    Raises:
        UnsupportedFileError, InvalidRasterError: the imagery cannot be read.
        EngineUnavailableError: no detector can run in this process.
        MissingGeoreferenceError: a boundary was supplied for imagery that is
            not georeferenced.
        InvalidBoundaryError: the boundary is unreadable or does not overlap
            the imagery.
    """
    started = time.perf_counter()
    image_path = Path(image_path)

    info = raster.describe(image_path)
    warnings: list[str] = list(info.warnings)

    try:
        detector = resolve_engine(engine)
    except MlEngineUnavailable as exc:
        raise EngineUnavailableError(str(exc)) from exc

    source_crs = CRS.from_wkt(info.crs_wkt) if info.is_georeferenced else None
    metric_crs: CRS | None = None
    pixel_area: float | None = None

    if source_crs is not None:
        footprint = geo.raster_footprint(info.width, info.height, info.transform)
        metric_crs = geo.metric_crs_for(footprint, source_crs)
        pixel_area = geo.pixel_area_m2(
            info.transform,
            source_crs,
            metric_crs,
            at_column=info.width / 2.0,
            at_row=info.height / 2.0,
        )
        if pixel_area <= 0:
            raise MissingGeoreferenceError(
                "The raster's transform produces a zero-area pixel, so no "
                "physical scale can be derived from it.",
            )

    boundary_source = _load_boundary_in_source_crs(boundary_path, source_crs, info)

    effective_min_px = _minimum_area_in_pixels(
        min_crown_area_m2=min_crown_area_m2,
        min_crown_area_px=min_crown_area_px,
        pixel_area=pixel_area,
        warnings=warnings,
    )

    params = DetectionParams(
        confidence_threshold=confidence_threshold,
        min_crown_area_px=effective_min_px,
        tile_size=tile_size,
        tile_overlap=tile_overlap,
        iou_threshold=iou_threshold,
    )

    detections, tiles_processed = _detect_all_tiles(
        info, detector, params, tile_size, tile_overlap, max_pixels_without_tiling, warnings
    )
    raw_count = len(detections)

    above_threshold = [d for d in detections if d.confidence >= confidence_threshold]
    candidates = []
    for detection in above_threshold:
        polygon = geo.largest_polygon(Polygon(detection.polygon))
        if polygon is None:
            continue
        candidates.append(Candidate(polygon=polygon, confidence=detection.confidence, payload=detection))

    kept = suppress(candidates, iou_threshold=iou_threshold)
    duplicates_removed = len(candidates) - len(kept)

    crowns, excluded_by_boundary = _build_crowns(
        kept,
        info=info,
        source_crs=source_crs,
        metric_crs=metric_crs,
        min_crown_area_m2=min_crown_area_m2,
        min_crown_area_px=effective_min_px,
        boundary_source=boundary_source,
        engine_name=detector.name,
        confidence_kind=detector.confidence_kind,
    )

    summary = _summarise(
        crowns,
        info=info,
        source_crs=source_crs,
        metric_crs=metric_crs,
        pixel_area=pixel_area,
        boundary_source=boundary_source,
        warnings=warnings,
    )

    boundary_wgs84 = None
    if boundary_source is not None and source_crs is not None:
        boundary_wgs84 = geo.to_crs(boundary_source, source_crs, geo.WGS84)

    return AnalysisResult(
        crowns=crowns,
        summary=summary,
        raster=info,
        georeferenced=info.is_georeferenced,
        source_crs=_crs_label(source_crs),
        measurement_crs=_crs_label(metric_crs),
        pixel_area_m2=pixel_area,
        engine=detector.name,
        confidence_kind=detector.confidence_kind,
        confidence_threshold=confidence_threshold,
        min_crown_area_m2=min_crown_area_m2 if pixel_area is not None else None,
        min_crown_area_px=effective_min_px,
        tiles_processed=tiles_processed,
        raw_detections=raw_count,
        duplicates_removed=duplicates_removed,
        excluded_by_boundary=excluded_by_boundary,
        boundary_applied=boundary_source is not None,
        boundary_wgs84=boundary_wgs84,
        elapsed_seconds=round(time.perf_counter() - started, 3),
        warnings=warnings,
    )


# --- pipeline stages -----------------------------------------------------


def _load_boundary_in_source_crs(
    boundary_path: str | Path | None,
    source_crs: CRS | None,
    info: raster.RasterInfo,
) -> BaseGeometry | None:
    """Read a boundary and move it into the raster's CRS.

    Reprojecting the boundary into the imagery's CRS - rather than the imagery
    into the boundary's - means the raster is never resampled, so no pixel
    values are altered before detection.
    """
    if boundary_path is None:
        return None
    if source_crs is None:
        raise MissingGeoreferenceError(
            "A boundary was supplied, but this image has no georeferencing, so "
            "there is no way to tell which part of the image the boundary "
            "covers. Upload a GeoTIFF with a CRS, or analyse the whole image "
            "without a boundary.",
            detail={"file": Path(boundary_path).name},
        )

    geometry, boundary_crs = load_boundary(Path(boundary_path))
    in_source = geo.all_polygons(geo.to_crs(geometry, boundary_crs, source_crs))
    if in_source is None:
        raise InvalidBoundaryError(
            "The boundary has no usable area after being reprojected into the "
            "imagery's coordinate system.",
        )

    footprint = geo.raster_footprint(info.width, info.height, info.transform)
    if not in_source.intersects(footprint):
        raise InvalidBoundaryError(
            "The boundary does not overlap the imagery at all. Check that the "
            "KML covers the same area as the image you uploaded.",
            detail={
                "boundary_bounds_wgs84": list(geometry.bounds),
                "imagery_bounds": list(footprint.bounds),
            },
        )
    return in_source


def _minimum_area_in_pixels(
    min_crown_area_m2: float | None,
    min_crown_area_px: float,
    pixel_area: float | None,
    warnings: list[str],
) -> float:
    """Translate the user's area threshold into the pixel units engines use."""
    if pixel_area is None:
        if min_crown_area_m2:
            warnings.append(
                f"The minimum crown area of {min_crown_area_m2:g} m2 could not be "
                "applied: this image has no georeferencing, so there is no way "
                "to know how much ground a pixel covers. The pixel-based "
                "minimum was used instead."
            )
        return max(min_crown_area_px, 0.0)

    from_metres = (min_crown_area_m2 / pixel_area) if min_crown_area_m2 else 0.0
    return max(from_metres, min_crown_area_px, 0.0)


def _detect_all_tiles(
    info: raster.RasterInfo,
    detector,
    params: DetectionParams,
    tile_size: int,
    tile_overlap: int,
    max_pixels_without_tiling: int,
    warnings: list[str],
) -> tuple[list[CrownDetection], int]:
    """Run the engine over every tile, returning detections in raster pixel space."""
    windows = raster.plan_tiling(info, tile_size, tile_overlap, max_pixels_without_tiling)
    detections: list[CrownDetection] = []

    with rasterio.open(info.path) as src:
        for index, window in enumerate(windows, start=1):
            image = raster.read_rgb_window(src, window, warnings)
            if image.size == 0:
                continue
            tile_detections = detector.detect_tile(image, params)
            for detection in tile_detections:
                detection.polygon = translate(
                    detection.polygon, float(window.col_off), float(window.row_off)
                )
                detections.append(detection)
            if index % 25 == 0 or index == len(windows):
                log.info(
                    "Processed %d/%d tiles, %d raw detections so far",
                    index,
                    len(windows),
                    len(detections),
                )

    return detections, len(windows)


def _build_crowns(
    kept: list[Candidate],
    *,
    info: raster.RasterInfo,
    source_crs: CRS | None,
    metric_crs: CRS | None,
    min_crown_area_m2: float | None,
    min_crown_area_px: float,
    boundary_source: BaseGeometry | None,
    engine_name: str,
    confidence_kind: str,
) -> tuple[list[Crown], int]:
    """Georeference, measure, filter and identify the surviving detections."""
    measured: list[tuple[Polygon, float, float, float | None, Polygon | None, dict]] = []
    excluded_by_boundary = 0

    for candidate in kept:
        pixel_polygon = candidate.polygon
        pixel_area_value = float(pixel_polygon.area)
        if pixel_area_value < max(min_crown_area_px, 1.0):
            continue

        geometry_crs = None
        area = None
        if source_crs is not None and metric_crs is not None:
            geometry_crs = geo.largest_polygon(
                geo.pixel_ring_to_crs(list(pixel_polygon.exterior.coords), info.transform)
            )
            if geometry_crs is None:
                continue
            area = geo.area_m2(geometry_crs, source_crs, metric_crs)
            if min_crown_area_m2 and area < min_crown_area_m2:
                continue
            if boundary_source is not None and not boundary_source.contains(geometry_crs.centroid):
                excluded_by_boundary += 1
                continue

        attributes = dict(getattr(candidate.payload, "attributes", {}) or {})
        attributes.pop("centroid_px", None)
        measured.append(
            (pixel_polygon, candidate.confidence, pixel_area_value, area, geometry_crs, attributes)
        )

    # Identify in a stable spatial order (top-to-bottom, left-to-right) so that
    # the same imagery and settings always produce the same crown IDs, and an
    # exported CSV can be diffed against an earlier run.
    measured.sort(key=lambda item: (item[0].centroid.y, item[0].centroid.x))

    crowns: list[Crown] = []
    for index, (pixel_polygon, confidence, pixel_area_value, area, geometry_crs, attrs) in enumerate(
        measured, start=1
    ):
        centroid = pixel_polygon.centroid
        geometry_wgs84 = None
        centroid_lonlat = None
        if geometry_crs is not None and source_crs is not None:
            geometry_wgs84 = geo.to_crs(geometry_crs, source_crs, geo.WGS84)
            centroid_lonlat = geo.centroid_lonlat(geometry_crs, source_crs)

        crowns.append(
            Crown(
                id=f"crown_{index:05d}",
                confidence=float(confidence),
                pixel_area=pixel_area_value,
                area_m2=area,
                centroid=(float(centroid.x), float(centroid.y)),
                centroid_lonlat=centroid_lonlat,
                geometry_px=pixel_polygon,
                geometry_wgs84=geometry_wgs84,
                engine=engine_name,
                confidence_kind=confidence_kind,
                attributes=attrs,
            )
        )

    return crowns, excluded_by_boundary


def _analysed_region(
    info: raster.RasterInfo,
    source_crs: CRS | None,
    boundary_source: BaseGeometry | None,
) -> BaseGeometry:
    """The region the figures describe: the imagery, clipped to any boundary."""
    if source_crs is None:
        return box(0, 0, info.width, info.height)
    footprint = geo.raster_footprint(info.width, info.height, info.transform)
    if boundary_source is None:
        return footprint
    return footprint.intersection(boundary_source)


def _valid_data_fraction(
    info: raster.RasterInfo,
    source_crs: CRS | None,
    boundary_source: BaseGeometry | None,
    warnings: list[str],
) -> float:
    """Fraction of the analysed region that carries real imagery, not nodata.

    A mosaic clipped to an irregular shape is stored in a rectangular file with
    blank corners. Counting those corners as analysed ground would understate
    canopy coverage, so they are measured and removed.
    """
    with rasterio.open(info.path) as src:
        mask, scale = raster.valid_data_mask(src)
        if boundary_source is None or source_crs is None:
            inside = np.ones(mask.shape, dtype=bool)
        else:
            from rasterio.features import geometry_mask

            decimated = src.transform * Affine.scale(
                src.width / mask.shape[1], src.height / mask.shape[0]
            )
            inside = geometry_mask(
                [boundary_source],
                out_shape=mask.shape,
                transform=decimated,
                invert=True,
            )

    considered = int(inside.sum())
    if considered == 0:
        return 0.0
    fraction = float((mask & inside).sum() / considered)
    if fraction < 1.0 and scale < 1.0:
        warnings.append(
            f"About {(1 - fraction) * 100:.1f}% of the analysed region contains no "
            "imagery (nodata). The analysed area excludes it, estimated from a "
            "reduced-resolution overview."
        )
    elif fraction < 1.0:
        warnings.append(
            f"About {(1 - fraction) * 100:.1f}% of the analysed region contains no "
            "imagery (nodata) and has been excluded from the analysed area."
        )
    return fraction


def _summarise(
    crowns: list[Crown],
    *,
    info: raster.RasterInfo,
    source_crs: CRS | None,
    metric_crs: CRS | None,
    pixel_area: float | None,
    boundary_source: BaseGeometry | None,
    warnings: list[str],
) -> AnalysisSummary:
    region = _analysed_region(info, source_crs, boundary_source)
    fraction = _valid_data_fraction(info, source_crs, boundary_source, warnings)

    georeferenced = source_crs is not None and metric_crs is not None

    if georeferenced:
        region_area_m2 = geo.area_m2(region, source_crs, metric_crs) * fraction
        analyzed_area_px = region_area_m2 / pixel_area if pixel_area else 0.0
    else:
        region_area_m2 = None
        analyzed_area_px = float(region.area) * fraction

    canopy_px_union = _union_area(
        [c.geometry_px for c in crowns],
        clip_to=box(0, 0, info.width, info.height),
    )

    canopy_m2 = None
    sum_crown_area_m2 = None
    if georeferenced:
        projected = [
            geo.to_crs(c.geometry_wgs84, geo.WGS84, metric_crs)
            for c in crowns
            if c.geometry_wgs84 is not None
        ]
        region_metric = geo.to_crs(region, source_crs, metric_crs)
        canopy_m2 = _union_area(projected, clip_to=region_metric)
        areas = [c.area_m2 for c in crowns if c.area_m2 is not None]
        sum_crown_area_m2 = float(sum(areas)) if areas else 0.0

    if georeferenced and region_area_m2:
        coverage = canopy_m2 / region_area_m2 * 100.0
        basis = "area"
    elif not georeferenced and analyzed_area_px:
        coverage = canopy_px_union / analyzed_area_px * 100.0
        basis = "pixel"
    else:
        coverage = None
        basis = "unavailable"

    if coverage is not None and coverage > 100.0:
        warnings.append(
            f"Canopy coverage computed to {coverage:.1f}%, which is not physically "
            "possible. This usually means crowns were detected over nodata areas. "
            "Treat the coverage figure with caution."
        )

    areas_m2 = [c.area_m2 for c in crowns if c.area_m2 is not None]
    areas_px = [c.pixel_area for c in crowns]
    confidences = [c.confidence for c in crowns]

    return AnalysisSummary(
        tree_count=len(crowns),
        total_canopy_area_m2=None if canopy_m2 is None else round(canopy_m2, 2),
        total_canopy_area_px=round(canopy_px_union, 2),
        sum_crown_area_m2=None if sum_crown_area_m2 is None else round(sum_crown_area_m2, 2),
        analyzed_area_m2=None if region_area_m2 is None else round(region_area_m2, 2),
        analyzed_area_px=round(analyzed_area_px, 2),
        canopy_coverage_pct=None if coverage is None else round(coverage, 2),
        coverage_basis=basis,
        mean_crown_area_m2=_mean(areas_m2, 3),
        median_crown_area_m2=_median(areas_m2, 3),
        min_crown_area_m2=round(min(areas_m2), 3) if areas_m2 else None,
        max_crown_area_m2=round(max(areas_m2), 3) if areas_m2 else None,
        mean_crown_area_px=_mean(areas_px, 2),
        median_crown_area_px=_median(areas_px, 2),
        mean_confidence=_mean(confidences, 4),
        median_confidence=_median(confidences, 4),
        confidence_distribution=_confidence_distribution(confidences),
    )


# --- helpers -------------------------------------------------------------


def _union_area(geometries: list[BaseGeometry], clip_to: BaseGeometry | None = None) -> float:
    """Area of the union of geometries, optionally clipped to a region.

    The union matters: overlapping crowns cover one patch of ground, not two,
    and summing their areas would let coverage exceed 100%.
    """
    usable = [g for g in geometries if g is not None and not g.is_empty]
    if not usable:
        return 0.0
    merged = unary_union(usable)
    if clip_to is not None and not clip_to.is_empty:
        merged = merged.intersection(clip_to)
    repaired = geo.repair(merged)
    return float(repaired.area) if repaired is not None else 0.0


def _mean(values: list[float], digits: int) -> float | None:
    return round(statistics.fmean(values), digits) if values else None


def _median(values: list[float], digits: int) -> float | None:
    return round(statistics.median(values), digits) if values else None


def _confidence_distribution(confidences: list[float]) -> list[ConfidenceBin]:
    """Histogram of confidence scores in ten equal bins across [0, 1]."""
    bins = []
    for index in range(CONFIDENCE_BINS):
        lower = index / CONFIDENCE_BINS
        upper = (index + 1) / CONFIDENCE_BINS
        # The final bin includes 1.0 so a perfect score is not dropped.
        count = sum(
            1
            for c in confidences
            if (lower <= c < upper) or (index == CONFIDENCE_BINS - 1 and math.isclose(c, 1.0))
        )
        bins.append(ConfidenceBin(lower=round(lower, 2), upper=round(upper, 2), count=count))
    return bins


def _crs_label(crs: CRS | None) -> str | None:
    """Human-readable CRS identity: 'EPSG:32617' where possible, else its name."""
    if crs is None:
        return None
    epsg = crs.to_epsg()
    if epsg:
        return f"EPSG:{epsg}"
    return crs.name or crs.to_proj4()
