"""Tests for instance segmentation, crown geometry repair, and confidence calibration."""

from __future__ import annotations

import numpy as np
import pytest

from canopylens_ml.calibration import (
    ImageQualityDiagnostics,
    TemperatureScaler,
    assess_detection_uncertainty,
    brier_score,
    expected_calibration_error,
    reliability_diagram,
)
from canopylens_ml.segmentation import (
    ActiveContourSegmentationEngine,
    validate_and_repair_crown_polygon,
)
from canopylens_ml.types import DetectionParams


class TestGeometryValidator:
    def test_clean_polygon_preserved(self):
        square = [(10.0, 10.0), (30.0, 10.0), (30.0, 30.0), (10.0, 30.0)]
        clean, _ = validate_and_repair_crown_polygon(square)
        assert clean is not None
        assert len(clean) >= 4

    def test_bowtie_self_intersecting_polygon_repaired(self):
        # Bowtie: (0,0) -> (10,10) -> (10,0) -> (0,10)
        bowtie = [(0.0, 0.0), (10.0, 10.0), (10.0, 0.0), (0.0, 10.0)]
        clean, was_modified = validate_and_repair_crown_polygon(bowtie)
        assert was_modified
        assert clean is not None
        # Should be a simple valid polygon after repair
        from shapely.geometry import Polygon
        poly = Polygon(clean)
        assert poly.is_valid

    def test_degenerate_sliver_filtered(self):
        line = [(0.0, 0.0), (10.0, 0.0), (0.0, 0.0)]
        clean, _ = validate_and_repair_crown_polygon(line, min_area_px=5.0)
        assert clean is None


class TestActiveContourSegmentation:
    def test_segment_tile_synthetic_scene(self):
        engine = ActiveContourSegmentationEngine()
        ok, reason = engine.check()
        if not ok:
            pytest.skip(f"Active contour segmentation dependencies missing: {reason}")

        # Render 2 circular green crowns on bare ground
        image = np.zeros((200, 200, 3), dtype=np.uint8)
        image[..., 0], image[..., 1], image[..., 2] = 120, 110, 95  # ground
        rows, cols = np.mgrid[0:200, 0:200]
        # Crown 1 at (50, 50, r=25)
        image[(rows - 50) ** 2 + (cols - 50) ** 2 <= 25**2] = (45, 135, 40)
        # Crown 2 at (150, 150, r=20)
        image[(rows - 150) ** 2 + (cols - 150) ** 2 <= 20**2] = (45, 135, 40)

        params = DetectionParams(min_crown_area_px=100.0)
        crowns = engine.segment_tile(image, params)

        assert len(crowns) >= 2
        for c in crowns:
            assert c.mask is not None
            assert len(c.polygon) >= 3
            assert 0.0 <= c.confidence <= 1.0
            assert c.segmentation_method == "morphological_watershed"
            assert "circularity" in c.attributes


class TestCalibrationAndUncertainty:
    def test_ece_perfectly_calibrated(self):
        # 10 samples with 0.8 confidence and 8/10 accuracy -> ECE ~ 0.0
        confs = [0.8] * 10
        labels = [1, 1, 1, 1, 1, 1, 1, 1, 0, 0]
        ece = expected_calibration_error(confs, labels, num_bins=5)
        assert ece == pytest.approx(0.0, abs=1e-4)

    def test_brier_score(self):
        confs = [0.9, 0.1]
        labels = [1, 0]
        # (0.9 - 1)^2 + (0.1 - 0)^2 / 2 = (0.01 + 0.01) / 2 = 0.01
        score = brier_score(confs, labels)
        assert score == pytest.approx(0.01)

    def test_reliability_diagram_bins(self):
        confs = [0.1, 0.2, 0.8, 0.9]
        labels = [0, 0, 1, 1]
        bins = reliability_diagram(confs, labels, num_bins=5)
        assert len(bins) == 5
        # Total counts across bins should sum to 4
        assert sum(b.count for b in bins) == 4

    def test_temperature_scaling(self):
        scaler = TemperatureScaler()
        confs = [0.95, 0.90, 0.85, 0.70, 0.60, 0.40]
        labels = [1, 1, 0, 1, 0, 0]
        t = scaler.fit(confs, labels)
        assert t > 0.0
        calibrated = scaler.calibrate(confs)
        assert len(calibrated) == len(confs)
        assert all(0.0 <= p <= 1.0 for p in calibrated)

    def test_image_quality_diagnostics_sharp_vs_blurry(self):
        # Sharp synthetic image with high-frequency noise / edges
        sharp = np.random.randint(0, 255, (100, 100, 3), dtype=np.uint8)
        diag_sharp = ImageQualityDiagnostics.analyze(sharp)
        assert diag_sharp.sharpness_score > 50.0

        # Uniform flat blurry image
        blurry = np.full((100, 100, 3), 128, dtype=np.uint8)
        diag_blurry = ImageQualityDiagnostics.analyze(blurry)
        assert diag_blurry.is_blurry
        assert diag_blurry.sharpness_score < 1.0
        assert any("sharpness" in w.lower() for w in diag_blurry.warnings)

    def test_uncertainty_assessment_flags(self):
        # High confidence, normal size
        good = assess_detection_uncertainty(confidence=0.85, confidence_kind="model", area_px=500.0)
        assert not good.requires_human_review
        assert good.confidence_reliability == "high"

        # Low confidence
        low_conf = assess_detection_uncertainty(confidence=0.30, confidence_kind="model", area_px=500.0)
        assert low_conf.requires_human_review
        assert any("below" in r.lower() for r in low_conf.reasons)

        # Huge crown area (merged cluster)
        huge = assess_detection_uncertainty(confidence=0.90, confidence_kind="model", area_px=15000.0)
        assert huge.requires_human_review
        assert any("large" in r.lower() for r in huge.reasons)
