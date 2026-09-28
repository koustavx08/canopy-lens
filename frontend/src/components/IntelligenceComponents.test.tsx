import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ModelEvaluationDashboard } from "./ModelEvaluationDashboard";
import { TemporalChangePanel } from "./TemporalChangePanel";
import { UncertaintyIndicator } from "./UncertaintyIndicator";
import { VegetationPanel } from "./VegetationPanel";

// Mock api
vi.mock("../lib/api", () => ({
  fetchModels: vi.fn().mockResolvedValue([
    {
      id: "deepforest_neon",
      name: "deepforest_neon",
      architecture: "retinanet",
      version: "1.0.0",
      description: "NEON benchmark model",
      created_at: "2026-01-01T00:00:00",
      status: "ready",
      is_default: true,
      is_fallback: false,
    },
  ]),
  fetchBenchmarks: vi.fn().mockResolvedValue({
    models: [],
    comparison_table: [
      {
        model_name: "deepforest_neon",
        dataset_name: "neon_eval",
        map50: 0.78,
        map50_95: 0.52,
        precision_50: 0.81,
        recall_50: 0.76,
        f1_50: 0.78,
        count_mae: 3.2,
        count_rmse: 4.1,
        area_ratio: 0.98,
      },
    ],
    markdown_table: "# Benchmarks\n| Model | mAP@50 |\n| --- | --- |\n| deepforest_neon | 78.0% |",
  }),
  evaluateModel: vi.fn(),
  startTraining: vi.fn(),
  fetchTrainJob: vi.fn(),
  analyzeVegetation: vi.fn(),
  analyzeTemporalChange: vi.fn(),
}));

describe("UncertaintyIndicator", () => {
  it("renders model confidence and sharpness diagnostics", () => {
    render(
      <UncertaintyIndicator
        confidenceKind="model"
        diagnostics={{
          sharpness_variance: 145.2,
          rms_contrast: 42.1,
          is_blurry: false,
          is_low_contrast: false,
        }}
        selectedCrown={{
          id: "crown-1",
          confidence: 0.92,
          calibrated_confidence: 0.89,
          confidence_kind: "model",
          pixel_area: 500,
          area_m2: 12.5,
          centroid_x_px: 100,
          centroid_y_px: 120,
          centroid_lon: -120.1,
          centroid_lat: 38.5,
          engine: "deepforest",
          geometry: null,
          geometry_px: { type: "Polygon", coordinates: [] },
          uncertainty_flags: ["human_review_recommended"],
        }}
      />,
    );

    expect(screen.getByText(/Calibration & Diagnostics/i)).toBeInTheDocument();
    expect(screen.getByText(/Calibrated Model/i)).toBeInTheDocument();
    expect(screen.getByText(/145.2/i)).toBeInTheDocument();
    expect(screen.getByText(/89.0%/i)).toBeInTheDocument();
    expect(screen.getByText(/human review recommended/i)).toBeInTheDocument();
  });
});

describe("ModelEvaluationDashboard", () => {
  it("renders benchmark table and model registry cards after loading", async () => {
    render(<ModelEvaluationDashboard />);

    // Wait for data load
    const heading = await screen.findByText(/Model Registry & Evaluation Benchmarks/i);
    expect(heading).toBeInTheDocument();

    const modelName = await screen.findByText("deepforest_neon");
    expect(modelName).toBeInTheDocument();

    expect(screen.getByText(/78.0%/i)).toBeInTheDocument();
    expect(screen.getByText(/Run Ground-Truth Evaluation/i)).toBeInTheDocument();
    expect(screen.getByText(/Domain-Specific Fine-Tuning/i)).toBeInTheDocument();
  });
});

describe("VegetationPanel", () => {
  it("renders index selector and honesty notice", () => {
    render(<VegetationPanel currentImage={null} currentDetections={[]} />);

    expect(
      screen.getByText(/Multispectral & RGB Vegetation Health Analysis/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/VARI — Visible Atmospherically Resistant Index/i)).toBeInTheDocument();
    expect(screen.getByText(/No active crown detections; whole-scene statistics will be calculated./i)).toBeInTheDocument();
  });
});

describe("TemporalChangePanel", () => {
  it("renders epoch configuration and thresholds", () => {
    render(<TemporalChangePanel currentDetections={[]} />);

    expect(screen.getByText(/Multi-Temporal Canopy Change Detection/i)).toBeInTheDocument();
    expect(screen.getByText(/T1: Baseline Epoch/i)).toBeInTheDocument();
    expect(screen.getByText(/T2: Monitoring Epoch/i)).toBeInTheDocument();
    expect(screen.getByText(/Temporal IoU Matching Threshold/i)).toBeInTheDocument();
  });
});
