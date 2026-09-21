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
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import rasterio
from affine import Affine
from canopylens_ml import CrownDetection, DetectionParams, resolve_engine, translate
from canopylens_ml import EngineUnavailableError as MlEngineUnavailable
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
from .boundary import LoadedBoundary, load_boundary
from .boundary import measure as measure_boundary
from .dedup import DEFAULT_IOU_THRESHOLD, Candidate, suppress

log = logging.getLogger(__name__)

CONFIDENCE_BINS = 10

ProgressCallback = Callable[[str, float], None]
"""Called as `(stage, fraction)` while an analysis runs.

`fraction` moves from 0.0 to 1.0 and is derived from tiles completed, not from
elapsed time: a progress bar that is really a stopwatch tells the user how long
they have waited, which they already know. Detection occupies the middle of the
range because it is the only stage whose length depends on the image.
"""

_DETECTION_BAND = (0.15, 0.85)

StopCheck = Callable[[], bool]
"""Asked, at each checkpoint, whether the analysis should give up.

Separate from `on_progress` rather than folded into it. A progress reporter is
allowed to fail without consequence - its exceptions are swallowed, because a
broken progress bar must not lose an analysis that is succeeding - and a stop
signal that travelled the same path would be swallowed with them.
"""


class AnalysisCancelled(Exception):
    """Raised at a checkpoint when the analysis has been asked to stop.

    Not a `CanopyLensError`: nothing went wrong, and it is not an answer to
    give an HTTP caller. The job that asked for the work records that it was
    cancelled and there is nothing else to report.
    """


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
    geometry_crs: Polygon | None
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
class BoundarySummary:
    """The supplied boundary, measured in its own right.

    This is deliberately separate from the scene figures, because three areas
    are in play and confusing them is the easiest way to publish a wrong
    number:

    - **Boundary area** (`area_m2`) is the ground the KML encloses. It is a
      property of the file the user drew, and it exists whether or not any
      imagery covers it.
    - **Analysed area** (`AnalysisSummary.analyzed_area_m2`) is the part of
      that boundary the imagery actually covers, with nodata removed. It is
      the denominator of canopy coverage.
    - **Detected crown area** (`AnalysisSummary.total_canopy_area_m2`) is the
      ground covered by crowns inside the analysed area.

    `imagery_covers_pct` is what fraction of the boundary the imagery reaches.
    A boundary larger than the image is legitimate - it is also a reason the
    user should not read the result as a survey of the whole stand.
    """

    area_m2: float
    area_within_imagery_m2: float
    imagery_covers_pct: float
    part_count: int
    parts_intersecting_imagery: int
    has_holes: bool
    source_format: str
    measurement_crs: str
    bounds_wgs84: tuple[float, float, float, float]
    centroid_wgs84: tuple[float, float]


@dataclass(frozen=True)
class AnalysisSummary:
    """Scene-level figures.

    Three quantities are reported and they are not interchangeable:

    - `analyzed_area_m2` - the ground examined: the imagery footprint, clipped
      to the boundary if one was given, with nodata excluded.
    - `total_canopy_area_m2` - the detected crown area: the ground covered by
      crowns within that region.
    - `canopy_coverage_pct` - the first as a percentage of the second,
      `detected crown area / analysed area * 100`.

    The boundary's own area is *not* the analysed area and is reported on
    `AnalysisResult.boundary` instead - see `BoundarySummary`.

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
    source_name: str
    """What to call this image when reporting on it.

    The stored file is named by the server - an upload's own name is never used
    as a path - so without this a result and its exports would identify the
    imagery by a random hex string that means nothing to the person who
    uploaded it.
    """
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
    boundary: BoundarySummary | None
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
    on_progress: ProgressCallback | None = None,
    should_stop: StopCheck | None = None,
    source_name: str | None = None,
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
        source_name: What to call the image in the result and its exports.
            Defaults to the file's own name, which is right for a local file
            and wrong for an upload stored under a generated name.
        on_progress: Optional `(stage, fraction)` callback. Exceptions raised by
            it are swallowed: a progress reporter must never be able to fail an
            analysis that is otherwise succeeding.
        should_stop: Optional predicate consulted at each checkpoint, and once
            per tile. Returning True abandons the analysis at that point.

    Raises:
        AnalysisCancelled: `should_stop` asked for the work to end.
        UnsupportedFileError, InvalidRasterError: the imagery cannot be read.
        EngineUnavailableError: no detector can run in this process.
        MissingGeoreferenceError: a boundary was supplied for imagery that is
            not georeferenced.
        InvalidBoundaryError: the boundary is unreadable or does not overlap
            the imagery.
    """
    started = time.perf_counter()
    image_path = Path(image_path)
    _stop_if_asked(should_stop)
    _report(on_progress, "reading", 0.02)

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

    prepared_boundary = _prepare_boundary(
        boundary_path, source_crs, metric_crs, info, warnings
    )
    boundary_source = prepared_boundary.geometry if prepared_boundary else None

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

    _stop_if_asked(should_stop)
    _report(on_progress, "detecting", _DETECTION_BAND[0])
    detections, tiles_processed = _detect_all_tiles(
        info,
        detector,
        params,
        tile_size,
        tile_overlap,
        max_pixels_without_tiling,
        warnings,
        on_progress=on_progress,
        should_stop=should_stop,
    )
    raw_count = len(detections)

    above_threshold = [d for d in detections if d.confidence >= confidence_threshold]
    candidates = []
    for detection in above_threshold:
        polygon = geo.largest_polygon(Polygon(detection.polygon))
        if polygon is None:
            continue
        candidates.append(
            Candidate(polygon=polygon, confidence=detection.confidence, payload=detection)
        )

    kept = suppress(candidates, iou_threshold=iou_threshold)
    duplicates_removed = len(candidates) - len(kept)

    _stop_if_asked(should_stop)
    _report(on_progress, "measuring", _DETECTION_BAND[1])
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

    _report(on_progress, "summarising", 0.95)
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
    if prepared_boundary is not None:
        boundary_wgs84 = geo.to_crs(
            prepared_boundary.loaded.geometry, prepared_boundary.loaded.crs, geo.WGS84
        )

    return AnalysisResult(
        crowns=crowns,
        summary=summary,
        raster=info,
        source_name=source_name or image_path.name,
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
        boundary_applied=prepared_boundary is not None,
        boundary_wgs84=boundary_wgs84,
        boundary=prepared_boundary.summary if prepared_boundary else None,
        elapsed_seconds=round(time.perf_counter() - started, 3),
        warnings=warnings,
    )


