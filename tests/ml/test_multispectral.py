"""Tests for multispectral vegetation analysis and zonal crown statistics."""

from __future__ import annotations

import numpy as np
import pytest

from canopylens_ml.multispectral import (
    BandMapping,
    analyze_vegetation,
    compute_vegetation_index,
    compute_zonal_crown_statistics,
)


class TestVegetationIndices:
    def test_rgb_native_indices(self):
        # Shape (3, 50, 50): pure green foliage (low R, high G, low B)
        data = np.zeros((3, 50, 50), dtype=np.float32)
        data[0] = 30.0   # Red
        data[1] = 160.0  # Green
        data[2] = 40.0   # Blue

        # VARI = (G - R) / (G + R - B) = (160 - 30) / (160 + 30 - 40) = 130 / 150 ~ 0.866
        vari = compute_vegetation_index(data, "VARI")
        assert np.nanmean(vari) == pytest.approx(130.0 / 150.0, rel=0.01)

        # GLI = (2G - R - B) / (2G + R + B) = (320 - 70) / (320 + 70) = 250 / 390 ~ 0.641
        gli = compute_vegetation_index(data, "GLI")
        assert np.nanmean(gli) == pytest.approx(250.0 / 390.0, rel=0.01)

        # ExG
        exg = compute_vegetation_index(data, "ExG")
        assert np.nanmean(exg) > 0.3

    def test_multispectral_ndvi(self):
        # 4 bands: R, G, B, NIR
        data = np.zeros((4, 40, 40), dtype=np.float32)
        data[0] = 40.0   # Red
        data[1] = 100.0  # Green
        data[2] = 30.0   # Blue
        data[3] = 200.0  # NIR
        # NDVI = (NIR - R) / (NIR + R) = (200 - 40) / (240) = 160 / 240 = 2/3 ~ 0.667
        ndvi = compute_vegetation_index(data, "NDVI")
        assert np.nanmean(ndvi) == pytest.approx(160.0 / 240.0, rel=0.01)

    def test_ndvi_fails_honestly_on_3_band_rgb(self):
        data = np.zeros((3, 40, 40), dtype=np.float32)
        with pytest.raises(ValueError) as exc:
            compute_vegetation_index(data, "NDVI")
        assert "Near-Infrared" in str(exc.value)
        assert "VARI" in str(exc.value)

    def test_ndre_fails_without_rededge(self):
        data = np.zeros((4, 40, 40), dtype=np.float32)  # only 4 bands, missing RedEdge
        with pytest.raises(ValueError) as exc:
            compute_vegetation_index(data, "NDRE")
        assert "RedEdge" in str(exc.value)


class TestZonalStatisticsAndAnalysis:
    def test_zonal_crown_statistics(self):
        # Create a gradient grid
        grid = np.full((100, 100), 0.75, dtype=np.float32)
        grid[20:40, 20:40] = 0.90  # healthy crown

        crowns = [
            {"id": "crown_1", "polygon_px": [(20, 20), (40, 20), (40, 40), (20, 40)]},
            {"id": "crown_2", "polygon_px": [(60, 60), (80, 60), (80, 80), (60, 80)]},
        ]
        stats = compute_zonal_crown_statistics(grid, crowns)
        assert len(stats) == 2
        assert stats[0].crown_id == "crown_1"
        assert stats[0].mean == pytest.approx(0.90, rel=0.01)
        assert stats[1].crown_id == "crown_2"
        assert stats[1].mean == pytest.approx(0.75, rel=0.01)

    def test_analyze_vegetation_end_to_end(self):
        data = np.zeros((4, 80, 80), dtype=np.float32)
        data[0] = 40.0
        data[1] = 120.0
        data[2] = 30.0
        data[3] = 180.0

        crowns = [
            {"id": "c1", "polygon_px": [(10, 10), (30, 10), (30, 30), (10, 30)]}
        ]

        result = analyze_vegetation(data, index_name="auto", crowns_px=crowns)
        assert result.index_name == "NDVI"  # auto-detected 4 bands -> NDVI
        assert result.valid_pixel_count == 80 * 80
        assert len(result.crown_stats) == 1
        assert len(result.distribution_bins) == 10
        assert "relative canopy vigor" in result.limitations
