"""Score a CanopyLens run against crowns somebody verified.

    python -m app.validate --truth crowns.geojson --detections run.geojson
    python -m app.validate --truth crowns.geojson --imagery scene.tif

The first form scores an export you already have - the file `/api/results/{id}/
geojson` hands back, or anything else holding one polygon per crown. The second
runs the analysis first, which is what you want when comparing engines or
settings against the same truth.

Ground truth is not supplied with this repository and is not generated here.
Delineate crowns over your own imagery in QGIS and export them as GeoJSON, one
feature per tree; that file is what makes the word "estimate" into a number.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .logging_config import configure_logging
from .services import geo
from .services.validation import DEFAULT_IOU_THRESHOLD, ValidationReport, load_crowns, score

log = logging.getLogger("canopylens.validate")


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    configure_logging("WARNING" if args.json else "INFO")

    truth, truth_crs = load_crowns(args.truth)
    log.info("Ground truth: %d crown(s) from %s", len(truth), args.truth.name)

    if args.detections:
        detections, detection_crs = load_crowns(args.detections)
        log.info("Detections: %d crown(s) from %s", len(detections), args.detections.name)
    else:
        detections, detection_crs = _analyse(args)

    # Both sides have to be in one coordinate system before anything is
    # compared. The truth file decides which, since it is the fixed point.
    if detection_crs != truth_crs:
        detections = [geo.to_crs(d, detection_crs, truth_crs) for d in detections]

    report = score(truth, detections, source_crs=truth_crs, iou_threshold=args.iou)

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(_readable(report))
    return 0


def _analyse(args) -> tuple[list, object]:
    """Run the analysis now, and take its crowns as the detections."""
    from .services.analysis import analyze_image

    log.info("Analysing %s with the %s engine", args.imagery.name, args.engine)
    result = analyze_image(
        args.imagery,
        args.boundary,
        confidence_threshold=args.confidence,
        min_crown_area_m2=args.min_crown_area_m2,
        engine=args.engine,
    )
    crowns = [c.geometry_wgs84 for c in result.crowns if c.geometry_wgs84 is not None]
    if len(crowns) < len(result.crowns):
        # Scoring pixel polygons against ground truth in degrees would compare
        # two different things and report a number for it.
        log.warning(
            "%d of %d crowns have no geographic geometry and cannot be scored; "
            "the imagery is not georeferenced.",
            len(result.crowns) - len(crowns),
            len(result.crowns),
        )
    log.info("Detected %d crown(s)", len(crowns))
    return crowns, geo.WGS84


def _readable(report: ValidationReport) -> str:
    lines = [
        "",
        f"Scored at IoU >= {report.iou_threshold:.2f}, measured in {report.measurement_crs}",
        "",
        f"  Ground truth crowns   {report.truth_count}",
        f"  Detected crowns       {report.detection_count}",
        f"  Matched               {report.matched}",
        f"  Missed (false neg.)   {report.false_negatives}",
        f"  Spurious (false pos.) {report.false_positives}",
        "",
        f"  Precision             {_figure(report.precision)}",
        f"  Recall                {_figure(report.recall)}",
        f"  F1                    {_figure(report.f1)}",
        f"  Mean IoU of matches   {_figure(report.mean_matched_iou)}",
        "",
        f"  Truth canopy area     {report.truth_area_m2:,.2f} m2",
        f"  Detected canopy area  {report.detected_area_m2:,.2f} m2",
        f"  Detected / truth      {_figure(report.area_ratio)}",
        "",
    ]
    if report.area_ratio is not None and report.area_ratio > 1.0:
        lines.append(
            f"  The run claims {(report.area_ratio - 1) * 100:.1f}% more canopy "
            "than the truth holds."
        )
        lines.append("")
    return "\n".join(lines)


def _figure(value: float | None) -> str:
    """A number, or a dash where there is no number to give.

    Undefined here means one side of the comparison was empty. Printing 0.000
    would read as a measured failure and 1.000 as a measured success, and it is
    neither.
    """
    return "—" if value is None else f"{value:.3f}"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.validate",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--truth",
        type=Path,
        required=True,
        help="GeoJSON, KML or KMZ holding one polygon per verified crown.",
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--detections", type=Path, help="A CanopyLens GeoJSON export to score as it stands."
    )
    source.add_argument(
        "--imagery", type=Path, help="Imagery to analyse now, and score the result of."
    )
    parser.add_argument("--boundary", type=Path, default=None, help="Optional boundary file.")
    parser.add_argument(
        "--engine", default="auto", help="Detection engine to run, or 'auto'. With --imagery."
    )
    parser.add_argument(
        "--confidence", type=float, default=0.30, help="Confidence threshold. With --imagery."
    )
    parser.add_argument(
        "--min-crown-area-m2",
        dest="min_crown_area_m2",
        type=float,
        default=1.0,
        help="Smallest crown to keep, in square metres. With --imagery.",
    )
    parser.add_argument(
        "--iou",
        type=float,
        default=DEFAULT_IOU_THRESHOLD,
        help=(
            "Overlap at which a detection counts as the same tree "
            f"(default {DEFAULT_IOU_THRESHOLD})."
        ),
    )
    parser.add_argument("--json", action="store_true", help="Print the report as JSON.")
    return parser


if __name__ == "__main__":
    sys.exit(main())
