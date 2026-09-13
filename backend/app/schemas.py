"""Pydantic response models shared across routers."""

from __future__ import annotations

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str = Field(description="'ok' when the service is ready to accept work")
    version: str
    environment: str


class DependencyStatus(BaseModel):
    module: str
    installed: bool
    version: str | None = None


class EngineStatus(BaseModel):
    name: str
    title: str
    description: str
    available: bool
    reason: str | None = None
    produces: str
    requires_download: bool
    confidence_kind: str = Field(
        description=(
            "How this engine's confidence should be read: 'model' is a score "
            "from a trained detector, 'heuristic' is a rule-based plausibility "
            "score. The two must not be presented to the user as the same thing."
        )
    )


class ProcessingLimits(BaseModel):
    max_upload_mb: int
    max_boundary_upload_mb: int
    tile_size: int
    tile_overlap: int
    max_pixels_without_tiling: int


class DetectionDefaults(BaseModel):
    confidence_threshold: float
    min_crown_area_m2: float
    engine: str


class CapabilitiesResponse(BaseModel):
    """Everything the client needs to know about *this* server instance.

    Values are read from the running process — never hardcoded — so the UI can
    tell the user honestly what is installed and what is not.
    """

    version: str
    environment: str
    dependencies: list[DependencyStatus]
    engines: list[EngineStatus]
    limits: ProcessingLimits
    defaults: DetectionDefaults
    supported_raster_formats: list[str]
    supported_boundary_formats: list[str]


class BoundaryPreviewResponse(BaseModel):
    """A parsed boundary, ready to be drawn and measured.

    Every field is derived from the uploaded file. `area_m2` is the ground the
    boundary encloses, measured after reprojection into `measurement_crs` - it
    is not the analysed area and not the canopy area, and the UI must not
    present it as either.
    """

    geometry: dict = Field(
        description="The boundary as a GeoJSON geometry in WGS84 (EPSG:4326)."
    )
    area_m2: float = Field(
        description=(
            "Ground enclosed by the boundary, holes excluded, in square metres. "
            "Measured in a projected CRS - never from degrees."
        )
    )
    area_hectares: float
    part_count: int = Field(description="Number of separate areas the boundary describes.")
    has_holes: bool
    source_format: str = Field(description="KML, KMZ or GeoJSON.")
    source_crs: str = Field(
        description="The CRS the file was read in, before reprojection to WGS84."
    )
    measurement_crs: str = Field(description="The projected CRS the area was measured in.")
    bounds_wgs84: list[float] = Field(
        description="[min_lon, min_lat, max_lon, max_lat] for fitting a map view."
    )
    centroid_wgs84: list[float] = Field(description="[longitude, latitude] of the centroid.")
    warnings: list[str] = Field(
        default_factory=list,
        description="Anything the user should know about how this file was read.",
    )


class ErrorBody(BaseModel):
    code: str
    message: str
    detail: dict = {}


class ErrorResponse(BaseModel):
    error: ErrorBody
