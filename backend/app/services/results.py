"""Turning an `AnalysisResult` into the JSON the API returns.

Kept apart from `analysis.py` so the pipeline stays a plain function over files
with no opinion about HTTP, and apart from `export.py` because a response read
by a browser and a file opened in QGIS a year from now want different shapes.

The shape is grouped rather than flat, and the grouping is the point: the three
areas that must never be confused live in three separately named fields, and
the standing limits of the method travel in the same payload as the numbers
they qualify.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from shapely.geometry import mapping

from ..limitations import LIMITATIONS, MODEL_ESTIMATE_NOTICE
from .analysis import AnalysisResult, Crown
from .export import boundary_metadata

if TYPE_CHECKING:  # pragma: no cover
    from .jobs import Job

HECTARE_M2 = 10_000.0


def job_status_payload(job: Job) -> dict:
    """The cheap response: where a job has got to, without its result.

    Polled repeatedly while an analysis runs, so it carries no detections - a
    client watching a progress bar should not be re-downloading a megabyte of
    polygons every second to find out that nothing has finished yet.
    """
    return {
        "job_id": job.id,
        "status": job.status,
        "stage": job.stage,
        "progress": round(job.progress, 4),
        "image_name": job.image_name,
        "boundary_name": job.boundary_name,
        "parameters": job.parameters,
        "created_at": _stamp(job.created_at),
        "started_at": _stamp(job.started_at),
        "finished_at": _stamp(job.finished_at),
        "processing_time_seconds": job.processing_time_seconds,
        "error": None
        if job.error is None
        else {"code": job.error.code, "message": job.error.message, "detail": job.error.detail},
        "result_url": f"/api/results/{job.id}" if job.status == "completed" else None,
    }


def result_payload(job: Job) -> dict:
    """The full result for a completed job."""
    result = job.result
    assert result is not None, "result_payload is only valid for a completed job"

    boundary = boundary_metadata(result)
    if boundary is not None:
        # The geometry travels with the figures so the map draws the same
        # boundary the analysis clipped to, rather than re-parsing the file.
        boundary["geometry"] = boundary_geojson(result)

    return {
        "job_id": job.id,
        "status": job.status,
        "image_metadata": image_metadata(result),
        "boundary_metadata": boundary,
        "summary": summary_payload(result),
        "detections": [detection_payload(crown) for crown in result.crowns],
        "processing_time": {
            "seconds": job.processing_time_seconds,
            "pipeline_seconds": result.elapsed_seconds,
            "tiles_processed": result.tiles_processed,
        },
        "warnings": result.warnings,
        "limitations": {"notice": MODEL_ESTIMATE_NOTICE, "items": list(LIMITATIONS)},
        "downloads": {
            "csv": f"/api/results/{job.id}/csv",
            "geojson": f"/api/results/{job.id}/geojson",
        },
    }


def image_metadata(result: AnalysisResult) -> dict:
    """What was read from the imagery itself, not from the request.

    `georeferenced` decides whether any square-metre figure in this response
    can exist at all. When it is false every `*_m2` field is null and the pixel
    fields carry the real measurements; nothing is scaled by an assumed ground
    resolution to make the response look complete.
    """
    info = result.raster
    return {
        "filename": result.source_name,
        "driver": info.driver,
        "width_px": info.width,
        "height_px": info.height,
        "band_count": info.band_count,
        "dtype": info.dtype,
        "nodata": info.nodata,
        "has_alpha": info.has_alpha,
        "georeferenced": result.georeferenced,
        "source_crs": result.source_crs,
        "crs_name": info.crs_name,
        "measurement_crs": result.measurement_crs,
        "pixel_area_m2": None if result.pixel_area_m2 is None else round(result.pixel_area_m2, 6),
        "resolution": None if info.resolution is None else list(info.resolution),
        "bounds": None if info.bounds is None else list(info.bounds),
    }


def summary_payload(result: AnalysisResult) -> dict:
    """The scene figures, with the three areas kept apart by name.

    `analyzed_area_m2` is the ground examined and is the denominator of
    coverage. `total_canopy_area_m2` is the ground under crowns. The boundary's
    own area is in `boundary_metadata` and is neither of them.
    """
    summary = result.summary
    return {
        "tree_count": summary.tree_count,
        "total_canopy_area_m2": summary.total_canopy_area_m2,
        "total_canopy_area_hectares": _hectares(summary.total_canopy_area_m2),
        "total_canopy_area_px": summary.total_canopy_area_px,
        "sum_crown_area_m2": summary.sum_crown_area_m2,
        "analyzed_area_m2": summary.analyzed_area_m2,
        "analyzed_area_hectares": _hectares(summary.analyzed_area_m2),
        "analyzed_area_px": summary.analyzed_area_px,
        "canopy_coverage_pct": summary.canopy_coverage_pct,
        "coverage_basis": summary.coverage_basis,
        "mean_crown_area_m2": summary.mean_crown_area_m2,
        "median_crown_area_m2": summary.median_crown_area_m2,
        "min_crown_area_m2": summary.min_crown_area_m2,
        "max_crown_area_m2": summary.max_crown_area_m2,
        "mean_crown_area_px": summary.mean_crown_area_px,
        "median_crown_area_px": summary.median_crown_area_px,
        "mean_confidence": summary.mean_confidence,
        "median_confidence": summary.median_confidence,
        "confidence_distribution": [
            {"lower": b.lower, "upper": b.upper, "count": b.count}
            for b in summary.confidence_distribution
        ],
        "engine": result.engine,
        "confidence_kind": result.confidence_kind,
        "confidence_threshold": result.confidence_threshold,
        "min_crown_area_m2_requested": result.min_crown_area_m2,
        "min_crown_area_px_applied": result.min_crown_area_px,
        "raw_detections": result.raw_detections,
        "duplicates_removed": result.duplicates_removed,
        "excluded_by_boundary": result.excluded_by_boundary,
        "boundary_applied": result.boundary_applied,
    }


def detection_payload(crown: Crown) -> dict:
    """One crown, with both geometries it is entitled to.

    `geometry` is WGS84 and is null for imagery with no georeferencing;
    `geometry_px` is measured from the image and is therefore always present.
    Without the second, crowns found in a plain PNG could not be drawn at all.
    """
    payload = crown.to_dict()
    payload["geometry"] = None if crown.geometry_wgs84 is None else mapping(crown.geometry_wgs84)
    payload["geometry_px"] = mapping(crown.geometry_px)
    return payload


def boundary_geojson(result: AnalysisResult) -> dict | None:
    """The applied boundary in WGS84, for drawing over the imagery."""
    if result.boundary_wgs84 is None:
        return None
    return mapping(result.boundary_wgs84)


def _hectares(area_m2: float | None) -> float | None:
    return None if area_m2 is None else round(area_m2 / HECTARE_M2, 4)


def _stamp(moment) -> str | None:
    return None if moment is None else moment.isoformat(timespec="seconds")
