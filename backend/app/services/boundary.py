"""Parsing, repairing and measuring KML, KMZ and GeoJSON boundaries.

KML is parsed directly from its XML rather than through GDAL's KML driver.
That driver is optional in a GDAL build and its availability varies between
wheels and container images, so depending on it would make boundary support
work on the developer's machine and fail in production. KML is a small, well
specified XML format and the subset that matters here - polygons, holes and
multi-geometries - is short to read directly.

Three rules shape this module:

**The coordinate system is asserted, not guessed.** KML coordinates are
longitude, latitude and optional altitude in WGS84 by the OGC KML
specification, and GeoJSON is WGS84 by RFC 7946. Neither is inferred from the
magnitude of the numbers in the file. The one exception is a GeoJSON that
carries the pre-RFC 7946 `crs` member: QGIS and ArcGIS still emit those, and
silently reading a projected easting as a longitude would put the boundary in
the Gulf of Guinea. When that member is present it is honoured and a warning
says so.

**Broken geometry is repaired before it is used, not after.** A hand-drawn
boundary that crosses itself is a bow-tie, and `unary_union` raises on some of
them. Each polygon is repaired individually first, so one bad ring in a
twenty-stand KML costs that ring and not the whole file.

**Area is measured, never assumed.** `measure` reprojects the boundary into a
metric CRS chosen for its own extent before taking an area. The boundary's area
is a different quantity from the analysed area and from the detected crown
area, and it is reported separately so the three cannot be confused.
"""

from __future__ import annotations

import json
import logging
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree

from pyproj import CRS
from shapely.geometry import MultiPolygon, Polygon, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from ..errors import InvalidBoundaryError, UnsupportedFileError
from .geo import WGS84, all_polygons, area_m2, centroid_lonlat, metric_crs_for, repair, to_crs

log = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".kml", ".kmz", ".geojson", ".json"}

# Tags that describe a shape of some kind. Used only to tell "this file has no
# geometry in it at all" apart from "this file has geometry, but none of it
# encloses an area", because those are different mistakes with different fixes.
_GEOMETRY_TAGS = {
    "Polygon",
    "LinearRing",
    "LineString",
    "Point",
    "MultiGeometry",
    "Model",
    "Track",
}


@dataclass(frozen=True)
class LoadedBoundary:
    """A boundary as read from a file, before it meets any imagery.

    `geometry` is always a Polygon or MultiPolygon in `crs`. `part_count` is
    the number of separate areas it contains: a forest split by a road is two
    stands in one file, and the user should see that reflected rather than
    discover it later.
    """

    geometry: Polygon | MultiPolygon
    crs: CRS
    part_count: int
    source_format: str
    warnings: list[str] = field(default_factory=list)

    @property
    def has_holes(self) -> bool:
        parts = getattr(self.geometry, "geoms", [self.geometry])
        return any(len(part.interiors) > 0 for part in parts)


@dataclass(frozen=True)
class BoundaryMeasurement:
    """The boundary's own geometry, measured.

    `area_m2` is the ground the boundary encloses, holes excluded. It is *not*
    the analysed area (which is the boundary clipped to the imagery, minus
    nodata) and it is *not* the canopy area. Keeping the three apart is the
    whole point of reporting this separately.
    """

    area_m2: float
    part_count: int
    has_holes: bool
    measurement_crs: str
    bounds_wgs84: tuple[float, float, float, float]
    centroid_wgs84: tuple[float, float]
    source_format: str
    warnings: list[str] = field(default_factory=list)


# --- KML -----------------------------------------------------------------

# KML documents may or may not declare the namespace, and several versions are
# in circulation. Matching on the local tag name sidesteps the whole problem.


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _find_all(element: ElementTree.Element, name: str) -> list[ElementTree.Element]:
    """Find descendants by local tag name, ignoring namespace."""
    return [node for node in element.iter() if _local_name(node.tag) == name]


def _direct_children(element: ElementTree.Element, name: str) -> list[ElementTree.Element]:
    return [node for node in element if _local_name(node.tag) == name]


