"""Coordinate systems, reprojection, and area.

This is the only module in CanopyLens that converts a pixel into a metre, and
the rules it enforces are the ones that decide whether the numbers the user
sees are real:

**Area is never computed from degrees.** Latitude and longitude are angles. A
degree of longitude is about 111 km at the equator and about 0 at the pole, so
`shapely.area` on a WGS84 polygon returns square degrees, which is not a unit
of area at all. Every area in this application is measured after reprojecting
the geometry into a projected CRS whose units are metres.

**The projected CRS is chosen for the data, not fixed.** Web Mercator
(EPSG:3857) is the obvious wrong answer here: it is conformal, not equal-area,
and inflates area by 1/cos^2(latitude) - roughly 40% at 30 degrees and 300% at
60. `metric_crs_for` picks a UTM zone for a normal aerial scene and a local
equal-area projection for anything wide enough that a single zone would
distort.

**Invalid geometry is repaired, not ignored.** Watershed contours can
self-intersect where a crown pinches; `make_valid` fixes them, and anything
that cannot be repaired into a polygon is dropped rather than contributing a
meaningless area.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from pyproj import CRS, Transformer
from shapely.geometry import MultiPolygon, Polygon, box, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as shapely_transform

log = logging.getLogger(__name__)

WGS84 = CRS.from_epsg(4326)

# Beyond this span a single UTM zone is the wrong tool and an equal-area
# projection centred on the data is used instead. UTM zones are 6 degrees wide.
MAX_UTM_SPAN_DEGREES = 6.0

# UTM is undefined above these latitudes; polar imagery gets a polar
# stereographic CRS instead.
UTM_LATITUDE_LIMIT = 84.0


# --- CRS selection -------------------------------------------------------


def is_metric(crs: CRS) -> bool:
    """True when the CRS is projected and its horizontal unit is the metre."""
    if not crs.is_projected:
        return False
    axes = crs.axis_info
    if not axes:
        return False
    return all(axis.unit_name in ("metre", "meter") for axis in axes[:2])


def utm_crs_for(longitude: float, latitude: float) -> CRS:
    """The UTM CRS containing a point, or a polar stereographic CRS near the poles."""
    if latitude > UTM_LATITUDE_LIMIT:
        return CRS.from_epsg(32661)  # WGS 84 / UPS North
    if latitude < -UTM_LATITUDE_LIMIT:
        return CRS.from_epsg(32761)  # WGS 84 / UPS South
    zone = int((longitude + 180.0) // 6.0) + 1
    zone = min(max(zone, 1), 60)
    return CRS.from_epsg((32600 if latitude >= 0 else 32700) + zone)


def equal_area_crs_for(longitude: float, latitude: float) -> CRS:
    """A Lambert Azimuthal Equal-Area projection centred on the data.

    Used when the extent is too wide for one UTM zone. Equal-area is the right
    property to preserve when the output is an area measurement.
    """
    return CRS.from_proj4(
        f"+proj=laea +lat_0={latitude:.6f} +lon_0={longitude:.6f} "
        "+x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs"
    )


def metric_crs_for(geometry: BaseGeometry, source_crs: CRS) -> CRS:
    """Choose the projected, metre-based CRS in which to measure `geometry`.

    A source CRS that is already projected in metres is kept: reprojecting it
    would add error without adding accuracy.
    """
    if is_metric(source_crs):
        return source_crs

    in_wgs84 = to_crs(geometry, source_crs, WGS84)
    min_lon, min_lat, max_lon, max_lat = in_wgs84.bounds
    centre_lon = (min_lon + max_lon) / 2.0
    centre_lat = (min_lat + max_lat) / 2.0

    span = max(max_lon - min_lon, max_lat - min_lat)
    if span > MAX_UTM_SPAN_DEGREES:
        log.info("Extent spans %.2f degrees; using an equal-area projection", span)
        return equal_area_crs_for(centre_lon, centre_lat)
    return utm_crs_for(centre_lon, centre_lat)


# --- Reprojection --------------------------------------------------------


@lru_cache(maxsize=64)
def _transformer(source: str, target: str) -> Transformer:
    """Cached transformer. Building one is expensive; they are reused per crown."""
    return Transformer.from_crs(
        CRS.from_user_input(source), CRS.from_user_input(target), always_xy=True
    )


def to_crs(geometry: BaseGeometry, source_crs: CRS, target_crs: CRS) -> BaseGeometry:
    """Reproject a geometry, short-circuiting when the CRSs already match."""
    if source_crs == target_crs:
        return geometry
    transformer = _transformer(source_crs.to_wkt(), target_crs.to_wkt())
    return shapely_transform(
        lambda xs, ys: transformer.transform(xs, ys),  # noqa: B023
        geometry,
    )


# --- Pixel space ---------------------------------------------------------


def pixel_ring_to_crs(
    ring: list[tuple[float, float]],
    transform: tuple[float, float, float, float, float, float],
) -> Polygon:
    """Apply a raster's affine transform to a pixel ring.

    The transform maps (column, row) to CRS coordinates, so it is applied
    directly - never by scaling with a resolution figure, which would ignore
    rotation and any north-up assumption baked into the file.
    """
    a, b, c, d, e, f = transform
    return Polygon([(a * x + b * y + c, d * x + e * y + f) for x, y in ring])


def raster_footprint(
    width: int,
    height: int,
    transform: tuple[float, float, float, float, float, float],
) -> Polygon:
    """The raster's outline as a polygon in its own CRS."""
    corners = [(0.0, 0.0), (float(width), 0.0), (float(width), float(height)), (0.0, float(height))]
    return pixel_ring_to_crs(corners, transform)


