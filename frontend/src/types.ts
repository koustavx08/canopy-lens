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
}