def parse_coordinates(text: str) -> list[tuple[float, float]]:
    """Parse a KML <coordinates> block into (longitude, latitude) pairs.

    The altitude component, when present, is discarded: CanopyLens measures
    ground area and a boundary's height above the ellipsoid is not part of it.
    """
    points: list[tuple[float, float]] = []
    for token in text.replace("\n", " ").replace("\t", " ").split():
        parts = token.split(",")
        if len(parts) < 2:
            continue
        try:
            points.append((float(parts[0]), float(parts[1])))
        except ValueError:
            continue
    return points


def _polygon_from_kml(node: ElementTree.Element) -> Polygon | None:
    """Build a polygon from a KML <Polygon>, honouring inner boundaries (holes)."""
    outer_nodes = _find_all(node, "outerBoundaryIs")
    if not outer_nodes:
        return None
    outer_coords = _find_all(outer_nodes[0], "coordinates")
    if not outer_coords or not outer_coords[0].text:
        return None
    shell = parse_coordinates(outer_coords[0].text)
    if len(shell) < 3:
        return None

    holes = []
    for inner in _find_all(node, "innerBoundaryIs"):
        inner_coords = _find_all(inner, "coordinates")
        if inner_coords and inner_coords[0].text:
            ring = parse_coordinates(inner_coords[0].text)
            if len(ring) >= 3:
                holes.append(ring)

    return Polygon(shell, holes)


def parse_kml(text: str) -> BaseGeometry:
    """Extract every polygon in a KML document as one geometry in WGS84."""
    if not text.strip():
        raise InvalidBoundaryError(
            "The boundary file is empty. Upload a KML or KMZ that contains the "
            "forest boundary you want to analyse.",
        )

    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError as exc:
        raise InvalidBoundaryError(
            f"The boundary file is not valid XML: {exc}",
        ) from exc

    polygons: list[Polygon] = []
    for node in _find_all(root, "Polygon"):
        polygon = _polygon_from_kml(node)
        if polygon is not None:
            polygons.append(polygon)

    if not polygons:
        # Some exports draw an area as a closed LineString or a bare
        # LinearRing. Those describe the same region, so they are accepted when
        # no true Polygon is present.
        for name in ("LinearRing", "LineString"):
            for node in _find_all(root, name):
                coords_nodes = _direct_children(node, "coordinates") or _find_all(
                    node, "coordinates"
                )
                if not coords_nodes or not coords_nodes[0].text:
                    continue
                ring = parse_coordinates(coords_nodes[0].text)
                if len(ring) >= 3:
                    polygons.append(Polygon(ring))
            if polygons:
                break

    if not polygons:
        raise InvalidBoundaryError(_no_area_message(_kml_has_any_geometry(root)))

    return _combine(polygons)


def _kml_has_any_geometry(root: ElementTree.Element) -> bool:
    return any(_local_name(node.tag) in _GEOMETRY_TAGS for node in root.iter())


def _no_area_message(had_geometry: bool) -> str:
    """Explain the failure in terms of what the user would have to change."""
    if had_geometry:
        return (
            "The boundary file contains geometry, but none of it encloses an "
            "area. CanopyLens needs a polygon describing the stand to analyse; "
            "placemark pins and unclosed paths do not define one. In Google "
            "Earth, use the polygon tool rather than the placemark or path tool."
        )
    return (
        "No geometry was found in the boundary file. It parsed correctly but is "
        "empty, which usually means the polygon was never saved into it."
    )


def read_kmz(path: Path) -> str:
    """Return the KML document inside a KMZ archive."""
    try:
        with zipfile.ZipFile(path) as archive:
            names = [n for n in archive.namelist() if n.lower().endswith(".kml")]
            if not names:
                raise InvalidBoundaryError(
                    f"{path.name} is a KMZ archive but contains no .kml document. "
                    "A KMZ is a zipped KML; this archive holds "
                    f"{len(archive.namelist())} other file(s) instead.",
                )
            # doc.kml is the conventional entry point; otherwise take the first.
            preferred = next((n for n in names if n.lower().endswith("doc.kml")), names[0])
            return archive.read(preferred).decode("utf-8", errors="replace")
    except zipfile.BadZipFile as exc:
        raise InvalidBoundaryError(
            f"{path.name} could not be opened as a KMZ archive: {exc}. If this "
            "is a plain KML file, rename it with a .kml extension.",
        ) from exc


