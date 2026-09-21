"""Scoring detections against crowns somebody actually verified.

Every figure this application reports is labelled an estimate, and that label
is honest but unquantified: it says the number might be wrong without saying by
how much. This module is how that becomes a measurement. Given ground truth -
crowns delineated by hand, from field survey or from an analyst working over
the same imagery - it reports precision, recall, F1 and how closely the total
canopy area agrees.

What it does not do is supply the ground truth. There is none in this
repository and none is invented: a validation harness scored against synthetic
circles would report a number about circles. The dataset is the user's to bring
and is specific to their sensor, season and region, which is exactly the
domain-shift limitation the rest of the application states about itself.

Two decisions worth stating plainly, because they decide what the numbers mean.

**Matching is one-to-one and greedy by descending IoU.** A detection that
overlaps two truth crowns is credited against one of them, not both. Allowing
it to count twice would let a single blob covering a whole cluster report a
recall it has not earned - which is the exact failure mode of the classical
engine in closed canopy, and the one a validation run most needs to catch.

**Everything is measured in a metric CRS** chosen from the data's own position,
as the analysis pipeline does. IoU is a ratio and would survive being computed
in degrees; area would not, and reporting one honestly while the other quietly
used square degrees is the kind of disagreement that takes a day to find.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from pyproj import CRS
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from ..errors import InvalidBoundaryError
from . import geo
from .boundary import geojson_crs, load_boundary

log = logging.getLogger(__name__)

DEFAULT_IOU_THRESHOLD = 0.5
"""Overlap at which a detection is counted as the same tree as a truth crown.