# --- pipeline stages -----------------------------------------------------


@dataclass(frozen=True)
class _PreparedBoundary:
    """A loaded boundary placed against the imagery it will clip."""

    geometry: BaseGeometry  # in the raster's own CRS
    loaded: LoadedBoundary
    summary: BoundarySummary


def _prepare_boundary(
    boundary_path: str | Path | None,
    source_crs: CRS | None,
    metric_crs: CRS | None,
    info: raster.RasterInfo,
    warnings: list[str],
) -> _PreparedBoundary | None:
    """Read a boundary, move it into the raster's CRS, and measure it.

    Reprojecting the boundary into the imagery's CRS - rather than the imagery
    into the boundary's - means the raster is never resampled, so no pixel
    values are altered before detection.

    The boundary is measured here, in the same metric CRS the imagery is
    measured in, so that "boundary area" and "analysed area" are comparable
    figures rather than two numbers from two projections.
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

    loaded = load_boundary(Path(boundary_path))
    warnings.extend(loaded.warnings)

    in_source = geo.all_polygons(geo.to_crs(loaded.geometry, loaded.crs, source_crs))
    if in_source is None:
        raise InvalidBoundaryError(
            "The boundary has no usable area after being reprojected into the "
            "imagery's coordinate system.",
            detail={"boundary_crs": _crs_label(loaded.crs), "imagery_crs": _crs_label(source_crs)},
        )

    footprint = geo.raster_footprint(info.width, info.height, info.transform)
    overlap = in_source.intersection(footprint)
    if overlap.is_empty or overlap.area <= 0:
        in_wgs84 = geo.to_crs(loaded.geometry, loaded.crs, geo.WGS84)
        footprint_wgs84 = geo.to_crs(footprint, source_crs, geo.WGS84)
        raise InvalidBoundaryError(
            "The boundary does not overlap the imagery at all, so there would "
            "be nothing to analyse. Check that the boundary file covers the "
            "same ground as the image you uploaded.",
            detail={
                "boundary_bounds_wgs84": [round(v, 6) for v in in_wgs84.bounds],
                "imagery_bounds_wgs84": [round(v, 6) for v in footprint_wgs84.bounds],
            },
        )

    measurement = measure_boundary(loaded, metric_crs)
    within_m2 = geo.area_m2(overlap, source_crs, metric_crs) if metric_crs else 0.0
    covers_pct = (within_m2 / measurement.area_m2 * 100.0) if measurement.area_m2 else 0.0

    parts = list(getattr(in_source, "geoms", [in_source]))
    intersecting = sum(1 for part in parts if part.intersects(footprint))
    if intersecting < len(parts):
        warnings.append(
            f"{len(parts) - intersecting} of the boundary's {len(parts)} parts "
            "lie entirely outside the imagery and contribute no analysed area."
        )

    # A boundary wider than the picture is a normal thing to upload and not an
    # error - but the result then describes the overlap, not the stand, and the
    # user has to be told which one they are looking at.
    if covers_pct < 99.5:
        warnings.append(
            f"The imagery covers {covers_pct:.1f}% of the boundary "
            f"({within_m2:,.0f} m2 of {measurement.area_m2:,.0f} m2). Results "
            "describe only the part of the boundary the image reaches."
        )

    summary = BoundarySummary(
        area_m2=round(measurement.area_m2, 2),
        area_within_imagery_m2=round(within_m2, 2),
        imagery_covers_pct=round(covers_pct, 2),
        part_count=measurement.part_count,
        parts_intersecting_imagery=intersecting,
        has_holes=measurement.has_holes,
        source_format=measurement.source_format,
        measurement_crs=measurement.measurement_crs,
        bounds_wgs84=measurement.bounds_wgs84,
        centroid_wgs84=measurement.centroid_wgs84,
    )
    return _PreparedBoundary(geometry=in_source, loaded=loaded, summary=summary)


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
    on_progress: ProgressCallback | None = None,
    should_stop: StopCheck | None = None,
) -> tuple[list[CrownDetection], int]:
    """Run the engine over every tile, returning detections in raster pixel space."""
    windows = raster.plan_tiling(info, tile_size, tile_overlap, max_pixels_without_tiling)
    detections: list[CrownDetection] = []
    low, high = _DETECTION_BAND

    with rasterio.open(info.path) as src:
        for index, window in enumerate(windows, start=1):
            # Checked once per tile: the tile is the unit of work here, so this
            # is the finest granularity at which stopping is possible, and on a
            # large mosaic it is the difference between a cancel that lands in
            # a second and one that lands in several minutes.
            _stop_if_asked(should_stop)
            image = raster.read_rgb_window(src, window, warnings)
            if image.size == 0:
                continue
            tile_detections = detector.detect_tile(image, params)
            for detection in tile_detections:
                detection.polygon = translate(
                    detection.polygon, float(window.col_off), float(window.row_off)
                )
                detections.append(detection)
            _report(on_progress, "detecting", low + (high - low) * index / len(windows))
            if index % 25 == 0 or index == len(windows):
                log.info(
                    "Processed %d/%d tiles, %d raw detections so far",
                    index,
                    len(windows),
                    len(detections),
                )

    return detections, len(windows)


def _stop_if_asked(should_stop: StopCheck | None) -> None:
    """Abandon the analysis if the caller has asked for it to end.

    A failure to answer is taken as "carry on". Whatever went wrong with the
    check, throwing away work in progress is the more expensive way to be
    wrong about it.
    """
    if should_stop is None:
        return
    try:
        asked = should_stop()
    except Exception:  # pragma: no cover - defensive
        log.warning("Cancellation check failed; continuing", exc_info=True)
        return
    if asked:
        raise AnalysisCancelled


def _report(on_progress: ProgressCallback | None, stage: str, fraction: float) -> None:
    """Tell the caller where the analysis has got to, if it asked.

    A broken progress reporter is a reporting problem, not an analysis problem,
    so its exceptions are logged and dropped rather than losing work that has
    already succeeded.
    """
    if on_progress is None:
        return
    try:
        on_progress(stage, max(0.0, min(1.0, fraction)))
    except Exception:  # pragma: no cover - defensive
        log.warning("Progress callback failed at stage %s", stage, exc_info=True)


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
            # A crown belongs to the boundary when its centre does. Requiring
            # the whole polygon to be inside would drop every tree on the
            # forest edge; accepting any overlap would pull in trees that are
            # mostly outside it. `covers` rather than `contains` so a centroid
            # falling exactly on the line counts as inside.
            if boundary_source is not None and not boundary_source.covers(geometry_crs.centroid):
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
    for index, entry in enumerate(measured, start=1):
        pixel_polygon, confidence, pixel_area_value, area, geometry_crs, attrs = entry
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
                geometry_crs=geometry_crs,
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

            decimated = src.transform @ Affine.scale(
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
        # Projected by the same route `area_m2` used, and repaired the same
        # way, so that the union and the sum describe the same polygons. Going
        # via WGS84 instead would be a second transform of the same crown: the
        # areas would differ in the fifth significant figure, and the union
        # could come out fractionally larger than the sum it must never exceed.
        projected = [
            geo.repair(geo.to_crs(c.geometry_crs, source_crs, metric_crs))
            for c in crowns
            if c.geometry_crs is not None
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

    # The ground the crowns cover cannot exceed the ground they add up to.
    # Measuring both on the same polygons makes that true of the raw figures;
    # the cap makes it true of the reported ones as well. A scene can land
    # exactly on a .005 boundary, and there a disagreement far below the two
    # decimals being reported is still enough to round the pair apart.
    reported_sum = None if sum_crown_area_m2 is None else round(sum_crown_area_m2, 2)
    reported_canopy = None if canopy_m2 is None else round(canopy_m2, 2)
    if reported_canopy is not None and reported_sum is not None:
        reported_canopy = min(reported_canopy, reported_sum)

    return AnalysisSummary(
        tree_count=len(crowns),
        total_canopy_area_m2=reported_canopy,
        total_canopy_area_px=round(canopy_px_union, 2),
        sum_crown_area_m2=reported_sum,
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
