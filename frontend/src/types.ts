/** Shapes mirrored from the FastAPI response models in backend/app/schemas.py. */

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
}

export interface ProcessingLimits {
  max_upload_mb: number;
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