Half is the convention in detection benchmarks and it is a convention rather
than a fact: at 0.5 a detection can miss half the crown and still count. It is
reported alongside every score so the number is never read without it.
"""

GEOJSON_SUFFIXES = {".geojson", ".json"}


@dataclass(frozen=True)
class Match:
    """One truth crown and the detection credited to it."""

    truth_index: int
    detection_index: int
    iou: float


@dataclass(frozen=True)
class ValidationReport:
    """What a run scored, and against what.

    `precision`, `recall` and `f1` are None rather than 0 or 1 where they are
    undefined - no detections at all, or no truth at all. A score of 1.0 for
    finding nothing in an empty scene is arithmetically defensible and reads as
    a perfect result, which is the sort of number this project does not print.
    """

    iou_threshold: float
    truth_count: int
    detection_count: int
    matched: int
    precision: float | None
    recall: float | None
    f1: float | None
    mean_matched_iou: float | None
    truth_area_m2: float
    detected_area_m2: float
    area_ratio: float | None
    measurement_crs: str
    matches: list[Match] = field(default_factory=list)
    unmatched_truth: list[int] = field(default_factory=list)
    unmatched_detections: list[int] = field(default_factory=list)

    @property
    def false_positives(self) -> int:
        return len(self.unmatched_detections)

    @property
    def false_negatives(self) -> int:
        return len(self.unmatched_truth)

    def to_dict(self) -> dict:
        return {
            "iou_threshold": self.iou_threshold,
            "measurement_crs": self.measurement_crs,
            "counts": {
                "truth": self.truth_count,
                "detected": self.detection_count,
                "matched": self.matched,
                "false_positives": self.false_positives,
                "false_negatives": self.false_negatives,
            },
            "scores": {
                "precision": _rounded(self.precision, 4),
                "recall": _rounded(self.recall, 4),
                "f1": _rounded(self.f1, 4),
                "mean_matched_iou": _rounded(self.mean_matched_iou, 4),
            },
            "area": {
                "truth_m2": round(self.truth_area_m2, 2),
                "detected_m2": round(self.detected_area_m2, 2),
                "ratio": _rounded(self.area_ratio, 4),
            },
        }


def score(
    truth: list[BaseGeometry],
    detections: list[BaseGeometry],
    *,
    source_crs: CRS = geo.WGS84,
    iou_threshold: float = DEFAULT_IOU_THRESHOLD,
) -> ValidationReport:
    """Match detections to truth crowns and report how well they agree.

    Both lists are in `source_crs`. They are reprojected once into a metric CRS
    chosen from their combined position, and everything after that - overlap,
    union, area - is computed there.
    """
    if not 0.0 < iou_threshold <= 1.0:
        raise ValueError("iou_threshold must be greater than 0 and at most 1")

    everything = [g for g in (*truth, *detections) if g is not None and not g.is_empty]
    if not everything:
        return _empty_report(iou_threshold, geo.WGS84)

    metric_crs = geo.metric_crs_for(unary_union(everything), source_crs)
    truth_m = _project(truth, source_crs, metric_crs)
    detections_m = _project(detections, source_crs, metric_crs)

    matches = _match(truth_m, detections_m, iou_threshold)
    matched = len(matches)

    precision = matched / len(detections_m) if detections_m else None
    recall = matched / len(truth_m) if truth_m else None
    f1 = _harmonic_mean(precision, recall)

    truth_area = _union_area(truth_m)
    detected_area = _union_area(detections_m)

    claimed = {m.detection_index for m in matches}
    credited = {m.truth_index for m in matches}

    return ValidationReport(
        iou_threshold=iou_threshold,
        truth_count=len(truth_m),
        detection_count=len(detections_m),
        matched=matched,
        precision=precision,
        recall=recall,
        f1=f1,
        mean_matched_iou=(sum(m.iou for m in matches) / matched) if matched else None,
        truth_area_m2=truth_area,
        detected_area_m2=detected_area,
        # Detected over truth: above 1 the tool is claiming more canopy than is
        # there. Undefined rather than infinite when the truth has no area,
        # which is a statement about the truth file and not about the tool.
        area_ratio=(detected_area / truth_area) if truth_area else None,
        measurement_crs=_label(metric_crs),
        matches=matches,
        unmatched_truth=[i for i in range(len(truth_m)) if i not in credited],
        unmatched_detections=[i for i in range(len(detections_m)) if i not in claimed],
    )


def _match(
    truth: list[BaseGeometry], detections: list[BaseGeometry], iou_threshold: float
) -> list[Match]:
    """Pair each truth crown with at most one detection, best overlap first.

    Every qualifying pair is scored, then taken in descending order of overlap,
    skipping any whose truth crown or detection has already been spoken for.
    Greedy rather than optimal: a globally best assignment needs the Hungarian
    algorithm, and at the overlaps that matter here the two agree - crowns that
    are genuinely the same tree do not usually compete.
    """
    candidates: list[Match] = []
    for t_index, truth_crown in enumerate(truth):
        # A cheap rejection first. Most pairs in a scene of any size do not
        # come near each other, and `intersection` on two polygons is far more
        # expensive than comparing four numbers.
        t_bounds = truth_crown.bounds
        for d_index, detection in enumerate(detections):
            if not _boxes_touch(t_bounds, detection.bounds):
                continue
            overlap = _iou(truth_crown, detection)
            if overlap >= iou_threshold:
                candidates.append(Match(t_index, d_index, overlap))

    candidates.sort(key=lambda m: (-m.iou, m.truth_index, m.detection_index))

    taken_truth: set[int] = set()
    taken_detections: set[int] = set()
    matches: list[Match] = []
    for candidate in candidates:
        if candidate.truth_index in taken_truth or candidate.detection_index in taken_detections:
            continue
        taken_truth.add(candidate.truth_index)
        taken_detections.add(candidate.detection_index)
        matches.append(candidate)
    return sorted(matches, key=lambda m: m.truth_index)


def _iou(a: BaseGeometry, b: BaseGeometry) -> float:
    intersection = a.intersection(b).area
    if not intersection:
        return 0.0
    union = a.area + b.area - intersection
    return intersection / union if union else 0.0


def _boxes_touch(a: tuple[float, ...], b: tuple[float, ...]) -> bool:
    return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])


def _project(
    geometries: list[BaseGeometry], source_crs: CRS, metric_crs: CRS
) -> list[BaseGeometry]:
    projected = []
    for geometry in geometries:
        if geometry is None or geometry.is_empty:
            continue
        repaired = geo.repair(geo.to_crs(geometry, source_crs, metric_crs))
        if repaired is not None:
            projected.append(repaired)
    return projected


def _union_area(geometries: list[BaseGeometry]) -> float:
    """Ground covered, not the sum of the parts.

    The same measure the application reports as canopy area, so a run scored
    here can be compared with what the interface showed. Summing would count
    ground twice wherever two crowns overlap.
    """
    if not geometries:
        return 0.0
    merged = geo.repair(unary_union(geometries))
    return float(merged.area) if merged is not None else 0.0


def _harmonic_mean(precision: float | None, recall: float | None) -> float | None:
    if precision is None or recall is None or precision + recall == 0:
        return None
    return 2 * precision * recall / (precision + recall)


def _empty_report(iou_threshold: float, crs: CRS) -> ValidationReport:
    return ValidationReport(
        iou_threshold=iou_threshold,
        truth_count=0,
        detection_count=0,
        matched=0,
        precision=None,
        recall=None,
        f1=None,
        mean_matched_iou=None,
        truth_area_m2=0.0,
        detected_area_m2=0.0,
        area_ratio=None,
        measurement_crs=_label(crs),
    )


def _label(crs: CRS) -> str:
    code = crs.to_epsg()
    return f"EPSG:{code}" if code else (crs.name or "unknown")


def _rounded(value: float | None, digits: int) -> float | None:
    return None if value is None else round(value, digits)


# --- reading crowns from a file -------------------------------------------


def load_crowns(path: Path) -> tuple[list[BaseGeometry], CRS]:
    """Read one polygon per crown, with the CRS they are in.

    GeoJSON is read feature by feature rather than through the boundary loader,
    which combines what it reads into a single geometry. That is right for a
    boundary and wrong here: two truth crowns whose canopies touch would be
    merged into one, and the count this is all built on would be off before any
    scoring started.

    KML and KMZ go through the boundary loader and are then split back into
    their parts, which recovers separate crowns except where two of them
    overlap. GeoJSON is the format to prefer, and it is the one this
    application exports.
    """
    if path.suffix.lower() in GEOJSON_SUFFIXES:
        return _load_geojson_crowns(path)

    loaded = load_boundary(path)
    parts = geo.all_polygons(loaded.geometry)
    if parts is None:
        raise InvalidBoundaryError(
            f"{path.name} contains no polygons to score against.", detail={"file": path.name}
        )
    return (list(parts.geoms) if hasattr(parts, "geoms") else [parts]), loaded.crs


def _load_geojson_crowns(path: Path) -> tuple[list[BaseGeometry], CRS]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise InvalidBoundaryError(
            f"{path.name} could not be read as GeoJSON: {error}", detail={"file": path.name}
        ) from error

    crs, warnings = geojson_crs(payload)
    for warning in warnings:
        log.warning("%s: %s", path.name, warning)

    crowns: list[BaseGeometry] = []
    for geometry in _geometries_in(payload):
        try:
            parsed = shape(geometry)
        except (ValueError, AttributeError, KeyError):
            continue
        repaired = geo.repair(parsed)
        if repaired is not None and repaired.area > 0:
            crowns.append(repaired)

    if not crowns:
        raise InvalidBoundaryError(
            f"{path.name} contains no polygons to score against.", detail={"file": path.name}
        )
    return crowns, crs


def _geometries_in(payload: dict) -> list[dict]:
    """Every geometry in a FeatureCollection, a Feature, or a bare geometry."""
    kind = payload.get("type")
    if kind == "FeatureCollection":
        return [f.get("geometry") for f in payload.get("features", []) if f.get("geometry")]
    if kind == "Feature":
        return [payload["geometry"]] if payload.get("geometry") else []
    if kind == "GeometryCollection":
        return list(payload.get("geometries", []))
    return [payload] if kind else []
