"""Parsing KML, KMZ and GeoJSON boundary files.

KML is parsed directly from its XML rather than through GDAL's KML driver.
That driver is optional in a GDAL build and its availability varies between
wheels and container images, so depending on it would make boundary support
work on the developer's machine and fail in production. KML is a small, well
specified XML format and the subset that matters here - polygons and
multi-geometries - is short to read directly.

KML coordinates are defined by the specification to be longitude, latitude and
optional altitude in WGS84 (EPSG:4326). That is asserted here rather than
guessed, and the geometry is reprojected into the raster's CRS by the caller so
that clipping happens in one consistent space.
"""

from __future__ import annotations

import json
import logging
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from pyproj import CRS
from shapely.geometry import MultiPolygon, Polygon, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from ..errors import InvalidBoundaryError, UnsupportedFileError
from .geo import WGS84, all_polygons

log = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".kml", ".kmz", ".geojson", ".json"}

# KML documents may or may not declare the namespace, and several versions are
# in circulation. Matching on the local tag name sidesteps the whole problem.
_KML_NAMESPACES = (
    "{http://www.opengis.net/kml/2.2}",
    "{http://earth.google.com/kml/2.1}",
    "{http://earth.google.com/kml/2.0}",
    "",
)


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
        raise InvalidBoundaryError(
            "No polygon was found in the boundary file. CanopyLens needs an "
            "area to analyse; a KML containing only points or paths does not "
            "define one.",
        )

    merged = unary_union(polygons)
    cleaned = all_polygons(merged)
    if cleaned is None:
        raise InvalidBoundaryError(
            "The boundary in this file has zero area once repaired, so there "
            "is no region to analyse.",
        )
    return cleaned


def read_kmz(path: Path) -> str:
    """Return the KML document inside a KMZ archive."""
    try:
        with zipfile.ZipFile(path) as archive:
            names = [n for n in archive.namelist() if n.lower().endswith(".kml")]
            if not names:
                raise InvalidBoundaryError(
                    f"{path.name} is a KMZ archive but contains no .kml document.",
                )
            # doc.kml is the conventional entry point; otherwise take the first.
            preferred = next((n for n in names if n.lower().endswith("doc.kml")), names[0])
            return archive.read(preferred).decode("utf-8", errors="replace")
    except zipfile.BadZipFile as exc:
        raise InvalidBoundaryError(
            f"{path.name} could not be opened as a KMZ archive: {exc}",
        ) from exc


def parse_geojson(text: str) -> BaseGeometry:
    """Extract every polygon from a GeoJSON document as one geometry."""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise InvalidBoundaryError(f"The boundary file is not valid JSON: {exc}") from exc

    geometries: list[BaseGeometry] = []

    def collect(node) -> None:
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
        raise InvalidBoundaryError(
            "No polygon was found in the boundary file. CanopyLens needs an "
            "area to analyse, not a point or a line.",
        )

    cleaned = all_polygons(unary_union(polygons))
    if cleaned is None:
        raise InvalidBoundaryError(
            "The boundary in this file has zero area once repaired, so there "
            "is no region to analyse.",
        )
    return cleaned


def load_boundary(path: Path) -> tuple[BaseGeometry, CRS]:
    """Read a boundary file and return (geometry, its CRS).

    GeoJSON is WGS84 by RFC 7946, and KML is WGS84 by its own specification, so
    the CRS is known rather than assumed from the file's contents.
    """
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise UnsupportedFileError(
            f"{suffix or 'This file'} is not a supported boundary format.",
            detail={"supported": sorted(SUPPORTED_EXTENSIONS), "received": suffix},
        )
    if not path.exists():
        raise InvalidBoundaryError(f"Boundary file not found: {path.name}")

    if suffix == ".kmz":
        geometry = parse_kml(read_kmz(path))
    elif suffix == ".kml":
        geometry = parse_kml(path.read_text(encoding="utf-8", errors="replace"))
    else:
        geometry = parse_geojson(path.read_text(encoding="utf-8", errors="replace"))

    log.info("Boundary loaded from %s (%s)", path.name, geometry.geom_type)
    return geometry, WGS84
