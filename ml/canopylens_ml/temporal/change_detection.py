"""Temporal canopy change detection and crown tracking between two observation dates.

Compares:
- Crown detections between T1 (earlier) and T2 (later)
- Spatial persistence, mortality/loss, and new recruitment
- Canopy area gain, loss, and disturbance
- GeoJSON and CSV exports
"""

from __future__ import annotations

import csv
import io
import json
import logging
import math
from dataclasses import asdict, dataclass, field
from typing import Any, Sequence

try:
    from shapely.geometry import Polygon, box, mapping, shape
    from shapely.ops import unary_union
    _HAS_SHAPELY = True
except ImportError:
    _HAS_SHAPELY = False

from ..geometry import bounding_box, polygon_area
from ..evaluation.metrics import polygon_iou

log = logging.getLogger(__name__)

TEMPORAL_LIMITATIONS_NOTICE = (
    "Change detection figures indicate apparent canopy differences between two observation dates. "
    "Apparent change may be influenced by differences in sun angle, shadow, seasonal phenology (leaf-on vs leaf-off), "
    "sensor resolution, or spatial co-registration inaccuracies. Results must be ground-verified before attributing "
    "change to deforestation or permanent tree mortality."
)


@dataclass
class ChangePolygon:
    """A spatial change feature (gain, loss, disturbance)."""

    id: str
    change_type: str  # "loss", "gain", "disturbance"
    area_m2: float
    confidence: float
    coordinates_wgs84: list[tuple[float, float]] | None = None
    coordinates_px: list[tuple[float, float]] | None = None
    attributes: dict[str, Any] = field(default_factory=dict)

    def to_geojson_feature(self) -> dict[str, Any]:
        coords = self.coordinates_wgs84 or self.coordinates_px or []
        # Close ring if open
        ring = list(coords)
        if len(ring) > 0 and ring[0] != ring[-1]:
            ring.append(ring[0])
        return {
            "type": "Feature",
            "id": self.id,
            "properties": {
                "change_type": self.change_type,
                "area_m2": round(self.area_m2, 2),
                "confidence": round(self.confidence, 4),
                **self.attributes,
            },
            "geometry": {
                "type": "Polygon",
                "coordinates": [ring],
            },
        }


@dataclass
class TemporalCrownMatch:
    """Association between a tree crown across observation times T1 and T2."""

    t1_id: str | None
    t2_id: str | None
    status: str  # "persistent", "lost", "recruited", "disturbed"
    iou: float
    area_t1_m2: float | None
    area_t2_m2: float | None
    delta_area_m2: float | None
    delta_area_pct: float | None
    delta_vegetation_index: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "t1_id": self.t1_id,
            "t2_id": self.t2_id,
            "status": self.status,
            "iou": round(self.iou, 4),
            "area_t1_m2": round(self.area_t1_m2, 2) if self.area_t1_m2 is not None else None,
            "area_t2_m2": round(self.area_t2_m2, 2) if self.area_t2_m2 is not None else None,
            "delta_area_m2": round(self.delta_area_m2, 2) if self.delta_area_m2 is not None else None,
            "delta_area_pct": round(self.delta_area_pct, 2) if self.delta_area_pct is not None else None,
            "delta_index": round(self.delta_vegetation_index, 4) if self.delta_vegetation_index is not None else None,
        }


@dataclass
class TemporalChangeResult:
    """Summary and spatial geometry of temporal canopy change."""

    t1_crown_count: int
    t2_crown_count: int
    persistent_count: int
    lost_crown_count: int
    recruited_crown_count: int
    disturbed_crown_count: int

    t1_total_canopy_m2: float
    t2_total_canopy_m2: float
    canopy_loss_m2: float
    canopy_gain_m2: float
    canopy_disturbance_m2: float
    net_canopy_change_m2: float
    net_change_pct: float

    crown_matches: list[TemporalCrownMatch] = field(default_factory=list)
    change_polygons: list[ChangePolygon] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notice: str = TEMPORAL_LIMITATIONS_NOTICE

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": {
                "t1_crown_count": self.t1_crown_count,
                "t2_crown_count": self.t2_crown_count,
                "persistent_trees": self.persistent_count,
                "lost_trees": self.lost_crown_count,
                "recruited_trees": self.recruited_crown_count,
                "disturbed_trees": self.disturbed_crown_count,
                "t1_canopy_area_m2": round(self.t1_total_canopy_m2, 2),
                "t2_canopy_area_m2": round(self.t2_total_canopy_m2, 2),
                "canopy_loss_area_m2": round(self.canopy_loss_m2, 2),
                "canopy_gain_area_m2": round(self.canopy_gain_m2, 2),
                "disturbance_area_m2": round(self.canopy_disturbance_m2, 2),
                "net_canopy_change_m2": round(self.net_canopy_change_m2, 2),
                "net_change_pct": round(self.net_change_pct, 2),
            },
            "crown_changes": [m.to_dict() for m in self.crown_matches],
            "total_change_polygons": len(self.change_polygons),
            "warnings": self.warnings,
            "notice": self.notice,
        }


