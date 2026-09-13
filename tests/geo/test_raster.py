"""Reading imagery: metadata, tiling windows, and dtype handling."""

from __future__ import annotations

import numpy as np
import pytest

from app.errors import InvalidRasterError, UnsupportedFileError
from app.services import raster

from ..conftest import PIXEL_METRES, SCENE_SIZE, draw_scene, write_raster


class TestDescribe:
    def test_a_geotiff_reports_its_real_metadata(self, utm_raster):
        info = raster.describe(utm_raster)
        assert info.width == SCENE_SIZE
        assert info.height == SCENE_SIZE
        assert info.band_count == 3
        assert info.dtype == "uint8"
        assert info.crs_epsg == 32617
        assert info.resolution == pytest.approx((PIXEL_METRES, PIXEL_METRES))
        assert info.crs_units == "metre"
        assert info.is_georeferenced

    def test_bounds_match_the_transform(self, utm_raster):
        info = raster.describe(utm_raster)
        extent = SCENE_SIZE * PIXEL_METRES
        min_x, min_y, max_x, max_y = info.bounds
        assert max_x - min_x == pytest.approx(extent)
        assert max_y - min_y == pytest.approx(extent)

    def test_a_plain_png_is_reported_as_not_georeferenced(self, plain_png):
        info = raster.describe(plain_png)
        assert info.crs_wkt is None
        assert not info.is_georeferenced
        # The user has to be told, not left to infer it from a missing field.
        assert any("georeferencing" in w for w in info.warnings)

    def test_a_crs_without_a_transform_is_not_georeferenced(self, tmp_path, scene_array):
        # A CRS tag with an identity transform locates nothing. Trusting the
        # CRS alone would place the imagery at the projection's origin.
        import rasterio

        path = tmp_path / "crs_only.tif"
        with rasterio.open(
            path,
            "w",
            driver="GTiff",
            height=SCENE_SIZE,
            width=SCENE_SIZE,
            count=3,
            dtype="uint8",
            crs="EPSG:32617",
        ) as dst:
            dst.write(scene_array)
        assert not raster.describe(path).is_georeferenced

    def test_a_single_band_raster_is_refused_with_a_reason(self, tmp_path):
        grey = np.full((1, 64, 64), 128, np.uint8)
        path = write_raster(tmp_path / "grey.tif", grey)
        with pytest.raises(InvalidRasterError) as caught:
            raster.describe(path)
        # Crowns are found from the relationship between R, G and B; one band
        # cannot express greenness at all.
        assert "band" in str(caught.value).lower()

    def test_an_unsupported_extension_is_refused(self, tmp_path):
        path = tmp_path / "data.csv"
        path.write_text("not imagery", encoding="utf-8")
        with pytest.raises(UnsupportedFileError):
            raster.describe(path)

    def test_a_corrupt_file_produces_an_error_not_a_crash(self, tmp_path):
        path = tmp_path / "broken.tif"
        path.write_bytes(b"II*\x00 this is not a real tiff")
        with pytest.raises(InvalidRasterError):
            raster.describe(path)

    def test_a_missing_file_is_reported(self, tmp_path):
        with pytest.raises(InvalidRasterError):
            raster.describe(tmp_path / "absent.tif")


class TestTilingWindows:
    def test_a_raster_smaller_than_one_tile_yields_a_single_window(self):
        windows = raster.iter_windows(500, 400, tile_size=800, overlap=160)
        assert len(windows) == 1
        assert (windows[0].col_off, windows[0].row_off) == (0, 0)

    def test_windows_step_by_tile_size_minus_overlap(self):
        windows = raster.iter_windows(2000, 800, tile_size=800, overlap=160)
        columns = sorted({w.col_off for w in windows})
        assert columns[1] - columns[0] == 800 - 160

    def test_neighbouring_windows_actually_overlap(self):
        # This is the whole point: a crown on a seam must be seen whole by at
        # least one tile.
        windows = raster.iter_windows(2000, 800, 800, 160)
        columns = sorted({w.col_off for w in windows})
        for left, right in zip(columns, columns[1:]):
            assert right < left + 800

    def test_the_last_window_is_flush_with_the_far_edge(self):
        # Shifted back inside the raster rather than truncated, so every tile
        # is the size the detector expects.
        windows = raster.iter_windows(2000, 1000, 800, 160)
        assert max(w.col_off + w.width for w in windows) == 2000
        assert max(w.row_off + w.height for w in windows) == 1000

    def test_every_pixel_is_covered_by_some_window(self):
        width, height = 1500, 1100
        windows = raster.iter_windows(width, height, 512, 128)
        covered = np.zeros((height, width), bool)
        for window in windows:
            covered[
                window.row_off : window.row_off + window.height,
                window.col_off : window.col_off + window.width,
            ] = True
        assert covered.all()

    def test_no_window_runs_past_the_raster(self):
        for window in raster.iter_windows(1500, 1100, 512, 128):
            assert window.col_off + window.width <= 1500
            assert window.row_off + window.height <= 1100

    def test_an_overlap_as_large_as_the_tile_is_rejected(self):
        # Tiling would never advance, producing an infinite loop.
        with pytest.raises(ValueError):
            raster.iter_windows(2000, 2000, tile_size=800, overlap=800)

    @pytest.mark.parametrize(("tile_size", "overlap"), [(0, 0), (-1, 0), (800, -5)])
    def test_nonsensical_tiling_parameters_are_rejected(self, tile_size, overlap):
        with pytest.raises(ValueError):
            raster.iter_windows(1000, 1000, tile_size, overlap)


