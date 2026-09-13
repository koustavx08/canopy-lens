"""Parsing and applying KML, KMZ and GeoJSON boundaries."""

from __future__ import annotations

import json
import zipfile

import pytest
from shapely.geometry import Polygon, box

from app.errors import InvalidBoundaryError, UnsupportedFileError
from app.services import geo
from app.services.boundary import load_boundary, parse_coordinates, parse_geojson, parse_kml

from ..conftest import kml_polygon

SQUARE = [(-81.0, 42.0), (-80.99, 42.0), (-80.99, 42.01), (-81.0, 42.01)]


class TestCoordinateParsing:
    def test_longitude_latitude_pairs_are_read_in_order(self):
        assert parse_coordinates("-81.0,42.0 -80.9,42.1") == [(-81.0, 42.0), (-80.9, 42.1)]

    def test_altitude_is_discarded(self):
        # KML allows a third component. Ground area does not depend on height
        # above the ellipsoid, and keeping it would make the geometry 3D.
        assert parse_coordinates("-81.0,42.0,350.5") == [(-81.0, 42.0)]

    def test_newlines_and_tabs_separate_points_like_spaces(self):
        text = "-81.0,42.0\n\t-80.9,42.1\n  -80.8,42.2 "
        assert len(parse_coordinates(text)) == 3

    def test_malformed_tokens_are_skipped_rather_than_crashing(self):
        assert parse_coordinates("-81.0,42.0 rubbish -80.9,42.1") == [
            (-81.0, 42.0),
            (-80.9, 42.1),
        ]


class TestKmlParsing:
    def test_a_simple_polygon_is_read(self):
        geometry = parse_kml(kml_polygon(SQUARE))
        assert geometry.geom_type == "Polygon"
        assert geometry.bounds == pytest.approx((-81.0, 42.0, -80.99, 42.01))

    def test_an_undeclared_namespace_still_parses(self):
        # Plenty of real exports omit the xmlns. Matching on local tag names
        # rather than namespaced ones is what makes this work.
        document = (
            "<kml><Document><Placemark><Polygon><outerBoundaryIs><LinearRing>"
            "<coordinates>-81,42 -80.99,42 -80.99,42.01 -81,42.01</coordinates>"
            "</LinearRing></outerBoundaryIs></Polygon></Placemark></Document></kml>"
        )
        assert parse_kml(document).area > 0

    def test_inner_boundaries_become_holes(self):
        document = (
            '<kml xmlns="http://www.opengis.net/kml/2.2"><Placemark><Polygon>'
            "<outerBoundaryIs><LinearRing><coordinates>"
            "0,0 0,10 10,10 10,0</coordinates></LinearRing></outerBoundaryIs>"
            "<innerBoundaryIs><LinearRing><coordinates>"
            "2,2 2,4 4,4 4,2</coordinates></LinearRing></innerBoundaryIs>"
            "</Polygon></Placemark></kml>"
        )
        geometry = parse_kml(document)
        # A clearing inside the stand is not part of the area to analyse.
        assert geometry.area == pytest.approx(100.0 - 4.0)

    def test_several_placemarks_merge_into_one_boundary(self):
        document = (
            '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
            "<Placemark><Polygon><outerBoundaryIs><LinearRing><coordinates>"
            "0,0 0,1 1,1 1,0</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>"
            "<Placemark><Polygon><outerBoundaryIs><LinearRing><coordinates>"
            "5,5 5,7 7,7 7,5</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>"
            "</Document></kml>"
        )
        geometry = parse_kml(document)
        assert geometry.geom_type == "MultiPolygon"
        assert geometry.area == pytest.approx(1.0 + 4.0)

    def test_a_closed_linestring_is_accepted_as_an_area(self):
        # Some tools export a drawn area as a path rather than a polygon.
        document = (
            '<kml xmlns="http://www.opengis.net/kml/2.2"><Placemark><LineString>'
            "<coordinates>0,0 0,2 2,2 2,0 0,0</coordinates>"
            "</LineString></Placemark></kml>"
        )
        assert parse_kml(document).area == pytest.approx(4.0)

    def test_a_points_only_kml_is_rejected_with_an_explanation(self):
        document = (
            '<kml xmlns="http://www.opengis.net/kml/2.2"><Placemark><Point>'
            "<coordinates>-81.0,42.0</coordinates></Point></Placemark></kml>"
        )
        with pytest.raises(InvalidBoundaryError) as caught:
            parse_kml(document)
        assert "polygon" in str(caught.value).lower()

    def test_malformed_xml_is_reported_as_such(self):
        with pytest.raises(InvalidBoundaryError) as caught:
            parse_kml("<kml><unclosed>")
        assert "xml" in str(caught.value).lower()


