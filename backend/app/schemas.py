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


class ErrorBody(BaseModel):
    code: str
    message: str
    detail: dict = {}


class ErrorResponse(BaseModel):
    error: ErrorBody
