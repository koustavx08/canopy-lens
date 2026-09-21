/** Shapes mirrored from the FastAPI response models in backend/app/schemas.py. */

import type { Geometry } from "geojson";

export interface DependencyStatus {
  module: string;
  installed: boolean;
  version: string | null;
}

export interface EngineStatus {
  name: string;
  title: string;
  description: string;
  available: boolean;
  reason: string | null;
  produces: "polygon" | "bbox" | string;
  requires_download: boolean;
  /**
   * How to present this engine's confidence. A "heuristic" score ranks
   * candidates by shape and colour; it is not a probability and must not be
   * labelled as one. "model" is a score from a trained detector.
   */
  confidence_kind: "model" | "heuristic" | string;
}

export interface ProcessingLimits {
  max_upload_mb: number;
  max_boundary_upload_mb: number;
  tile_size: number;
  tile_overlap: number;
  max_pixels_without_tiling: number;
}

export interface DetectionDefaults {
  confidence_threshold: number;
  min_crown_area_m2: number;
  engine: string;
}

export interface Capabilities {
  version: string;
  environment: string;
  dependencies: DependencyStatus[];
  engines: EngineStatus[];
  limits: ProcessingLimits;
  defaults: DetectionDefaults;
  supported_raster_formats: string[];
  supported_boundary_formats: string[];
}

/**
 * A parsed forest boundary, as measured by the backend.
 *
 * `area_m2` is the ground the boundary encloses. It is not the analysed area
 * (the part of it the imagery covers) and not the canopy area, and the three
 * must never be shown under the same label.
 */
export interface BoundaryPreview {
  geometry: Geometry;
  area_m2: number;
  area_hectares: number;
  part_count: number;
  has_holes: boolean;
  source_format: string;
  source_crs: string;
  measurement_crs: string;
  /** [min_lon, min_lat, max_lon, max_lat] */
  bounds_wgs84: [number, number, number, number];
  /** [longitude, latitude] */
  centroid_wgs84: [number, number];
  warnings: string[];
}

export interface ApiErrorBody {
  code: string;
  message: string;
  detail: Record<string, unknown>;
}

/** A file the user picked, plus whatever the browser can tell us about it. */
export interface SelectedFile {
  file: File;
  objectUrl: string | null;
  width?: number;
  height?: number;
}

export interface DetectionSettings {
  confidenceThreshold: number;
  minCrownAreaM2: number;
  /** Engine name, or "auto" to let the server choose the best available. */
  engine: string;
}

// ---------------------------------------------------------------------------
// Analysis jobs
// ---------------------------------------------------------------------------

export interface JobAccepted {
  job_id: string;
  status: string;
  status_url: string;
  result_url: string;
}

/** Where a job has got to. Carries no detections: it is polled every second. */
export interface JobStatus {
  job_id: string;
  status: "queued" | "running" | "completed" | "failed" | "cancelled";
  stage: string;
  /** Fraction of tiles completed, not of time elapsed. */
  progress: number;
  image_name: string;
  boundary_name: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  processing_time_seconds: number | null;
  error: ApiErrorBody | null;
  result_url: string | null;
}

export interface ImageMetadata {
  filename: string;
  driver: string | null;
  width_px: number;
  height_px: number;
  band_count: number;
  dtype: string | null;
  nodata: number | null;
  has_alpha: boolean;
  /**
   * Whether the file carries a coordinate system. When false every `*_m2`
   * field in the response is null and only the pixel figures are real.
   */
  georeferenced: boolean;
  source_crs: string | null;
  crs_name: string | null;
  measurement_crs: string | null;
  pixel_area_m2: number | null;
  resolution: [number, number] | null;
  bounds: [number, number, number, number] | null;
}

/** The boundary as the analysis actually applied it. */
export interface BoundaryMetadata {
  source_format: string;
  part_count: number;
  has_holes: boolean;
  area_m2: number | null;
  area_within_imagery_m2: number | null;
  imagery_covers_pct: number | null;
  detections_excluded: number;
  geometry: Geometry | null;
  [key: string]: unknown;
}

export interface ConfidenceBin {
  lower: number;
  upper: number;
  count: number;
}

export interface AnalysisSummary {
  tree_count: number;
  total_canopy_area_m2: number | null;
  total_canopy_area_hectares: number | null;
  total_canopy_area_px: number;
  sum_crown_area_m2: number | null;
  analyzed_area_m2: number | null;
  analyzed_area_hectares: number | null;
  analyzed_area_px: number;
  canopy_coverage_pct: number | null;
  /**
   * What the coverage percentage divides by: real ground area, image pixels,
   * or nothing at all. A pixel-basis figure is a true ratio of the image and
   * must not be presented as a ground measurement.
   */
  coverage_basis: "area" | "pixel" | "unavailable" | string;
  mean_crown_area_m2: number | null;
  median_crown_area_m2: number | null;
  min_crown_area_m2: number | null;
  max_crown_area_m2: number | null;
  mean_crown_area_px: number | null;
  median_crown_area_px: number | null;
  mean_confidence: number | null;
  median_confidence: number | null;
  confidence_distribution: ConfidenceBin[];
  engine: string;
  confidence_kind: "model" | "heuristic" | string;
  confidence_threshold: number;
  min_crown_area_m2_requested: number | null;
  min_crown_area_px_applied: number | null;
  raw_detections: number;
  duplicates_removed: number;
  excluded_by_boundary: number;
  boundary_applied: boolean;
}

/** One detected crown. */
export interface Detection {
  id: string;
  confidence: number;
  confidence_kind: string;
  pixel_area: number;
  area_m2: number | null;
  centroid_x_px: number;
  centroid_y_px: number;
  centroid_lon: number | null;
  centroid_lat: number | null;
  engine: string;
  /** WGS84. Null for imagery with no georeferencing. */
  geometry: Geometry | null;
  /** Image pixel coordinates. Always present, so crowns can always be drawn. */
  geometry_px: Geometry;
}

export interface AnalysisResult {
  job_id: string;
  status: string;
  image_metadata: ImageMetadata;
  boundary_metadata: BoundaryMetadata | null;
  summary: AnalysisSummary;
  detections: Detection[];
  processing_time: {
    seconds: number | null;
    pipeline_seconds: number | null;
    tiles_processed: number | null;
  };
  /** Specific to this image and boundary. */
  warnings: string[];
  /** The standing limits of the method, identical on every result. */
  limitations: { notice: string; items: string[] };
  downloads: { csv: string; geojson: string };
}

/**
 * What the user sees while an analysis runs.
 *
 * Derived from the job's own stage, never from a timer: a phase that advances
 * on a clock is a progress bar that lies.
 */
export type AnalysisPhase =
  | "idle"
  | "uploading"
  | "processing"
  | "post-processing"
  | "complete"
  | "error";