class TestGeoJsonParsing:
    def test_a_feature_collection_is_read(self):
        payload = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[0, 0], [0, 2], [2, 2], [2, 0], [0, 0]]],
                    },
                }
            ],
        }
        assert parse_geojson(json.dumps(payload)).area == pytest.approx(4.0)

    def test_a_bare_geometry_is_read(self):
        payload = {"type": "Polygon", "coordinates": [[[0, 0], [0, 1], [1, 1], [1, 0], [0, 0]]]}
        assert parse_geojson(json.dumps(payload)).area == pytest.approx(1.0)

    def test_a_line_only_document_is_rejected(self):
        payload = {"type": "LineString", "coordinates": [[0, 0], [1, 1]]}
        with pytest.raises(InvalidBoundaryError):
            parse_geojson(json.dumps(payload))

    def test_invalid_json_is_reported_as_such(self):
        with pytest.raises(InvalidBoundaryError) as caught:
            parse_geojson("{not json")
        assert "json" in str(caught.value).lower()


class TestLoadingFiles:
    def test_kml_is_loaded_as_wgs84(self, tmp_path):
        path = tmp_path / "b.kml"
        path.write_text(kml_polygon(SQUARE), encoding="utf-8")
        geometry, crs = load_boundary(path)
        # KML is defined by its specification to be WGS84; this is asserted,
        # not inferred from the numbers in the file.
        assert crs == geo.WGS84
        assert geometry.area > 0

    def test_kmz_is_unzipped_and_parsed(self, tmp_path):
        path = tmp_path / "b.kmz"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("doc.kml", kml_polygon(SQUARE))
        geometry, crs = load_boundary(path)
        assert crs == geo.WGS84
        assert geometry.bounds == pytest.approx((-81.0, 42.0, -80.99, 42.01))

    def test_a_kmz_without_a_kml_inside_is_rejected(self, tmp_path):
        path = tmp_path / "empty.kmz"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("readme.txt", "nothing here")
        with pytest.raises(InvalidBoundaryError):
            load_boundary(path)

    def test_a_corrupt_kmz_is_reported_not_crashed(self, tmp_path):
        path = tmp_path / "broken.kmz"
        path.write_bytes(b"this is not a zip archive")
        with pytest.raises(InvalidBoundaryError):
            load_boundary(path)

    def test_an_unsupported_extension_is_refused(self, tmp_path):
        path = tmp_path / "boundary.shp"
        path.write_bytes(b"")
        with pytest.raises(UnsupportedFileError):
            load_boundary(path)

    def test_a_missing_file_is_reported(self, tmp_path):
        with pytest.raises(InvalidBoundaryError):
            load_boundary(tmp_path / "absent.kml")


class TestClippingBehaviour:
    """Clipping is what the boundary is for; these check the geometry of it."""

    def test_a_boundary_reprojected_into_utm_keeps_its_shape(self):
        from pyproj import CRS

        utm = CRS.from_epsg(32617)
        boundary = Polygon(SQUARE)
        in_utm = geo.to_crs(boundary, geo.WGS84, utm)
        # Same ground, different numbers: the area must survive the round trip.
        assert geo.area_m2(boundary, geo.WGS84, utm) == pytest.approx(in_utm.area, rel=1e-9)

    def test_points_inside_and_outside_are_separated_correctly(self):
        boundary = box(0, 0, 10, 10)
        from shapely.geometry import Point

        assert boundary.contains(Point(5, 5))
        assert not boundary.contains(Point(15, 5))

    def test_intersecting_a_footprint_yields_the_overlap_only(self):
        footprint = box(0, 0, 10, 10)
        boundary = box(5, 0, 20, 10)  # extends beyond the imagery
        assert footprint.intersection(boundary).area == pytest.approx(50.0)
