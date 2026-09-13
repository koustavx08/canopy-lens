"""Shared fixtures for the geospatial and pipeline tests.

The imagery here is synthetic on purpose. Tree crowns are drawn as circles of
known radius at known pixel positions, on rasters with a known CRS and pixel
size, so every assertion can be made against an arithmetic truth rather than
against whatever the code happened to produce the first time it ran. A test
that only pins current behaviour cannot tell you the behaviour is correct.

Where a tolerance appears it is because rasterising a circle and tracing its
contour genuinely loses a little area at the edge - not because the expected
value is unknown.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import rasterio
from pyproj import Transformer
from rasterio.transform import from_origin

# A UTM zone 17N origin in Ontario, and a pixel size typical of drone or NEON
# imagery. Chosen because the scene is small enough that UTM is the natural
# projection, which is what the CRS selection logic should pick.
UTM_CRS = "EPSG:32617"
UTM_ORIGIN_X = 500000.0
UTM_ORIGIN_Y = 4649776.0
PIXEL_METRES = 0.25

GROUND_RGB = (120, 110, 95)  # bare ground: brownish, low excess-green
CANOPY_RGB = (45, 135, 40)  # foliage: unambiguously green


def draw_scene(
    height: int,
    width: int,
    circles: list[tuple[int, int, int]],
) -> np.ndarray:
    """Render a (3, H, W) uint8 scene with circular 'crowns' on bare ground.

    `circles` are (centre_row, centre_col, radius_px).
    """
    image = np.zeros((3, height, width), dtype=np.uint8)
    for band, value in enumerate(GROUND_RGB):
        image[band] = value
    rows, cols = np.mgrid[0:height, 0:width]
    for centre_row, centre_col, radius in circles:
        inside = (rows - centre_row) ** 2 + (cols - centre_col) ** 2 <= radius**2
        for band, value in enumerate(CANOPY_RGB):
            image[band][inside] = value
    return image


def write_raster(
    path: Path,
    data: np.ndarray,
    crs: str | None = UTM_CRS,
    pixel_size: float = PIXEL_METRES,
    origin: tuple[float, float] = (UTM_ORIGIN_X, UTM_ORIGIN_Y),
    driver: str = "GTiff",
    nodata: float | None = None,
) -> Path:
    """Write a (3, H, W) array to a raster file, with or without georeferencing."""
    height, width = data.shape[1:]
    profile = {
        "driver": driver,
        "height": height,
        "width": width,
        "count": data.shape[0],
        "dtype": str(data.dtype),
    }
    if crs is not None:
        profile["crs"] = crs
        profile["transform"] = from_origin(origin[0], origin[1], pixel_size, pixel_size)
    if nodata is not None:
        profile["nodata"] = nodata
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(data)
    return path


def kml_polygon(corners_lonlat: list[tuple[float, float]]) -> str:
    """A minimal but valid KML document containing one polygon."""
    coordinates = " ".join(f"{lon:.10f},{lat:.10f},0" for lon, lat in corners_lonlat)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<kml xmlns="http://www.opengis.net/kml/2.2"><Document><Placemark>'
        "<Polygon><outerBoundaryIs><LinearRing><coordinates>"
        f"{coordinates}"
        "</coordinates></LinearRing></outerBoundaryIs></Polygon>"
        "</Placemark></Document></kml>"
    )


def utm_to_lonlat(points: list[tuple[float, float]], crs: str = UTM_CRS) -> list[tuple[float, float]]:
    transformer = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    return [transformer.transform(x, y) for x, y in points]


# --- fixtures ------------------------------------------------------------

# Five crowns of distinct, known radii. Their true areas in square metres are
# pi * (radius * PIXEL_METRES)^2, which the area tests assert against.
SCENE_CIRCLES = [
    (100, 100, 24),
    (140, 320, 32),
    (380, 150, 28),
    (430, 420, 36),
    (250, 500, 20),
]
SCENE_SIZE = 600


@pytest.fixture(scope="session")
def scene_array() -> np.ndarray:
    return draw_scene(SCENE_SIZE, SCENE_SIZE, SCENE_CIRCLES)


@pytest.fixture
def utm_raster(tmp_path: Path, scene_array: np.ndarray) -> Path:
    """A georeferenced GeoTIFF: 600x600 px at 0.25 m/px in UTM 17N (150 m square)."""
    return write_raster(tmp_path / "scene_utm.tif", scene_array)


@pytest.fixture
def plain_png(tmp_path: Path, scene_array: np.ndarray) -> Path:
    """The same scene with no CRS and no transform at all."""
    return write_raster(tmp_path / "scene_plain.png", scene_array, crs=None, driver="PNG")


@pytest.fixture
def expected_crown_areas_m2() -> list[float]:
    return sorted(np.pi * (radius * PIXEL_METRES) ** 2 for _, _, radius in SCENE_CIRCLES)


@pytest.fixture
def left_half_kml(tmp_path: Path) -> Path:
    """A boundary covering the western half of `utm_raster`: 75 m x 150 m."""
    half_width_m = SCENE_SIZE / 2 * PIXEL_METRES  # 75 m
    full_height_m = SCENE_SIZE * PIXEL_METRES  # 150 m
    corners = [
        (UTM_ORIGIN_X, UTM_ORIGIN_Y),
        (UTM_ORIGIN_X + half_width_m, UTM_ORIGIN_Y),
        (UTM_ORIGIN_X + half_width_m, UTM_ORIGIN_Y - full_height_m),
        (UTM_ORIGIN_X, UTM_ORIGIN_Y - full_height_m),
    ]
    path = tmp_path / "left_half.kml"
    path.write_text(kml_polygon(utm_to_lonlat(corners)), encoding="utf-8")
    return path


@pytest.fixture
def elsewhere_kml(tmp_path: Path) -> Path:
    """A perfectly valid boundary that is nowhere near the imagery."""
    path = tmp_path / "elsewhere.kml"
    path.write_text(
        kml_polygon([(10.0, 50.0), (10.1, 50.0), (10.1, 50.1), (10.0, 50.1)]),
        encoding="utf-8",
    )
    return path
