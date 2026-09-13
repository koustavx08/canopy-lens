"""Parsing and applying KML, KMZ and GeoJSON boundaries."""

from __future__ import annotations

import json
import zipfile

import pytest
from shapely.geometry import Polygon, box

from app.errors import InvalidBoundaryError, UnsupportedFileError
from app.services import geo
from app.services.boundary import (
    count_parts,
    load_boundary,
    measure,
    parse_coordinates,
    parse_geojson,
    parse_kml,
    to_wgs84_geojson,
)

from ..conftest import kml_document, kml_polygon, utm_rect, utm_to_lonlat, write_utm_kml

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
        loaded = load_boundary(path)
        # KML is defined by its specification to be WGS84; this is asserted,
        # not inferred from the numbers in the file.
        assert loaded.crs == geo.WGS84
        assert loaded.geometry.area > 0
        assert loaded.source_format == "KML"
        assert loaded.part_count == 1

    def test_kmz_is_unzipped_and_parsed(self, tmp_path):
        path = tmp_path / "b.kmz"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("doc.kml", kml_polygon(SQUARE))
        loaded = load_boundary(path)
        assert loaded.crs == geo.WGS84
        assert loaded.source_format == "KMZ"
        assert loaded.geometry.bounds == pytest.approx((-81.0, 42.0, -80.99, 42.01))

    def test_a_kmz_with_a_differently_named_kml_still_works(self, tmp_path):
        # doc.kml is a convention, not a requirement.
        path = tmp_path / "named.kmz"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("files/stand-7.kml", kml_polygon(SQUARE))
        assert load_boundary(path).geometry.area > 0

    def test_an_empty_file_is_reported_before_parsing_is_attempted(self, tmp_path):
        path = tmp_path / "truncated.kml"
        path.write_bytes(b"")
        with pytest.raises(InvalidBoundaryError) as caught:
            load_boundary(path)
        assert "empty" in str(caught.value).lower()

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


class TestEmptyAndUnusableDocuments:
    """Nothing here should crash, and every message should name a fix."""

    def test_a_whitespace_only_kml_says_the_file_is_empty(self):
        with pytest.raises(InvalidBoundaryError) as caught:
            parse_kml("   \n  ")
        assert "empty" in str(caught.value).lower()

    def test_a_kml_with_no_geometry_is_distinguished_from_one_with_no_area(self):
        # "You drew nothing" and "you drew a line, not an area" are different
        # mistakes with different fixes, so they get different messages.
        with pytest.raises(InvalidBoundaryError) as empty:
            parse_kml('<kml xmlns="http://www.opengis.net/kml/2.2"><Document/></kml>')
        with pytest.raises(InvalidBoundaryError) as pins:
            parse_kml(
                '<kml xmlns="http://www.opengis.net/kml/2.2"><Placemark><Point>'
                "<coordinates>-81,42</coordinates></Point></Placemark></kml>"
            )
        assert "no geometry was found" in str(empty.value).lower()
        assert "polygon tool" in str(pins.value).lower()

    def test_a_degenerate_polygon_is_refused_rather_than_measured_as_zero(self):
        # Three collinear points enclose nothing. Returning a zero-area
        # boundary would make every later division silently meaningless.
        document = (
            '<kml xmlns="http://www.opengis.net/kml/2.2"><Placemark><Polygon>'
            "<outerBoundaryIs><LinearRing><coordinates>"
            "0,0 1,1 2,2 3,3</coordinates></LinearRing></outerBoundaryIs>"
            "</Polygon></Placemark></kml>"
        )
        with pytest.raises(InvalidBoundaryError) as caught:
            parse_kml(document)
        assert "zero area" in str(caught.value).lower()

    def test_an_empty_geojson_document_is_reported(self):
        with pytest.raises(InvalidBoundaryError) as caught:
            parse_geojson("")
        assert "empty" in str(caught.value).lower()

    def test_a_json_array_is_not_geojson(self):
        with pytest.raises(InvalidBoundaryError) as caught:
            parse_geojson("[1, 2, 3]")
        assert "not geojson" in str(caught.value).lower()


class TestInvalidGeometry:
    """A hand-drawn boundary that crosses itself must not take the file down."""

    def test_a_self_intersecting_polygon_is_repaired(self):
        # A bow-tie: the ring crosses itself, which is what happens when the
        # user's last click lands on the wrong side of their own line.
        document = (
            '<kml xmlns="http://www.opengis.net/kml/2.2"><Placemark><Polygon>'
            "<outerBoundaryIs><LinearRing><coordinates>"
            "0,0 2,2 2,0 0,2</coordinates></LinearRing></outerBoundaryIs>"
            "</Polygon></Placemark></kml>"
        )
        geometry = parse_kml(document)
        assert geometry.is_valid
        assert geometry.area > 0

    def test_one_broken_ring_does_not_discard_the_valid_ones(self):
        # Repairing each polygon before the union is what makes this hold: a
        # single bad ring in a twenty-stand file costs that ring, not the file.
        document = kml_document(
            [
                ([(0, 0), (0, 1), (1, 1), (1, 0)], []),
                ([(5, 5), (5, 5), (5, 5)], []),  # degenerate
                ([(10, 10), (10, 12), (12, 12), (12, 10)], []),
            ]
        )
        geometry = parse_kml(document)
        assert geometry.area == pytest.approx(1.0 + 4.0)