class TestTilingPlan:
    def test_a_small_raster_is_processed_in_one_pass(self, utm_raster):
        info = raster.describe(utm_raster)
        windows = raster.plan_tiling(info, 800, 160, max_pixels=4_000_000)
        assert len(windows) == 1
        assert windows[0].width == info.width

    def test_a_large_raster_is_tiled(self, utm_raster):
        info = raster.describe(utm_raster)
        windows = raster.plan_tiling(info, 200, 50, max_pixels=1000)
        assert len(windows) > 1


class TestDtypeConversion:
    def test_uint8_is_passed_through_untouched(self):
        data = np.array([[0, 128, 255]], np.uint8)
        assert raster.to_uint8(data) is data

    def test_uint16_is_scaled_by_its_declared_range(self):
        data = np.array([[0, 32768, 65535]], np.uint16)
        converted = raster.to_uint8(data)
        assert converted.dtype == np.uint8
        assert converted[0, 0] == 0
        assert converted[0, 2] == 255

    def test_floats_in_zero_to_one_are_scaled_up(self):
        data = np.array([[0.0, 0.5, 1.0]], np.float32)
        converted = raster.to_uint8(data)
        assert converted[0, 0] == 0
        assert converted[0, 2] == 255

    def test_floats_already_in_zero_to_255_are_kept(self):
        data = np.array([[0.0, 120.0, 255.0]], np.float32)
        assert raster.to_uint8(data)[0, 1] == 120

    def test_an_out_of_range_dtype_is_stretched_and_the_user_is_told(self):
        # A contrast stretch changes the values the detector sees, so it is not
        # something to do silently.
        data = (np.arange(100, dtype=np.int32) * 1000).reshape(10, 10)
        warnings: list[str] = []
        converted = raster.to_uint8(data, warnings)
        assert converted.dtype == np.uint8
        assert warnings and "stretch" in warnings[0].lower()

    def test_the_same_warning_is_not_repeated_once_per_tile(self):
        data = (np.arange(100, dtype=np.int32) * 1000).reshape(10, 10)
        warnings: list[str] = []
        for _ in range(5):
            raster.to_uint8(data, warnings)
        assert len(warnings) == 1


class TestWindowReading:
    def test_a_window_reads_back_as_rgb(self, utm_raster):
        import rasterio
        from rasterio.windows import Window

        with rasterio.open(utm_raster) as src:
            image = raster.read_rgb_window(src, Window(0, 0, 64, 64))
        assert image.shape[:2] == (64, 64)
        assert image.shape[2] >= 3
        assert image.dtype == np.uint8

    def test_a_nodata_raster_carries_a_validity_channel(self, tmp_path):
        # Blank padding must be distinguishable from very dark imagery.
        scene = draw_scene(64, 64, [(32, 32, 10)])
        scene[:, :, :16] = 0
        path = write_raster(tmp_path / "nodata.tif", scene, nodata=0)

        import rasterio
        from rasterio.windows import Window

        with rasterio.open(path) as src:
            image = raster.read_rgb_window(src, Window(0, 0, 64, 64))
        assert image.shape[2] == 4
        assert not image[:, :8, 3].any()  # the padded strip is marked invalid
        assert image[:, 40:, 3].all()

    def test_the_validity_fraction_reflects_the_nodata_area(self, tmp_path):
        scene = draw_scene(100, 100, [(50, 50, 10)])
        scene[:, :, :25] = 0  # a quarter of the columns are blank
        path = write_raster(tmp_path / "quarter.tif", scene, nodata=0)

        import rasterio

        with rasterio.open(path) as src:
            mask, scale = raster.valid_data_mask(src)
        assert scale == 1.0  # small enough to read at full resolution
        assert mask.mean() == pytest.approx(0.75, abs=0.01)
