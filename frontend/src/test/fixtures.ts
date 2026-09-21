/**
 * Response shapes the backend really produces, for tests to build on.
 *
 * Written as a georeferenced result with every figure present, and narrowed by
 * an override where a test is about a figure being absent. That way a test
 * that says "this is what happens when there is no ground scale" contains only
 * the part that makes it so, and the twenty fields it does not care about do
 * not have to be read to find it.
 */

import type { AnalysisResult, AnalysisSummary, JobStatus } from "../types";

export function summary(overrides: Partial<AnalysisSummary> = {}): AnalysisSummary {
  return {
    tree_count: 5,
    total_canopy_area_m2: 761.12,
    total_canopy_area_hectares: 0.0761,
    total_canopy_area_px: 12178,
    sum_crown_area_m2: 761.12,
    analyzed_area_m2: 22500,
    analyzed_area_hectares: 2.25,
    analyzed_area_px: 360000,
    canopy_coverage_pct: 3.38,
    coverage_basis: "area",
    mean_crown_area_m2: 152.22,
    median_crown_area_m2: 150.1,
    min_crown_area_m2: 78.5,
    max_crown_area_m2: 201.06,
    mean_crown_area_px: 2435.6,
    median_crown_area_px: 2401.6,
    mean_confidence: 0.62,
    median_confidence: 0.61,
    confidence_distribution: [{ lower: 0.6, upper: 0.7, count: 5 }],
    engine: "classical",
    confidence_kind: "heuristic",
    confidence_threshold: 0.3,
    min_crown_area_m2_requested: 1,
    min_crown_area_px_applied: 16,
    raw_detections: 7,
    duplicates_removed: 2,
    excluded_by_boundary: 0,
    boundary_applied: false,
    ...overrides,
  };
}

export function result(overrides: Partial<AnalysisResult> = {}): AnalysisResult {
  return {
    job_id: "job-1",
    status: "completed",
    image_metadata: {
      filename: "scene.tif",
      driver: "GTiff",
      width_px: 600,
      height_px: 600,
      band_count: 3,
      dtype: "uint8",
      nodata: null,
      has_alpha: false,
      georeferenced: true,
      source_crs: "EPSG:32617",
      crs_name: "WGS 84 / UTM zone 17N",
      measurement_crs: "EPSG:32617",
      pixel_area_m2: 0.0625,
      resolution: [0.25, 0.25],
      bounds: [-80.1, 25.7, -80.09, 25.71],
    },
    boundary_metadata: null,
    summary: summary(),
    detections: [],
    processing_time: { seconds: 1.4, pipeline_seconds: 1.3, tiles_processed: 1 },
    warnings: [],
    limitations: { notice: "Estimates, not a survey.", items: ["Crowns are estimated."] },
    downloads: { csv: "/api/results/job-1/csv", geojson: "/api/results/job-1/geojson" },
    ...overrides,
  };
}

export function job(overrides: Partial<JobStatus> = {}): JobStatus {
  return {
    job_id: "job-1",
    status: "running",
    stage: "detecting",
    progress: 0.4,
    image_name: "scene.tif",
    boundary_name: null,
    created_at: "2026-01-01T00:00:00Z",
    started_at: "2026-01-01T00:00:01Z",
    finished_at: null,
    processing_time_seconds: null,
    error: null,
    ...overrides,
  } as JobStatus;
}
