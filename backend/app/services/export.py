"""CSV and GeoJSON export.

Both exporters are built from an `AnalysisResult` and carry the same honesty
rules as the rest of the pipeline:

- Area columns are blank, not zero, when the imagery could not be measured.
  Zero is a measurement; blank is the absence of one.
- Column and property names state their units, so `area_m2` cannot be mistaken
  for pixels once the file leaves the application.
- A GeoJSON produced from non-georeferenced imagery does not pretend to be
  WGS84. RFC 7946 defines GeoJSON coordinates as longitude and latitude, so
  writing pixel coordinates into that field without saying so would make the
  file silently wrong in every GIS that opens it.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone

from shapely.geometry import mapping

from .analysis import AnalysisResult

CSV_COLUMNS = [
    "crown_id",
    "confidence",
    "confidence_kind",
    "area_m2",
    "area_pixels",
    "centroid_lon",
    "centroid_lat",
    "centroid_x_px",
    "centroid_y_px",
    "engine",
]


def export_csv(result: AnalysisResult, include_header_notes: bool = True) -> str:
    """One row per crown, as CSV text.

    `include_header_notes` prepends commented lines recording the imagery, the
    engine, the settings and - when relevant - the fact that areas are
    unavailable. A CSV that outlives the session it came from should still be
    able to explain itself. Spreadsheet software shows these as leading rows;
    pandas skips them with `comment='#'`.
    """
    buffer = io.StringIO()

    if include_header_notes:
        for line in _provenance_lines(result):
            buffer.write(f"# {line}\n")

    writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for crown in result.crowns:
        flat = crown.to_dict()
        writer.writerow(
            {
                "crown_id": flat["id"],
                "confidence": flat["confidence"],
                "confidence_kind": flat["confidence_kind"],
                # Empty rather than 0 when unmeasurable: a blank cell reads as
                # "no value", whereas 0 reads as "a crown with no area".
                "area_m2": "" if flat["area_m2"] is None else flat["area_m2"],
                "area_pixels": flat["pixel_area"],
                "centroid_lon": "" if flat["centroid_lon"] is None else flat["centroid_lon"],
                "centroid_lat": "" if flat["centroid_lat"] is None else flat["centroid_lat"],
                "centroid_x_px": flat["centroid_x_px"],
                "centroid_y_px": flat["centroid_y_px"],
                "engine": flat["engine"],
            }
        )
    return buffer.getvalue()


def export_geojson(result: AnalysisResult, indent: int | None = None) -> str:
    """Crown polygons as a GeoJSON FeatureCollection, as text.

    Geometries are in WGS84 when the imagery is georeferenced, which is what
    RFC 7946 requires and what every GIS expects. For imagery without
    georeferencing the geometries are pixel coordinates, and the collection
    carries `canopylens.coordinate_space: "pixel"` plus a warning saying so -
    the file is still useful for redrawing crowns over the original image, but
    it is not map data and does not claim to be.
    """
    return json.dumps(build_geojson(result), indent=indent)


def build_geojson(result: AnalysisResult) -> dict:
    georeferenced = result.georeferenced
    features = []

    for crown in result.crowns:
        geometry = crown.geometry_wgs84 if georeferenced else crown.geometry_px
        flat = crown.to_dict()
        properties = {
            "crown_id": flat["id"],
            "confidence": flat["confidence"],
            "confidence_kind": flat["confidence_kind"],
            "area_m2": flat["area_m2"],
            "area_pixels": flat["pixel_area"],
            "centroid_lon": flat["centroid_lon"],
            "centroid_lat": flat["centroid_lat"],
            "centroid_x_px": flat["centroid_x_px"],
            "centroid_y_px": flat["centroid_y_px"],
            "engine": flat["engine"],
        }
        features.append(
            {
                "type": "Feature",
                "id": flat["id"],
                "geometry": mapping(geometry),
                "properties": properties,
            }
        )

    collection = {
        "type": "FeatureCollection",
        "features": features,
        "canopylens": _metadata(result),
    }
    if not georeferenced:
        collection["canopylens"]["coordinate_space"] = "pixel"
        collection["canopylens"]["warning"] = (
            "The source image is not georeferenced. Coordinates in this file "
            "are image pixel positions (x = column, y = row), NOT longitude "
            "and latitude. Do not load this file as map data."
        )
    else:
        collection["canopylens"]["coordinate_space"] = "EPSG:4326"

    return collection


def _metadata(result: AnalysisResult) -> dict:
    summary = result.summary
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_image": result.raster.path.name,
        "image_size_px": [result.raster.width, result.raster.height],
        "georeferenced": result.georeferenced,
        "source_crs": result.source_crs,
        "measurement_crs": result.measurement_crs,
        "pixel_area_m2": result.pixel_area_m2,
        "engine": result.engine,
        "confidence_kind": result.confidence_kind,
        "confidence_threshold": result.confidence_threshold,
        "min_crown_area_m2": result.min_crown_area_m2,
        "boundary_applied": result.boundary_applied,
        "tree_count": summary.tree_count,
        "total_canopy_area_m2": summary.total_canopy_area_m2,
        "analyzed_area_m2": summary.analyzed_area_m2,
        "canopy_coverage_pct": summary.canopy_coverage_pct,
        "warnings": result.warnings,
        "disclaimer": (
            "Crown detections are estimated from imagery by an automated "
            "model. They have not been validated against ground survey and "
            "are not a field-verified tree inventory."
        ),
    }


def _provenance_lines(result: AnalysisResult) -> list[str]:
    summary = result.summary
    lines = [
        f"CanopyLens crown export - generated {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        f"Source image: {result.raster.path.name} ({result.raster.width}x{result.raster.height} px)",
        f"Engine: {result.engine} (confidence is a {result.confidence_kind} score)",
        f"Confidence threshold: {result.confidence_threshold}",
        f"Trees detected: {summary.tree_count}",
    ]
    if result.georeferenced:
        lines.append(f"Source CRS: {result.source_crs}; areas measured in {result.measurement_crs}")
        lines.append(
            f"Total canopy area: {summary.total_canopy_area_m2} m2; "
            f"analysed area: {summary.analyzed_area_m2} m2; "
            f"coverage: {summary.canopy_coverage_pct}%"
        )
    else:
        lines.append(
            "Source image is NOT georeferenced: area_m2 and centroid_lon/lat "
            "are empty, and only pixel measurements are available."
        )
    if result.boundary_applied:
        lines.append(
            f"A boundary was applied; {result.excluded_by_boundary} detection(s) "
            "outside it were excluded."
        )
    for warning in result.warnings:
        lines.append(f"Warning: {warning}")
    lines.append(
        "These are model estimates from imagery, not a field-verified tree inventory."
    )
    return lines
