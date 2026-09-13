"""Boundary upload and preview.

One endpoint, and it exists so that the boundary can be *seen* before anything
expensive happens. A user who uploads the wrong KML should find out from the
map in a second, not from a tree count twenty minutes later that quietly
describes the wrong stand.

The response is all measurement and no inference: the parsed geometry in WGS84
for drawing, the enclosed area in square metres for reading, and whatever the
parser had to warn about. When the file cannot be read the endpoint returns the
parser's own explanation with an `invalid_boundary` code rather than an empty
map and no reason.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, UploadFile

from ..config import Settings, get_settings
from ..schemas import BoundaryPreviewResponse, ErrorResponse
from ..services.boundary import SUPPORTED_EXTENSIONS, load_boundary, measure, to_wgs84_geojson
from ..services.uploads import temporary_upload

log = logging.getLogger(__name__)

router = APIRouter(tags=["boundary"])

HECTARE_M2 = 10_000.0


@router.post(
    "/boundary/preview",
    response_model=BoundaryPreviewResponse,
    summary="Parse and measure a KML, KMZ or GeoJSON boundary",
    responses={
        415: {"model": ErrorResponse, "description": "Unsupported boundary format"},
        422: {"model": ErrorResponse, "description": "The boundary could not be read"},
        413: {"model": ErrorResponse, "description": "The file is too large"},
    },
)
def preview_boundary(
    file: UploadFile = File(..., description="A .kml, .kmz or .geojson boundary file"),
    settings: Settings = Depends(get_settings),
) -> BoundaryPreviewResponse:
    """Read a boundary file and return it as drawable, measured geometry.

    The file is not kept. A preview is answered within the request, so storing
    the upload would only accumulate files nobody will ask for again.
    """
    with temporary_upload(
        file,
        settings.upload_dir,
        SUPPORTED_EXTENSIONS,
        settings.max_boundary_upload_mb * 1024 * 1024,
        kind="boundary",
    ) as path:
        loaded = load_boundary(path)
        measurement = measure(loaded)
        geometry = to_wgs84_geojson(loaded)

    log.info(
        "Previewed %s boundary: %d part(s), %.1f m2",
        measurement.source_format,
        measurement.part_count,
        measurement.area_m2,
    )

    return BoundaryPreviewResponse(
        geometry=geometry,
        area_m2=measurement.area_m2,
        area_hectares=round(measurement.area_m2 / HECTARE_M2, 4),
        part_count=measurement.part_count,
        has_holes=measurement.has_holes,
        source_format=measurement.source_format,
        source_crs=_crs_label(loaded.crs),
        measurement_crs=measurement.measurement_crs,
        bounds_wgs84=list(measurement.bounds_wgs84),
        centroid_wgs84=list(measurement.centroid_wgs84),
        warnings=measurement.warnings,
    )


def _crs_label(crs) -> str:
    epsg = crs.to_epsg()
    return f"EPSG:{epsg}" if epsg else (crs.name or crs.to_proj4())