def detect_canopy_change(
    t1_crowns: Sequence[dict],
    t2_crowns: Sequence[dict],
    min_matching_iou: float = 0.20,
    disturbance_index_drop_threshold: float = -0.15,
) -> TemporalChangeResult:
    """Track crowns between observation dates T1 and T2 and quantify change.

    `t1_crowns` and `t2_crowns` are lists of dictionaries containing:
    - id: str
    - polygon_wgs84 or polygon_px: list[tuple[float, float]]
    - area_m2: float
    - confidence: float
    - vegetation_index: float (optional)
    """
    warnings: list[str] = []

    n_t1 = len(t1_crowns)
    n_t2 = len(t2_crowns)

    tot_area_t1 = sum(float(c.get("area_m2", 0.0) or c.get("pixel_area", 0.0)) for c in t1_crowns)
    tot_area_t2 = sum(float(c.get("area_m2", 0.0) or c.get("pixel_area", 0.0)) for c in t2_crowns)

    # Spatial matching: greedy best IoU
    candidates = []
    for idx1, c1 in enumerate(t1_crowns):
        p1 = c1.get("polygon_wgs84") or c1.get("polygon_px") or c1.get("polygon")
        if not p1:
            continue
        for idx2, c2 in enumerate(t2_crowns):
            p2 = c2.get("polygon_wgs84") or c2.get("polygon_px") or c2.get("polygon")
            if not p2:
                continue
            iou = polygon_iou(p1, p2)
            if iou >= min_matching_iou:
                candidates.append((iou, idx1, idx2))

    candidates.sort(key=lambda x: x[0], reverse=True)

    matched_t1: dict[int, tuple[int, float]] = {}
    matched_t2: dict[int, tuple[int, float]] = {}

    for iou, i1, i2 in candidates:
        if i1 in matched_t1 or i2 in matched_t2:
            continue
        matched_t1[i1] = (i2, iou)
        matched_t2[i2] = (i1, iou)

    crown_matches: list[TemporalCrownMatch] = []
    change_polygons: list[ChangePolygon] = []

    loss_area = 0.0
    gain_area = 0.0
    disturbance_area = 0.0

    persistent_count = 0
    disturbed_count = 0

    # 1. Process T1 crowns: Persistent, Disturbed, or Lost
    for i1, c1 in enumerate(t1_crowns):
        cid1 = c1.get("id", f"t1_{i1}")
        area1 = float(c1.get("area_m2", 0.0) or c1.get("pixel_area", 0.0))
        poly1 = c1.get("polygon_wgs84") or c1.get("polygon_px") or c1.get("polygon")
        v1 = c1.get("vegetation_index", None)

        if i1 in matched_t1:
            i2, iou = matched_t1[i1]
            c2 = t2_crowns[i2]
            cid2 = c2.get("id", f"t2_{i2}")
            area2 = float(c2.get("area_m2", 0.0) or c2.get("pixel_area", 0.0))
            v2 = c2.get("vegetation_index", None)

            delta_a = area2 - area1
            delta_pct = (delta_a / area1 * 100.0) if area1 > 0 else 0.0
            delta_v = (float(v2) - float(v1)) if (v1 is not None and v2 is not None) else None

            # Disturbance check (significant vegetation decline or >50% crown loss)
            is_disturbed = (delta_v is not None and delta_v <= disturbance_index_drop_threshold) or (delta_pct < -50.0)

            status = "disturbed" if is_disturbed else "persistent"
            if is_disturbed:
                disturbed_count += 1
                disturbance_area += max(0.0, -delta_a)
                change_polygons.append(
                    ChangePolygon(
                        id=f"disturb_{cid1}_{cid2}",
                        change_type="disturbance",
                        area_m2=max(0.0, -delta_a),
                        confidence=round(iou, 3),
                        coordinates_wgs84=c2.get("polygon_wgs84"),
                        coordinates_px=c2.get("polygon_px"),
                        attributes={"t1_id": cid1, "t2_id": cid2, "delta_area_pct": round(delta_pct, 1)},
                    )
                )
            else:
                persistent_count += 1

            crown_matches.append(
                TemporalCrownMatch(
                    t1_id=cid1,
                    t2_id=cid2,
                    status=status,
                    iou=iou,
                    area_t1_m2=area1,
                    area_t2_m2=area2,
                    delta_area_m2=delta_a,
                    delta_area_pct=delta_pct,
                    delta_vegetation_index=delta_v,
                )
            )
        else:
            # Tree lost (mortality or harvest)
            loss_area += area1
            crown_matches.append(
                TemporalCrownMatch(
                    t1_id=cid1,
                    t2_id=None,
                    status="lost",
                    iou=0.0,
                    area_t1_m2=area1,
                    area_t2_m2=0.0,
                    delta_area_m2=-area1,
                    delta_area_pct=-100.0,
                )
            )
            change_polygons.append(
                ChangePolygon(
                    id=f"loss_{cid1}",
                    change_type="loss",
                    area_m2=area1,
                    confidence=float(c1.get("confidence", 0.8)),
                    coordinates_wgs84=c1.get("polygon_wgs84"),
                    coordinates_px=c1.get("polygon_px"),
                    attributes={"t1_id": cid1},
                )
            )

    # 2. Process unmatched T2 crowns: Recruited / Canopy Gain
    lost_count = n_t1 - len(matched_t1)
    recruited_count = 0

    for i2, c2 in enumerate(t2_crowns):
        if i2 not in matched_t2:
            cid2 = c2.get("id", f"t2_{i2}")
            area2 = float(c2.get("area_m2", 0.0) or c2.get("pixel_area", 0.0))
            gain_area += area2
            recruited_count += 1

            crown_matches.append(
                TemporalCrownMatch(
                    t1_id=None,
                    t2_id=cid2,
                    status="recruited",
                    iou=0.0,
                    area_t1_m2=0.0,
                    area_t2_m2=area2,
                    delta_area_m2=area2,
                    delta_area_pct=100.0,
                )
            )
            change_polygons.append(
                ChangePolygon(
                    id=f"gain_{cid2}",
                    change_type="gain",
                    area_m2=area2,
                    confidence=float(c2.get("confidence", 0.8)),
                    coordinates_wgs84=c2.get("polygon_wgs84"),
                    coordinates_px=c2.get("polygon_px"),
                    attributes={"t2_id": cid2},
                )
            )

    net_change = gain_area - loss_area
    net_pct = (net_change / tot_area_t1 * 100.0) if tot_area_t1 > 0 else 0.0

    if abs(tot_area_t1 - tot_area_t2) > 0.4 * max(tot_area_t1, tot_area_t2, 1.0):
        warnings.append(
            "Large difference (>40%) in total detected canopy area between observation dates. "
            "Inspect potential sensor resolution or seasonal differences."
        )

    return TemporalChangeResult(
        t1_crown_count=n_t1,
        t2_crown_count=n_t2,
        persistent_count=persistent_count,
        lost_crown_count=lost_count,
        recruited_crown_count=recruited_count,
        disturbed_crown_count=disturbed_count,
        t1_total_canopy_m2=tot_area_t1,
        t2_total_canopy_m2=tot_area_t2,
        canopy_loss_m2=loss_area,
        canopy_gain_m2=gain_area,
        canopy_disturbance_m2=disturbance_area,
        net_canopy_change_m2=net_change,
        net_change_pct=net_pct,
        crown_matches=crown_matches,
        change_polygons=change_polygons,
        warnings=warnings,
    )