def pixel_area_m2(
    transform: tuple[float, float, float, float, float, float],
    source_crs: CRS,
    metric_crs: CRS,
    at_column: float = 0.0,
    at_row: float = 0.0,
) -> float:
    """Ground area of one pixel, in square metres.

    Measured by projecting an actual one-pixel square rather than multiplying
    resolution figures together, so it is correct for geographic CRSs (where
    the resolution is in degrees) and for rotated transforms alike.

    Used to translate the user's minimum-crown-area setting from square metres
    into the pixel units the engines work in. It is a scale conversion, not an
    area measurement: reported crown areas come from projecting each crown's
    own polygon.
    """
    ring = [
        (at_column, at_row),
        (at_column + 1.0, at_row),
        (at_column + 1.0, at_row + 1.0),
        (at_column, at_row + 1.0),
    ]
    square = pixel_ring_to_crs(ring, transform)
    return float(to_crs(square, source_crs, metric_crs).area)


# --- Validity and measurement -------------------------------------------


def repair(geometry: BaseGeometry) -> BaseGeometry | None:
    """Return a valid version of a geometry, or None if nothing usable remains.

    Self-intersecting rings are common in contour output and shapely raises on
    area for some of them, so this runs before any measurement.
    """
    if geometry is None or geometry.is_empty:
        return None
    if geometry.is_valid:
        return geometry
    try:
        from shapely import make_valid

        repaired = make_valid(geometry)
    except Exception:  # pragma: no cover - very old shapely without make_valid
        repaired = geometry.buffer(0)
    if repaired is None or repaired.is_empty:
        return None
    return repaired


def largest_polygon(geometry: BaseGeometry) -> Polygon | None:
    """Reduce a repaired geometry to the single polygon that represents a crown.

    `make_valid` can turn a pinched ring into a MultiPolygon or a collection
    containing stray lines. A crown is one object, so the largest polygon is
    kept and the slivers are discarded.
    """
    repaired = repair(geometry)
    if repaired is None:
        return None
    if isinstance(repaired, Polygon):
        return repaired if repaired.area > 0 else None

    polygons = [g for g in getattr(repaired, "geoms", []) if isinstance(g, Polygon) and g.area > 0]
    if not polygons:
        return None
    return max(polygons, key=lambda g: g.area)


def all_polygons(geometry: BaseGeometry) -> Polygon | MultiPolygon | None:
    """Reduce a repaired geometry to every polygon it contains.

    Used for boundaries, where a multi-part area is legitimate and dropping the
    smaller parts would silently shrink the region being analysed.
    """
    repaired = repair(geometry)
    if repaired is None:
        return None
    if isinstance(repaired, Polygon):
        return repaired if repaired.area > 0 else None
    polygons = [g for g in getattr(repaired, "geoms", []) if isinstance(g, Polygon) and g.area > 0]
    if not polygons:
        return None
    return polygons[0] if len(polygons) == 1 else MultiPolygon(polygons)


def area_m2(geometry: BaseGeometry, source_crs: CRS, metric_crs: CRS) -> float:
    """Area of a geometry in square metres.

    The reprojection is not optional. Calling `.area` on a geographic geometry
    returns square degrees, and treating that as square metres is the single
    most common way a tool like this produces confident nonsense.
    """
    projected = to_crs(geometry, source_crs, metric_crs)
    repaired = repair(projected)
    return float(repaired.area) if repaired is not None else 0.0


def centroid_lonlat(geometry: BaseGeometry, source_crs: CRS) -> tuple[float, float]:
    """Centroid as (longitude, latitude) in WGS84."""
    point = to_crs(geometry, source_crs, WGS84).centroid
    return float(point.x), float(point.y)


def geometry_to_geojson(geometry: BaseGeometry) -> dict:
    """Shapely geometry as a GeoJSON geometry dict."""
    return geometry.__geo_interface__


def geojson_to_geometry(payload: dict) -> BaseGeometry:
    return shape(payload)


def bounds_polygon(bounds: tuple[float, float, float, float]) -> Polygon:
    return box(*bounds)
