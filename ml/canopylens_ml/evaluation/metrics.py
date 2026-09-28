"""Quantitative evaluation metrics for tree crown detection and instance segmentation.

Covers:
- Precision, Recall, F1 score
- Average Precision (AP), mAP@50, mAP@75, mAP@50:95
- Box IoU, Polygon Mask IoU, Dice Coefficient, Boundary IoU
- Tree count MAE and RMSE
- Crown area MAE and agreement ratio
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

try:
    from shapely.geometry import Polygon
    from shapely.validation import make_valid
    _HAS_SHAPELY = True
except ImportError:
    _HAS_SHAPELY = False


Box = tuple[float, float, float, float]  # (xmin, ymin, xmax, ymax)


def box_iou(box_a: Box, box_b: Box) -> float:
    """Compute Intersection over Union between two axis-aligned bounding boxes (xmin, ymin, xmax, ymax)."""
    x1 = max(box_a[0], box_b[0])
    y1 = max(box_a[1], box_b[1])
    x2 = min(box_a[2], box_b[2])
    y2 = min(box_a[3], box_b[3])

    intersection_w = max(0.0, x2 - x1)
    intersection_h = max(0.0, y2 - y1)
    intersection_area = intersection_w * intersection_h

    if intersection_area <= 0.0:
        return 0.0

    area_a = max(0.0, box_a[2] - box_a[0]) * max(0.0, box_a[3] - box_a[1])
    area_b = max(0.0, box_b[2] - box_b[0]) * max(0.0, box_b[3] - box_b[1])
    union_area = area_a + area_b - intersection_area

    if union_area <= 0.0:
        return 0.0
    return float(intersection_area / union_area)


def _ensure_polygon(geom) -> Polygon | None:
    if not _HAS_SHAPELY:
        return None
    if isinstance(geom, Polygon):
        poly = geom
    elif isinstance(geom, (list, tuple, np.ndarray)):
        if len(geom) < 3:
            return None
        poly = Polygon(geom)
    else:
        return None

    if not poly.is_valid:
        try:
            poly = make_valid(poly)
            if hasattr(poly, "geoms"):
                # Take largest component if collection
                polys = [p for p in poly.geoms if isinstance(p, Polygon)]
                if not polys:
                    return None
                poly = max(polys, key=lambda p: p.area)
        except Exception:
            return None
    return poly if isinstance(poly, Polygon) and not poly.is_empty else None


def polygon_iou(poly_a, poly_b) -> float:
    """Compute Intersection over Union between two polygon geometries or coordinate rings."""
    pa = _ensure_polygon(poly_a)
    pb = _ensure_polygon(poly_b)
    if pa is None or pb is None:
        return 0.0

    # Fast reject if bounding boxes do not intersect
    minx1, miny1, maxx1, maxy1 = pa.bounds
    minx2, miny2, maxx2, maxy2 = pb.bounds
    if maxx1 < minx2 or maxx2 < minx1 or maxy1 < miny2 or maxy2 < miny1:
        return 0.0

    try:
        intersection_area = pa.intersection(pb).area
        if intersection_area <= 0.0:
            return 0.0
        union_area = pa.area + pb.area - intersection_area
        return float(intersection_area / union_area) if union_area > 0 else 0.0
    except Exception:
        return 0.0


def dice_coefficient(poly_a, poly_b) -> float:
    """Compute Dice / F1 score between two polygon masks (2 * |A n B| / (|A| + |B|))."""
    pa = _ensure_polygon(poly_a)
    pb = _ensure_polygon(poly_b)
    if pa is None or pb is None:
        return 0.0

    try:
        intersection_area = pa.intersection(pb).area
        total_area = pa.area + pb.area
        if total_area <= 0.0:
            return 0.0
        return float(2.0 * intersection_area / total_area)
    except Exception:
        return 0.0


def boundary_iou(poly_a, poly_b, buffer_width: float = 2.0) -> float:
    """Compute Boundary IoU to evaluate crown boundary delineation accuracy.

    Buffers the exterior boundary line of both polygons by `buffer_width`
    and computes IoU of the boundary bands.
    """
    pa = _ensure_polygon(poly_a)
    pb = _ensure_polygon(poly_b)
    if pa is None or pb is None:
        return 0.0

    try:
        band_a = pa.exterior.buffer(buffer_width)
        band_b = pb.exterior.buffer(buffer_width)
        inter = band_a.intersection(band_b).area
        union = band_a.union(band_b).area
        return float(inter / union) if union > 0 else 0.0
    except Exception:
        return 0.0


@dataclass(frozen=True)
class DetectionMetrics:
    precision: float
    recall: float
    f1: float
    true_positives: int
    false_positives: int
    false_negatives: int
    mean_iou: float


def compute_detection_metrics(
    matched_ious: Sequence[float],
    num_predictions: int,
    num_ground_truths: int,
) -> DetectionMetrics:
    """Calculate standard precision, recall, and F1 at a specific IoU threshold."""
    tp = len(matched_ious)
    fp = max(0, num_predictions - tp)
    fn = max(0, num_ground_truths - tp)

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = (2.0 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0
    mean_iou = float(np.mean(matched_ious)) if tp > 0 else 0.0

    return DetectionMetrics(
        precision=float(precision),
        recall=float(recall),
        f1=float(f1),
        true_positives=tp,
        false_positives=fp,
        false_negatives=fn,
        mean_iou=mean_iou,
    )


def compute_ap(recalls: Sequence[float], precisions: Sequence[float]) -> float:
    """Compute Average Precision using COCO/VOC all-point area under PR curve."""
    if len(recalls) == 0 or len(precisions) == 0:
        return 0.0

    # Ensure precision is monotonically decreasing
    mrec = np.concatenate(([0.0], recalls, [1.0]))
    mpre = np.concatenate(([1.0], precisions, [0.0]))

    for i in range(len(mpre) - 2, -1, -1):
        mpre[i] = max(mpre[i], mpre[i + 1])

    # Find points where recall changes
    indices = np.where(mrec[1:] != mrec[:-1])[0]
    ap = float(np.sum((mrec[indices + 1] - mrec[indices]) * mpre[indices + 1]))
    return max(0.0, min(1.0, ap))


def count_mae(truth_counts: Sequence[int | float], pred_counts: Sequence[int | float]) -> float:
    """Mean Absolute Error of tree counts across images or plots."""
    if not truth_counts or not pred_counts or len(truth_counts) != len(pred_counts):
        return 0.0
    t = np.asarray(truth_counts, dtype=np.float64)
    p = np.asarray(pred_counts, dtype=np.float64)
    return float(np.mean(np.abs(t - p)))


def count_rmse(truth_counts: Sequence[int | float], pred_counts: Sequence[int | float]) -> float:
    """Root Mean Squared Error of tree counts across images or plots."""
    if not truth_counts or not pred_counts or len(truth_counts) != len(pred_counts):
        return 0.0
    t = np.asarray(truth_counts, dtype=np.float64)
    p = np.asarray(pred_counts, dtype=np.float64)
    return float(math.sqrt(np.mean((t - p) ** 2)))


def area_mae(truth_areas: Sequence[float], pred_areas: Sequence[float]) -> float:
    """Mean Absolute Error of crown areas."""
    if not truth_areas or not pred_areas or len(truth_areas) != len(pred_areas):
        return 0.0
    t = np.asarray(truth_areas, dtype=np.float64)
    p = np.asarray(pred_areas, dtype=np.float64)
    return float(np.mean(np.abs(t - p)))


@dataclass(frozen=True)
class MapScore:
    ap50: float
    ap75: float
    map50_95: float
    threshold_aps: dict[float, float]


def compute_map(
    matched_results_per_threshold: dict[float, tuple[Sequence[float], int, int]],
) -> MapScore:
    """Calculate mAP@50, mAP@75, and mAP@50:95 across a dictionary of IoU thresholds.

    `matched_results_per_threshold` maps iou_thresh -> (recalls, precisions) or
    precomputed AP values.
    """
    threshold_aps: dict[float, float] = {}

    for thresh, (recalls, precisions, _) in matched_results_per_threshold.items():
        ap = compute_ap(recalls, precisions)
        threshold_aps[round(thresh, 2)] = ap

    ap50 = threshold_aps.get(0.50, 0.0)
    ap75 = threshold_aps.get(0.75, 0.0)
    mean_map = float(np.mean(list(threshold_aps.values()))) if threshold_aps else 0.0

    return MapScore(
        ap50=ap50,
        ap75=ap75,
        map50_95=mean_map,
        threshold_aps=threshold_aps,
    )