# --- GeoJSON -------------------------------------------------------------


def parse_geojson(text: str) -> BaseGeometry:
    """Extract every polygon from a GeoJSON document as one geometry."""
    return _parse_geojson_with_crs(text)[0]


def _parse_geojson_with_crs(text: str) -> tuple[BaseGeometry, CRS, list[str]]:
    """Parse GeoJSON, honouring a legacy `crs` member if the file carries one."""
    if not text.strip():
        raise InvalidBoundaryError(
            "The boundary file is empty. Upload a GeoJSON containing the forest "
            "boundary you want to analyse.",
        )

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise InvalidBoundaryError(f"The boundary file is not valid JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise InvalidBoundaryError(
            "The boundary file is valid JSON but not GeoJSON: the top level is "
            f"a {type(payload).__name__}, not an object with a 'type' field.",
        )

    geometries: list[BaseGeometry] = []
    saw_geometry = False

    def collect(node) -> None:
        nonlocal saw_geometry
        if not isinstance(node, dict):
            return
        node_type = node.get("type")
        if node_type == "FeatureCollection":
            for feature in node.get("features", []) or []:
                collect(feature)
        elif node_type == "Feature":
            collect(node.get("geometry"))
        elif node_type in {
            "Polygon",
            "MultiPolygon",
            "GeometryCollection",
            "LineString",
            "Point",
            "MultiLineString",
            "MultiPoint",
        }:
            saw_geometry = True
            if node_type == "GeometryCollection":
                for child in node.get("geometries", []) or []:
                    collect(child)
                return
            try:
                geometries.append(shape(node))
            except Exception as exc:
                raise InvalidBoundaryError(
                    f"A geometry in the boundary file could not be read: {exc}",
                ) from exc

    collect(payload)
    polygons = [g for g in geometries if isinstance(g, Polygon | MultiPolygon)]
    if not polygons:
        raise InvalidBoundaryError(_no_area_message(saw_geometry))

    crs, warnings = geojson_crs(payload)
    return _combine(polygons), crs, warnings


def geojson_crs(payload: dict) -> tuple[CRS, list[str]]:
    """The CRS a GeoJSON declares, or WGS84 as RFC 7946 requires.

    RFC 7946 removed the `crs` member and fixed GeoJSON to WGS84, but files
    exported by QGIS and ArcGIS still carry the GeoJSON 2008 form. Ignoring a
    declared projected CRS would mean reading an easting of 500000 as a
    longitude, so the declaration is honoured where it can be understood.
    """
    member = payload.get("crs")
    if not isinstance(member, dict):
        return WGS84, []

    name = (member.get("properties") or {}).get("name")
    if not isinstance(name, str) or not name.strip():
        return WGS84, []

    try:
        declared = CRS.from_user_input(name)
    except Exception:
        return WGS84, [
            f"The boundary file declares the coordinate system '{name}', which "
            "could not be interpreted. It has been read as WGS84 longitude and "
            "latitude, which is what RFC 7946 requires; if the boundary appears "
            "in the wrong place, reproject the file to EPSG:4326 and retry."
        ]

    if declared == WGS84:
        return WGS84, []

    return declared, [
        f"The boundary file declares the coordinate system {_label(declared)} "
        "using the pre-RFC 7946 'crs' member. CanopyLens has honoured that "
        "declaration and reprojected the boundary rather than reading the "
        "coordinates as longitude and latitude."
    ]


# --- Shared ---------------------------------------------------------------


def _combine(polygons: list[Polygon | MultiPolygon]) -> Polygon | MultiPolygon:
    """Repair, then merge, a set of polygons into one boundary geometry.

    Repairing before the union matters. A self-intersecting ring - a bow-tie,
    which is what a hand-drawn boundary becomes when the user crosses their own
    line - makes `unary_union` raise on some GEOS builds, and would take the
    whole file down with it. Repaired individually, a bad ring costs at most
    itself.
    """
    usable: list[Polygon | MultiPolygon] = []
    for polygon in polygons:
        repaired = all_polygons(polygon)
        if repaired is not None:
            usable.append(repaired)

    if not usable:
        raise InvalidBoundaryError(
            "The boundary in this file has zero area once repaired, so there "
            "is no region to analyse. This happens when the polygon's points "
            "are all on one line, or when it encloses nothing.",
        )

    merged = all_polygons(unary_union(usable))
    if merged is None:
        raise InvalidBoundaryError(
            "The boundary in this file has zero area once repaired, so there "
            "is no region to analyse.",
        )
    return merged