class TestMultiplePolygons:
    TWO_STANDS = [
        ([(0, 0), (0, 1), (1, 1), (1, 0)], []),
        ([(5, 5), (5, 7), (7, 7), (7, 5)], []),
    ]

    def test_separate_stands_are_kept_as_separate_parts(self, tmp_path):
        path = tmp_path / "two.kml"
        path.write_text(kml_document(self.TWO_STANDS), encoding="utf-8")
        loaded = load_boundary(path)
        assert loaded.part_count == 2
        assert loaded.geometry.geom_type == "MultiPolygon"

    def test_the_user_is_told_there_is_more_than_one_area(self, tmp_path):
        path = tmp_path / "two.kml"
        path.write_text(kml_document(self.TWO_STANDS), encoding="utf-8")
        assert any("2 separate areas" in w for w in load_boundary(path).warnings)

    def test_touching_polygons_merge_into_one_part(self, tmp_path):
        # Two halves of one stand exported as two placemarks describe one area,
        # and counting them as two would misreport the file back to the user.
        path = tmp_path / "halves.kml"
        path.write_text(
            kml_document(
                [
                    ([(0, 0), (0, 2), (1, 2), (1, 0)], []),
                    ([(1, 0), (1, 2), (2, 2), (2, 0)], []),
                ]
            ),
            encoding="utf-8",
        )
        loaded = load_boundary(path)
        assert loaded.part_count == 1
        assert loaded.geometry.area == pytest.approx(4.0)

    def test_count_parts_handles_both_shapes(self):
        assert count_parts(Polygon(SQUARE)) == 1
        assert count_parts(box(0, 0, 1, 1).union(box(5, 5, 6, 6))) == 2


class TestHoles:
    def test_a_loaded_boundary_reports_that_it_has_holes(self, tmp_path):
        path = write_utm_kml(
            tmp_path / "holed.kml",
            [utm_rect(500000, 4649000, 500100, 4649100)],
            holes=[utm_rect(500020, 4649020, 500040, 4649040)],
        )
        loaded = load_boundary(path)
        assert loaded.has_holes is True
        assert loaded.geometry.interiors

    def test_a_solid_boundary_reports_that_it_does_not(self, tmp_path):
        path = write_utm_kml(tmp_path / "solid.kml", [utm_rect(500000, 4649000, 500100, 4649100)])
        assert load_boundary(path).has_holes is False


class TestDeclaredCoordinateSystems:
    """GeoJSON files in the wild still carry the pre-RFC 7946 `crs` member."""

    def _document(self, crs_name, ring):
        payload = {
            "type": "Polygon",
            "coordinates": [[list(point) for point in [*ring, ring[0]]]],
        }
        if crs_name is not None:
            payload["crs"] = {"type": "name", "properties": {"name": crs_name}}
        return json.dumps(payload)

    def test_a_declared_projected_crs_is_honoured(self, tmp_path):
        # Read as longitude and latitude, an easting of 500000 would land in
        # the Gulf of Guinea. The declaration is the only thing that prevents it.
        path = tmp_path / "utm.geojson"
        path.write_text(
            self._document(
                "urn:ogc:def:crs:EPSG::32617", utm_rect(500000, 4649000, 500100, 4649100)
            ),
            encoding="utf-8",
        )
        loaded = load_boundary(path)
        assert loaded.crs.to_epsg() == 32617
        assert any("32617" in w for w in loaded.warnings)

    def test_honouring_it_puts_the_boundary_in_the_right_place(self, tmp_path):
        path = tmp_path / "utm.geojson"
        path.write_text(
            self._document("EPSG:32617", utm_rect(500000, 4649000, 500100, 4649100)),
            encoding="utf-8",
        )
        drawn = to_wgs84_geojson(load_boundary(path))
        longitude, latitude = drawn["coordinates"][0][0]
        assert -81.1 < longitude < -80.9
        assert 41.9 < latitude < 42.1

    def test_an_explicit_wgs84_declaration_produces_no_warning(self, tmp_path):
        path = tmp_path / "wgs84.geojson"
        path.write_text(self._document("EPSG:4326", SQUARE), encoding="utf-8")
        loaded = load_boundary(path)
        assert loaded.crs == geo.WGS84
        assert loaded.warnings == []

    def test_an_uninterpretable_declaration_falls_back_and_says_so(self, tmp_path):
        path = tmp_path / "odd.geojson"
        path.write_text(self._document("NOT_A_CRS_AT_ALL", SQUARE), encoding="utf-8")
        loaded = load_boundary(path)
        assert loaded.crs == geo.WGS84
        assert any("could not be interpreted" in w for w in loaded.warnings)

    def test_no_declaration_means_wgs84_as_rfc_7946_requires(self, tmp_path):
        path = tmp_path / "plain.geojson"
        path.write_text(self._document(None, SQUARE), encoding="utf-8")
        loaded = load_boundary(path)
        assert loaded.crs == geo.WGS84
        assert loaded.warnings == []


class TestDrawableOutput:
    def test_the_geometry_handed_to_the_map_is_wgs84(self, tmp_path):
        path = write_utm_kml(tmp_path / "stand.kml", [utm_rect(500000, 4649000, 500100, 4649100)])
        drawn = to_wgs84_geojson(load_boundary(path))
        assert drawn["type"] == "Polygon"
        for longitude, latitude in drawn["coordinates"][0]:
            assert -180 <= longitude <= 180
            assert -90 <= latitude <= 90

    def test_the_bounds_can_frame_a_map_view(self, tmp_path):
        corners = utm_to_lonlat(utm_rect(500000, 4649000, 500100, 4649100))
        path = tmp_path / "stand.kml"
        path.write_text(kml_polygon(corners), encoding="utf-8")
        measurement = measure(load_boundary(path))
        min_lon, min_lat, max_lon, max_lat = measurement.bounds_wgs84
        assert min_lon < max_lon and min_lat < max_lat
        centroid_lon, centroid_lat = measurement.centroid_wgs84
        assert min_lon <= centroid_lon <= max_lon
        assert min_lat <= centroid_lat <= max_lat


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
