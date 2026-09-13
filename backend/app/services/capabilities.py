"""Builds the /api/capabilities payload from the live process state."""

from __future__ import annotations

from canopylens_ml import available_engines, probe_dependencies

from ..config import Settings
from ..schemas import (
    CapabilitiesResponse,
    DependencyStatus,
    DetectionDefaults,
    EngineStatus,
    ProcessingLimits,
)

SUPPORTED_RASTER_FORMATS = [".tif", ".tiff", ".geotiff", ".png", ".jpg", ".jpeg"]
SUPPORTED_BOUNDARY_FORMATS = [".kml", ".kmz", ".geojson", ".json"]


def build_capabilities(settings: Settings, version: str) -> CapabilitiesResponse:
    return CapabilitiesResponse(
        version=version,
        environment=settings.environment,
        dependencies=[
            DependencyStatus(module=d.module, installed=d.installed, version=d.version)
            for d in probe_dependencies()
        ],
        engines=[
            EngineStatus(
                name=e.name,
                title=e.title,
                description=e.description,
                available=e.available,
                reason=e.reason,
                produces=e.produces,
                requires_download=e.requires_download,
                confidence_kind=e.confidence_kind,
            )
            for e in available_engines()
        ],
        limits=ProcessingLimits(
            max_upload_mb=settings.max_upload_mb,
            tile_size=settings.tile_size,
            tile_overlap=settings.tile_overlap,
            max_pixels_without_tiling=settings.max_pixels_without_tiling,
        ),
        defaults=DetectionDefaults(
            confidence_threshold=settings.default_confidence,
            min_crown_area_m2=settings.default_min_crown_area_m2,
            engine=settings.default_engine,
        ),
        supported_raster_formats=SUPPORTED_RASTER_FORMATS,
        supported_boundary_formats=SUPPORTED_BOUNDARY_FORMATS,
    )
