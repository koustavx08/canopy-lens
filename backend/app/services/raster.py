"""Reading imagery: metadata, band handling, and tiling.

Everything that touches a raster file goes through here. The module answers
three questions for the rest of the pipeline:

1. What is this file? (`describe`)
2. How do I read it as RGB the engines can consume? (`read_rgb_window`)
3. In what pieces should I read it? (`iter_windows`)

It deliberately does *not* compute areas or reproject anything. Pixel-to-metre
conversion lives in `geo.py`, in one place.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import ColorInterp
from rasterio.windows import Window

from ..errors import InvalidRasterError, UnsupportedFileError

log = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".tif", ".tiff", ".geotiff", ".png", ".jpg", ".jpeg"}

# Longest edge of the decimated overview used for whole-raster statistics such
# as the valid-data fraction. Large enough to be representative, small enough
# to read instantly on a multi-gigapixel mosaic.
OVERVIEW_MAX_EDGE = 1024


@dataclass(frozen=True)
class RasterInfo:
    """Everything known about the source imagery, read from the file itself."""

    path: Path
    driver: str
    width: int
    height: int
    band_count: int
    dtype: str
    nodata: float | None
    crs_wkt: str | None
    crs_epsg: int | None
    crs_name: str | None
    transform: tuple[float, float, float, float, float, float]
    resolution: tuple[float, float] | None
    bounds: tuple[float, float, float, float] | None
    crs_units: str | None
    has_alpha: bool
    warnings: list[str] = field(default_factory=list)

    @property
    def is_georeferenced(self) -> bool:
        """True when the raster can be placed on the Earth.

        Both parts are required. A CRS with an identity transform (common in
        PNGs that carry a stray projection tag) locates nothing, and a
        transform with no CRS says how big a pixel is in unknown units.
        """
        return self.crs_wkt is not None and not self._transform_is_identity()

    def _transform_is_identity(self) -> bool:
        a, b, c, d, e, f = self.transform
        return (a, b, c, d, e, f) == (1.0, 0.0, 0.0, 0.0, 1.0, 0.0)

    @property
    def pixel_count(self) -> int:
        return self.width * self.height


def validate_extension(path: Path) -> None:
    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise UnsupportedFileError(
            f"{path.suffix or 'This file'} is not a supported imagery format.",
            detail={
                "supported": sorted(SUPPORTED_EXTENSIONS),
                "received": path.suffix.lower(),
            },
        )


def describe(path: Path) -> RasterInfo:
    """Open a raster and report what it actually contains.

    Raises InvalidRasterError rather than letting a GDAL exception escape, so
    a corrupt upload produces a message a user can act on.
    """
    validate_extension(path)
    if not path.exists():
        raise InvalidRasterError(f"Imagery file not found: {path.name}")

    warnings: list[str] = []
    try:
        with rasterio.open(path) as src:
            crs = src.crs
            epsg = None
            crs_name = None
            units = None
            if crs is not None:
                try:
                    epsg = crs.to_epsg()
                except Exception:  # pragma: no cover - malformed CRS
                    epsg = None
                crs_name = getattr(crs, "name", None)
                units = _linear_units(crs)

            has_alpha = ColorInterp.alpha in (src.colorinterp or ())
            transform = tuple(src.transform)[:6]
            resolution = (abs(transform[0]), abs(transform[4]))
            bounds = tuple(src.bounds) if crs is not None else None

            info = RasterInfo(
                path=path,
                driver=src.driver,
                width=src.width,
                height=src.height,
                band_count=src.count,
                dtype=str(src.dtypes[0]) if src.dtypes else "unknown",
                nodata=src.nodata,
                crs_wkt=crs.to_wkt() if crs is not None else None,
                crs_epsg=epsg,
                crs_name=crs_name,
                transform=transform,  # type: ignore[arg-type]
                resolution=resolution,
                bounds=bounds,  # type: ignore[arg-type]
                crs_units=units,
                has_alpha=has_alpha,
                warnings=warnings,
            )
    except InvalidRasterError:
        raise
    except Exception as exc:
        raise InvalidRasterError(
            f"Could not read {path.name} as imagery: {exc}",
            detail={"file": path.name},
        ) from exc

    if info.band_count < 3:
        raise InvalidRasterError(
            "CanopyLens needs colour imagery: crowns are found from the "
            f"relationship between the red, green and blue bands. {path.name} "
            f"has {info.band_count} band(s).",
            detail={"band_count": info.band_count, "required": 3},
        )
    if not info.is_georeferenced:
        warnings.append(
            "This image carries no usable georeferencing, so physical areas in "
            "square metres cannot be calculated. Crown sizes are reported in "
            "pixels only."
        )
    return info


def _linear_units(crs) -> str | None:
    """Name of the CRS's horizontal unit, e.g. 'metre' or 'degree'."""
    try:
        from pyproj import CRS as PyprojCRS

        axes = PyprojCRS.from_user_input(crs.to_wkt()).axis_info
        return axes[0].unit_name if axes else None
    except Exception:  # pragma: no cover - exotic or broken CRS definitions
        return None


