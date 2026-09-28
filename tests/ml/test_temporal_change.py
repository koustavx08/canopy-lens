"""Tests for temporal canopy change detection and crown tracking."""

from __future__ import annotations

import json
import pytest

from canopylens_ml.temporal import (
    detect_canopy_change,
    export_change_csv,
    export_change_geojson,
)


class TestTemporalChangeDetection:
    def test_persistent_lost_recruited_and_disturbed(self):
        # T1 crowns:
        # A: (10,10) to (30,30) - persistent
        # B: (50,50) to (70,70) - disturbed (large area shrink or index drop)
        # C: (100,100) to (120,120) - lost in T2
        t1 = [
            {"id": "c_A", "polygon": [(10, 10), (30, 10), (30, 30), (10, 30)], "area_m2": 40.0, "vegetation_index": 0.65},
            {"id": "c_B", "polygon": [(50, 50), (70, 50), (70, 70), (50, 70)], "area_m2": 40.0, "vegetation_index": 0.60},
            {"id": "c_C", "polygon": [(100, 100), (120, 100), (120, 120), (100, 120)], "area_m2": 40.0, "vegetation_index": 0.55},
        ]

        # T2 crowns:
        # A: (10,10) to (30,30) - persistent
        # B: (52,52) to (60,60) - shrunk to area 10.0 and index 0.35 -> disturbed
        # D: (200,200) to (220,220) - new recruit
        t2 = [
            {"id": "c_A2", "polygon": [(10, 10), (30, 10), (30, 30), (10, 30)], "area_m2": 40.0, "vegetation_index": 0.66},
            {"id": "c_B2", "polygon": [(52, 52), (60, 52), (60, 60), (52, 60)], "area_m2": 10.0, "vegetation_index": 0.35},
            {"id": "c_D", "polygon": [(200, 200), (220, 200), (220, 220), (200, 220)], "area_m2": 35.0, "vegetation_index": 0.70},
        ]

        result = detect_canopy_change(t1, t2)

        assert result.t1_crown_count == 3
        assert result.t2_crown_count == 3
        assert result.persistent_count == 1
        assert result.disturbed_crown_count == 1
        assert result.lost_crown_count == 1
        assert result.recruited_crown_count == 1

        assert result.canopy_loss_m2 == pytest.approx(40.0)  # Crown C
        assert result.canopy_gain_m2 == pytest.approx(35.0)  # Crown D
        assert result.canopy_disturbance_m2 == pytest.approx(30.0)  # Crown B shrunk from 40 to 10

        assert len(result.change_polygons) >= 3

    def test_export_change_geojson(self):
        t1 = [{"id": "c1", "polygon": [(0, 0), (10, 0), (10, 10), (0, 10)], "area_m2": 25.0}]
        t2 = []  # tree lost
        result = detect_canopy_change(t1, t2)

        geo = export_change_geojson(result)
        assert geo["type"] == "FeatureCollection"
        assert len(geo["features"]) == 1
        assert geo["features"][0]["properties"]["change_type"] == "loss"
        assert "notice" in geo["properties"]

    def test_export_change_csv(self):
        t1 = []
        t2 = [{"id": "c2", "polygon": [(0, 0), (10, 0), (10, 10), (0, 10)], "area_m2": 25.0}]
        result = detect_canopy_change(t1, t2)

        csv_text = export_change_csv(result)
        assert "t1_id,t2_id,status" in csv_text
        assert "recruited" in csv_text
