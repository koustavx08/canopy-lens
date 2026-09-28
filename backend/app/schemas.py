"""Pydantic response models shared across routers."""

from __future__ import annotations

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str = Field(description="'ok' when the service is ready to accept work")
    version: str
    environment: str


class ReadinessCheck(BaseModel):
    name: str
    ok: bool
    detail: str = Field(description="What was checked, and what was found.")
    critical: bool = Field(
        description="Whether failing this check means the service cannot do its job."
    )


class ReadinessResponse(BaseModel):
    status: str = Field(
        description=(
            "'ready' when every check passed, 'degraded' when only advisory ones "
            "failed, 'not_ready' when the service cannot analyse anything."
        )
    )
    version: str
    environment: str
    checks: list[ReadinessCheck]


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


class JobAcceptedResponse(BaseModel):
    """The receipt for a submitted analysis.

    Returned with 202: the files have been accepted and validated, and the work
    has been scheduled. Nothing has been detected yet, so no figure appears here
    that could be mistaken for a result.
    """

    job_id: str
    status: str = Field(description="'queued' - the analysis has not started yet.")
    status_url: str = Field(description="Poll this for progress.")
    result_url: str = Field(description="Available once the status is 'completed'.")


class JobErrorBody(BaseModel):
    code: str
    message: str
    detail: dict = {}


class JobStatusResponse(BaseModel):
    """Where an analysis has got to.

    `progress` is derived from tiles completed, not from elapsed time. It is a
    measure of work done; it is not an estimate of time remaining, and the API
    does not offer one it cannot compute.
    """

    job_id: str
    status: str = Field(description="queued, running, completed, failed or cancelled.")
    stage: str = Field(
        description=(
            "Which part of the pipeline is current: reading, detecting, "
            "measuring, summarising."
        )
    )
    progress: float = Field(ge=0.0, le=1.0, description="Fraction of the tiles processed.")
    image_name: str
    boundary_name: str | None = None
    parameters: dict
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    processing_time_seconds: float | None = Field(
        default=None,
        description="Time spent analysing, excluding any time spent queued.",
    )
    error: JobErrorBody | None = None
    result_url: str | None = None


class ErrorBody(BaseModel):
    code: str
    message: str
    detail: dict = {}


class ErrorResponse(BaseModel):
    error: ErrorBody


# ---------------------------------------------------------------------------
# ML Intelligence Models: Registry, Evaluation, Training, Vegetation, Change
# ---------------------------------------------------------------------------


class ModelMetadataResponse(BaseModel):
    id: str
    name: str
    architecture: str
    version: str
    description: str
    created_at: str
    checkpoint_path: str | None = None
    dataset_name: str | None = None
    image_resolution_m: float | None = None
    training_config: dict = Field(default_factory=dict)
    metrics: dict = Field(default_factory=dict)
    status: str = "ready"
    is_default: bool = False
    is_fallback: bool = False


class EvaluationResponse(BaseModel):
    model_name: str
    dataset_name: str
    summary: dict
    metrics_at_50: dict
    segmentation: dict = Field(default_factory=dict)
    forest_types: dict = Field(default_factory=dict)
    threshold_map: dict = Field(default_factory=dict)


class BenchmarkSummaryItem(BaseModel):
    model_name: str
    dataset_name: str
    map50: float
    map50_95: float
    precision_50: float
    recall_50: float
    f1_50: float
    count_mae: float
    count_rmse: float
    area_ratio: float


class BenchmarkResponse(BaseModel):
    models: list[dict]
    comparison_table: list[BenchmarkSummaryItem]
    markdown_table: str


class TrainJobAcceptedResponse(BaseModel):
    job_id: str
    model_name: str
    status: str
    status_url: str


class TrainJobStatusResponse(BaseModel):
    job_id: str
    model_name: str
    status: str  # queued, running, completed, failed
    epoch: int
    total_epochs: int
    loss: float
    progress: float
    metrics: dict = Field(default_factory=dict)
    error: JobErrorBody | None = None
    checkpoint_path: str | None = None


class VegetationResponse(BaseModel):
    index_name: str
    band_count: int
    scene_statistics: dict
    crown_count: int
    crown_statistics: list[dict] = Field(default_factory=list)
    distribution: list[dict] = Field(default_factory=list)
    notice: str


class TemporalChangeResponse(BaseModel):
    summary: dict
    crown_changes: list[dict] = Field(default_factory=list)
    total_change_polygons: int
    change_geojson: dict
    warnings: list[str] = Field(default_factory=list)
    notice: str