def count_parts(geometry: BaseGeometry) -> int:
    """How many separate areas the boundary describes."""
    if geometry is None or geometry.is_empty:
        return 0
    return len(getattr(geometry, "geoms", [geometry]))


def load_boundary(path: Path) -> LoadedBoundary:
    """Read a boundary file into a repaired geometry with a known CRS."""
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise UnsupportedFileError(
            f"{suffix or 'This file'} is not a supported boundary format. "
            f"Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}.",
            detail={"supported": sorted(SUPPORTED_EXTENSIONS), "received": suffix},
        )
    if not path.exists():
        raise InvalidBoundaryError(f"Boundary file not found: {path.name}")
    if path.stat().st_size == 0:
        raise InvalidBoundaryError(
            f"{path.name} is empty (0 bytes). The upload may have been "
            "interrupted; try exporting the boundary again."
        )

    warnings: list[str] = []
    if suffix == ".kmz":
        geometry, crs, source = parse_kml(read_kmz(path)), WGS84, "KMZ"
    elif suffix == ".kml":
        geometry = parse_kml(path.read_text(encoding="utf-8", errors="replace"))
        crs, source = WGS84, "KML"
    else:
        geometry, crs, warnings = _parse_geojson_with_crs(
            path.read_text(encoding="utf-8", errors="replace")
        )
        source = "GeoJSON"

    parts = count_parts(geometry)
    if parts > 1:
        warnings.append(
            f"The boundary contains {parts} separate areas. All of them are "
            "analysed together, and the reported boundary area is their total."
        )

    log.info(
        "Boundary loaded from %s: %s with %d part(s) in %s",
        path.name,
        geometry.geom_type,
        parts,
        _label(crs),
    )
    return LoadedBoundary(
        geometry=geometry,
        crs=crs,
        part_count=parts,
        source_format=source,
        warnings=warnings,
    )


# --- Measurement ----------------------------------------------------------


def measure(loaded: LoadedBoundary, metric_crs: CRS | None = None) -> BoundaryMeasurement:
    """Measure a boundary's own area in square metres.

    The area is taken after reprojecting into a metric CRS chosen for the
    boundary's extent - a UTM zone for a stand, an equal-area projection for
    something continental. Taking `.area` on the WGS84 geometry would return
    square degrees, which is not a unit of area.

    `metric_crs` overrides that choice. The analysis pipeline passes the CRS it
    is measuring the imagery in, so that the boundary area and the analysed
    area are figures in the same projection and can be compared directly rather
    than merely being close.
    """
    metric = metric_crs or metric_crs_for(loaded.geometry, loaded.crs)
    in_wgs84 = to_crs(loaded.geometry, loaded.crs, WGS84)
    return BoundaryMeasurement(
        area_m2=round(area_m2(loaded.geometry, loaded.crs, metric), 2),
        part_count=loaded.part_count,
        has_holes=loaded.has_holes,
        measurement_crs=_label(metric),
        bounds_wgs84=tuple(round(v, 8) for v in in_wgs84.bounds),  # type: ignore[arg-type]
        centroid_wgs84=tuple(
            round(v, 8) for v in centroid_lonlat(loaded.geometry, loaded.crs)
        ),  # type: ignore[arg-type]
        source_format=loaded.source_format,
        warnings=list(loaded.warnings),
    )


def to_wgs84_geojson(loaded: LoadedBoundary) -> dict:
    """The boundary as a GeoJSON geometry in WGS84, ready for a web map."""
    geometry = to_crs(loaded.geometry, loaded.crs, WGS84)
    cleaned = repair(geometry)
    return (cleaned if cleaned is not None else geometry).__geo_interface__


def _label(crs: CRS) -> str:
    epsg = crs.to_epsg()
    if epsg:
        return f"EPSG:{epsg}"
    return crs.name or crs.to_proj4()
