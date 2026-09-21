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

import os

# The suite polls a running job twenty times a second, which no interface does
# and the rate limit is right to refuse. Disabled for the whole suite so that
# every other module tests what it is about; the limiter itself is covered in
# test_security.py, which configures its own.
os.environ["CANOPYLENS_RATE_LIMIT_PER_MINUTE"] = "0"

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


def kml_document(polygons: list[tuple[list[tuple[float, float]], list[list[tuple[float, float]]]]]) -> str:
    """A KML document with one Placemark per (shell, holes) pair.

    Multiple placemarks is how Google Earth exports a stand split by a road or
    a river, so it is the shape the multi-polygon tests are written against.
    """
    placemarks = []
    for shell, holes in polygons:
        inner = "".join(
            "<innerBoundaryIs><LinearRing><coordinates>"
            f"{_coordinate_text(hole)}"
            "</coordinates></LinearRing></innerBoundaryIs>"
            for hole in holes
        )
        placemarks.append(
            "<Placemark><Polygon><outerBoundaryIs><LinearRing><coordinates>"
            f"{_coordinate_text(shell)}"
            "</coordinates></LinearRing></outerBoundaryIs>"
            f"{inner}</Polygon></Placemark>"
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
        f"{''.join(placemarks)}"
        "</Document></kml>"
    )


def _coordinate_text(ring: list[tuple[float, float]]) -> str:
    return " ".join(f"{lon:.10f},{lat:.10f},0" for lon, lat in ring)


def utm_rect(
    min_x: float, min_y: float, max_x: float, max_y: float
) -> list[tuple[float, float]]:
    """A rectangle in projected metres, counter-clockwise.

    Working in metres and converting once at the end keeps the test's intent -
    "75 metres by 150 metres" - visible, instead of hiding it in a set of
    decimal degrees nobody can check by eye.
    """
    return [(min_x, min_y), (max_x, min_y), (max_x, max_y), (min_x, max_y)]


def write_utm_kml(
    path: Path,
    rects: list[list[tuple[float, float]]],
    holes: list[list[tuple[float, float]]] | None = None,
) -> Path:
    """Write a KML whose polygons are given in UTM metres, converted to WGS84."""
    hole_rings = [utm_to_lonlat(hole) for hole in (holes or [])]
    polygons = [
        (utm_to_lonlat(rect), hole_rings if index == 0 else [])
        for index, rect in enumerate(rects)
    ]
    path.write_text(kml_document(polygons), encoding="utf-8")
    return path


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

# The georeferenced fixture raster covers exactly this ground: a 150 m
# square, which makes every expected boundary area in the tests arithmetic.
RASTER_MIN_X = UTM_ORIGIN_X
RASTER_MAX_X = UTM_ORIGIN_X + SCENE_SIZE * PIXEL_METRES
RASTER_MAX_Y = UTM_ORIGIN_Y
RASTER_MIN_Y = UTM_ORIGIN_Y - SCENE_SIZE * PIXEL_METRES


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


@pytest.fixture
def kmz_left_half(tmp_path: Path, left_half_kml: Path) -> Path:
    """The same western-half boundary, zipped as a KMZ."""
    import zipfile

    path = tmp_path / "left_half.kmz"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("doc.kml", left_half_kml.read_text(encoding="utf-8"))
    return path


@pytest.fixture
def two_part_kml(tmp_path: Path) -> Path:
    """Two disjoint 75 m squares: the north-west and south-east quarters.

    Together they enclose 11250 m2, the same total as the western half, but as
    two separate stands rather than one.
    """
    mid_x = RASTER_MIN_X + 75.0
    mid_y = RASTER_MIN_Y + 75.0
    return write_utm_kml(
        tmp_path / "two_parts.kml",
        [
            utm_rect(RASTER_MIN_X, mid_y, mid_x, RASTER_MAX_Y),
            utm_rect(mid_x, RASTER_MIN_Y, RASTER_MAX_X, mid_y),
        ],
    )


@pytest.fixture
def holed_kml(tmp_path: Path) -> Path:
    """The whole raster with a 20 m x 20 m clearing cut out of it.

    The clearing is centred on the smallest planted crown, so a boundary that
    honours holes must both measure 400 m2 less and drop that crown.
    """
    hole_centre_x = UTM_ORIGIN_X + 500 * PIXEL_METRES
    hole_centre_y = UTM_ORIGIN_Y - 250 * PIXEL_METRES
    return write_utm_kml(
        tmp_path / "holed.kml",
        [utm_rect(RASTER_MIN_X, RASTER_MIN_Y, RASTER_MAX_X, RASTER_MAX_Y)],
        holes=[
            utm_rect(
                hole_centre_x - 10.0,
                hole_centre_y - 10.0,
                hole_centre_x + 10.0,
                hole_centre_y + 10.0,
            )
        ],
    )


@pytest.fixture
def overhanging_kml(tmp_path: Path) -> Path:
    """A 300 m x 150 m boundary of which the imagery covers only the western half."""
    return write_utm_kml(
        tmp_path / "overhanging.kml",
        [utm_rect(RASTER_MIN_X, RASTER_MIN_Y, RASTER_MAX_X + 150.0, RASTER_MAX_Y)],
    )


@pytest.fixture
def detached_part_kml(tmp_path: Path) -> Path:
    """The western half, plus a second stand 40 km east that no imagery covers."""
    return write_utm_kml(
        tmp_path / "detached.kml",
        [
            utm_rect(RASTER_MIN_X, RASTER_MIN_Y, RASTER_MIN_X + 75.0, RASTER_MAX_Y),
            utm_rect(RASTER_MIN_X + 40_000.0, RASTER_MIN_Y, RASTER_MIN_X + 40_100.0, RASTER_MIN_Y + 100.0),
        ],
    )