def iter_windows(
    width: int,
    height: int,
    tile_size: int,
    overlap: int,
) -> list[Window]:
    """Tile a raster into overlapping windows.

    Overlap exists so that a crown sitting on a tile boundary is seen whole by
    at least one tile; the duplicate detections that creates are removed later
    by non-maximum suppression. Edge tiles are shifted back inside the raster
    rather than truncated, which keeps every tile the size the detector expects.
    """
    if tile_size <= 0:
        raise ValueError("tile_size must be positive")
    if overlap < 0:
        raise ValueError("overlap must not be negative")
    if overlap >= tile_size:
        raise ValueError(
            f"overlap ({overlap}) must be smaller than tile_size ({tile_size}); "
            "otherwise tiling never advances"
        )

    step = tile_size - overlap

    def offsets(extent: int) -> list[int]:
        if extent <= tile_size:
            return [0]
        found = []
        position = 0
        while position + tile_size < extent:
            found.append(position)
            position += step
        found.append(extent - tile_size)  # final tile flush with the far edge
        return found

    windows = []
    for row in offsets(height):
        for col in offsets(width):
            windows.append(
                Window(
                    col_off=col,
                    row_off=row,
                    width=min(tile_size, width),
                    height=min(tile_size, height),
                )
            )
    return windows


def plan_tiling(info: RasterInfo, tile_size: int, overlap: int, max_pixels: int) -> list[Window]:
    """Decide whether to tile at all, and return the windows to process."""
    if info.pixel_count <= max_pixels:
        return [Window(col_off=0, row_off=0, width=info.width, height=info.height)]
    return iter_windows(info.width, info.height, tile_size, overlap)


def to_uint8(array: np.ndarray, warnings: list[str] | None = None) -> np.ndarray:
    """Scale an arbitrary-dtype band stack to uint8.

    The vegetation index works on channel ratios and so is dtype-independent,
    but the pretrained detector expects 8-bit imagery. Conversions follow the
    dtype's declared range where there is one; anything else gets a percentile
    stretch, and that is recorded as a warning because a stretch changes the
    pixel values the model sees.
    """
    if array.dtype == np.uint8:
        return array
    data = array.astype(np.float32)

    if array.dtype == np.uint16:
        return np.clip(data / 257.0, 0, 255).astype(np.uint8)
    if np.issubdtype(array.dtype, np.floating):
        peak = float(np.nanmax(data)) if data.size else 0.0
        if peak <= 1.0:
            return np.clip(data * 255.0, 0, 255).astype(np.uint8)
        if peak <= 255.0:
            return np.clip(data, 0, 255).astype(np.uint8)

    finite = data[np.isfinite(data)]
    if finite.size == 0:
        return np.zeros(array.shape, np.uint8)
    low, high = np.percentile(finite, (2.0, 98.0))
    if high <= low:
        return np.zeros(array.shape, np.uint8)
    if warnings is not None:
        message = (
            f"Imagery is {array.dtype}, not 8-bit. Values were contrast-stretched "
            f"from the 2nd-98th percentile ({low:.4g}-{high:.4g}) before detection, "
            "which can affect results."
        )
        if message not in warnings:
            warnings.append(message)
    return np.clip((data - low) * (255.0 / (high - low)), 0, 255).astype(np.uint8)


def read_rgb_window(
    src,
    window: Window,
    warnings: list[str] | None = None,
) -> np.ndarray:
    """Read one window as an (H, W, 3) or (H, W, 4) uint8 array.

    A fourth channel is appended when the raster has an alpha band or a nodata
    value, carrying per-pixel validity. Engines use it to avoid scoring blank
    padding as though it were imagery.
    """
    bands = src.read((1, 2, 3), window=window, boundless=False)
    rgb = to_uint8(np.transpose(bands, (1, 2, 0)), warnings)

    validity = None
    colorinterp = src.colorinterp or ()
    if ColorInterp.alpha in colorinterp:
        alpha_index = list(colorinterp).index(ColorInterp.alpha) + 1
        validity = src.read(alpha_index, window=window) > 0
    elif src.nodata is not None:
        validity = src.read_masks(1, window=window) > 0

    if validity is None:
        return rgb
    return np.dstack([rgb, (validity.astype(np.uint8) * 255)])


def valid_data_mask(src, max_edge: int = OVERVIEW_MAX_EDGE) -> tuple[np.ndarray, float]:
    """Decimated validity mask for the whole raster, plus its scale factor.

    Returned at reduced resolution because it is used only for the fraction of
    the scene that carries real data; reading a full-resolution mask of a large
    mosaic would cost far more than the answer is worth.
    """
    longest = max(src.width, src.height)
    scale = min(1.0, max_edge / longest) if longest else 1.0
    out_height = max(1, int(round(src.height * scale)))
    out_width = max(1, int(round(src.width * scale)))
    mask = src.read_masks(1, out_shape=(out_height, out_width))
    return mask > 0, scale