def export_change_geojson(result: TemporalChangeResult) -> dict[str, Any]:
    """Generate GeoJSON FeatureCollection of canopy change polygons."""
    features = [p.to_geojson_feature() for p in result.change_polygons]
    return {
        "type": "FeatureCollection",
        "properties": {
            "notice": result.notice,
            "summary": result.to_dict()["summary"],
        },
        "features": features,
    }


def export_change_csv(result: TemporalChangeResult) -> str:
    """Generate CSV summary of temporal crown tracking."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "t1_id",
        "t2_id",
        "status",
        "iou",
        "area_t1_m2",
        "area_t2_m2",
        "delta_area_m2",
        "delta_area_pct",
        "delta_index",
    ])
    for m in result.crown_matches:
        writer.writerow([
            m.t1_id or "",
            m.t2_id or "",
            m.status,
            round(m.iou, 4),
            round(m.area_t1_m2, 2) if m.area_t1_m2 is not None else "",
            round(m.area_t2_m2, 2) if m.area_t2_m2 is not None else "",
            round(m.delta_area_m2, 2) if m.delta_area_m2 is not None else "",
            round(m.delta_area_pct, 2) if m.delta_area_pct is not None else "",
            round(m.delta_vegetation_index, 4) if m.delta_vegetation_index is not None else "",
        ])
    return buf.getvalue()
