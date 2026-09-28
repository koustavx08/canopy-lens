"""Model evaluator comparing predictions against ground-truth tree crowns.

Implements:
- Multi-threshold IoU matching (0.50 to 0.95 in 0.05 steps)
- Standard COCO-style mAP@50, mAP@75, mAP@50:95
- Detection Precision, Recall, F1
- Instance mask IoU, Dice coefficient, boundary IoU
- Tree count MAE and RMSE
- Crown area MAE and agreement ratio
- Sub-cohort evaluation across forest types
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Sequence

import numpy as np

from .dataset import Annotation, EvaluationDataset
from .metrics import (
    DetectionMetrics,
    MapScore,
    area_mae,
    boundary_iou,
    box_iou,
    compute_ap,
    compute_detection_metrics,
    count_mae,
    count_rmse,
    dice_coefficient,
    polygon_iou,
)

log = logging.getLogger(__name__)

DEFAULT_IOU_THRESHOLDS = (0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95)


@dataclass(frozen=True)
class MatchRecord:
    truth_id: str
    prediction_id: str
    iou: float
    confidence: float


@dataclass
class EvaluationResult:
    """Comprehensive evaluation metrics for a model on a dataset."""

    model_name: str
    dataset_name: str
    total_images: int
    total_ground_truth_trees: int
    total_predicted_trees: int
    map50: float
    map75: float
    map50_95: float
    metrics_at_50: DetectionMetrics
    metrics_at_75: DetectionMetrics
    count_mae: float
    count_rmse: float
    area_mae_px: float
    area_ratio: float
    segmentation_metrics: dict[str, float] = field(default_factory=dict)
    forest_type_breakdown: dict[str, dict] = field(default_factory=dict)
    threshold_metrics: dict[float, dict] = field(default_factory=dict)
    matches_at_50: list[MatchRecord] = field(default_factory=list)
    unmatched_ground_truth_ids: list[str] = field(default_factory=list)
    unmatched_prediction_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "model_name": self.model_name,
            "dataset_name": self.dataset_name,
            "summary": {
                "total_images": self.total_images,
                "ground_truth_trees": self.total_ground_truth_trees,
                "predicted_trees": self.total_predicted_trees,
                "map50": round(self.map50, 4),
                "map75": round(self.map75, 4),
                "map50_95": round(self.map50_95, 4),
                "precision_50": round(self.metrics_at_50.precision, 4),
                "recall_50": round(self.metrics_at_50.recall, 4),
                "f1_50": round(self.metrics_at_50.f1, 4),
                "count_mae": round(self.count_mae, 2),
                "count_rmse": round(self.count_rmse, 2),
                "area_ratio": round(self.area_ratio, 4),
            },
            "metrics_at_50": {
                "precision": round(self.metrics_at_50.precision, 4),
                "recall": round(self.metrics_at_50.recall, 4),
                "f1": round(self.metrics_at_50.f1, 4),
                "tp": self.metrics_at_50.true_positives,
                "fp": self.metrics_at_50.false_positives,
                "fn": self.metrics_at_50.false_negatives,
                "mean_iou": round(self.metrics_at_50.mean_iou, 4),
            },
            "segmentation": {
                k: round(v, 4) for k, v in self.segmentation_metrics.items()
            },
            "forest_types": self.forest_type_breakdown,
            "threshold_map": {
                str(k): round(v, 4) for k, v in self.threshold_metrics.items()
            },
        }


class ForestModelEvaluator:
    """Evaluates tree crown detection and segmentation performance."""

    def __init__(
        self,
        iou_thresholds: Sequence[float] = DEFAULT_IOU_THRESHOLDS,
        use_polygons: bool = True,
    ):
        self.iou_thresholds = tuple(round(t, 2) for t in iou_thresholds)
        self.use_polygons = use_polygons

    def _iou_fn(self, truth: Annotation, pred: Annotation) -> float:
        if self.use_polygons and truth.polygon and pred.polygon:
            piou = polygon_iou(truth.polygon, pred.polygon)
            if piou > 0.0:
                return piou
        return box_iou(truth.bbox, pred.bbox)

    def evaluate_predictions(
        self,
        ground_truths_by_image: dict[str, list[Annotation]],
        predictions_by_image: dict[str, list[Annotation]],
        model_name: str = "model",
        dataset_name: str = "benchmark",
        forest_types_by_image: dict[str, str] | None = None,
    ) -> EvaluationResult:
        """Run benchmark evaluation comparing predictions against ground truth."""
        forest_types = forest_types_by_image or {}
        image_ids = sorted(set(ground_truths_by_image.keys()) | set(predictions_by_image.keys()))

        total_gt = sum(len(ground_truths_by_image.get(img_id, [])) for img_id in image_ids)
        total_pred = sum(len(predictions_by_image.get(img_id, [])) for img_id in image_ids)

        truth_counts = []
        pred_counts = []
        truth_areas = []
        pred_areas = []

        for img_id in image_ids:
            gts = ground_truths_by_image.get(img_id, [])
            preds = predictions_by_image.get(img_id, [])
            truth_counts.append(len(gts))
            pred_counts.append(len(preds))
            truth_areas.append(sum(g.area_px for g in gts))
            pred_areas.append(sum(p.area_px for p in preds))

        mae_cnt = count_mae(truth_counts, pred_counts)
        rmse_cnt = count_rmse(truth_counts, pred_counts)
        tot_gt_area = sum(truth_areas)
        tot_pred_area = sum(pred_areas)
        ratio_area = (tot_pred_area / tot_gt_area) if tot_gt_area > 0 else 1.0

        threshold_aps: dict[float, float] = {}
        threshold_metrics_dict: dict[float, dict] = {}
        matches_at_50: list[MatchRecord] = []
        unmatched_gt_50: list[str] = []
        unmatched_pred_50: list[str] = []

        mask_ious: list[float] = []
        dice_scores: list[float] = []
        b_ious: list[float] = []

        for thresh in self.iou_thresholds:
            all_pred_matches: list[tuple[float, bool]] = []  # (confidence, is_tp)
            thresh_matched_ious: list[float] = []

            for img_id in image_ids:
                gts = ground_truths_by_image.get(img_id, [])
                preds = predictions_by_image.get(img_id, [])

                # Sort predictions descending by confidence score
                sorted_preds = sorted(
                    preds,
                    key=lambda p: (p.confidence if p.confidence is not None else 1.0),
                    reverse=True,
                )

                matched_gt_indices: set[int] = set()

                for pred in sorted_preds:
                    conf = pred.confidence if pred.confidence is not None else 1.0
                    best_iou = 0.0
                    best_gt_idx = -1

                    for gt_idx, gt in enumerate(gts):
                        if gt_idx in matched_gt_indices:
                            continue
                        cur_iou = self._iou_fn(gt, pred)
                        if cur_iou > best_iou:
                            best_iou = cur_iou
                            best_gt_idx = gt_idx

                    if best_iou >= thresh and best_gt_idx >= 0:
                        matched_gt_indices.add(best_gt_idx)
                        all_pred_matches.append((conf, True))
                        thresh_matched_ious.append(best_iou)

                        if abs(thresh - 0.50) < 1e-4:
                            gt_match = gts[best_gt_idx]
                            matches_at_50.append(
                                MatchRecord(
                                    truth_id=gt_match.id,
                                    prediction_id=pred.id,
                                    iou=best_iou,
                                    confidence=conf,
                                )
                            )
                            if self.use_polygons and gt_match.polygon and pred.polygon:
                                mask_ious.append(best_iou)
                                dice_scores.append(dice_coefficient(gt_match.polygon, pred.polygon))
                                b_ious.append(boundary_iou(gt_match.polygon, pred.polygon))
                    else:
                        all_pred_matches.append((conf, False))
                        if abs(thresh - 0.50) < 1e-4:
                            unmatched_pred_50.append(pred.id)

                if abs(thresh - 0.50) < 1e-4:
                    for gt_idx, gt in enumerate(gts):
                        if gt_idx not in matched_gt_indices:
                            unmatched_gt_50.append(gt.id)

            # Compute precision-recall curve for AP
            all_pred_matches.sort(key=lambda x: x[0], reverse=True)
            tp_cumsum = 0
            fp_cumsum = 0
            precisions = []
            recalls = []

            for _, is_tp in all_pred_matches:
                if is_tp:
                    tp_cumsum += 1
                else:
                    fp_cumsum += 1
                prec = tp_cumsum / (tp_cumsum + fp_cumsum)
                rec = tp_cumsum / total_gt if total_gt > 0 else 0.0
                precisions.append(prec)
                recalls.append(rec)

            ap = compute_ap(recalls, precisions)
            threshold_aps[thresh] = ap

            t_metrics = compute_detection_metrics(thresh_matched_ious, total_pred, total_gt)
            threshold_metrics_dict[thresh] = {
                "ap": round(ap, 4),
                "precision": round(t_metrics.precision, 4),
                "recall": round(t_metrics.recall, 4),
                "f1": round(t_metrics.f1, 4),
            }

        map50 = threshold_aps.get(0.50, 0.0)
        map75 = threshold_aps.get(0.75, 0.0)
        map50_95 = float(np.mean(list(threshold_aps.values()))) if threshold_aps else 0.0

        metrics_50 = compute_detection_metrics(
            [m.iou for m in matches_at_50], total_pred, total_gt
        )
        # Compute metrics at 75
        matched_75_ious = [
            m.iou for m in matches_at_50 if m.iou >= 0.75
        ]
        metrics_75 = compute_detection_metrics(matched_75_ious, total_pred, total_gt)

        seg_metrics = {}
        if mask_ious:
            seg_metrics["mean_mask_iou"] = float(np.mean(mask_ious))
            seg_metrics["mean_dice"] = float(np.mean(dice_scores))
            seg_metrics["mean_boundary_iou"] = float(np.mean(b_ious))

        # Forest-type breakdown
        forest_breakdown: dict[str, dict] = {}
        distinct_forests = set(forest_types.values())
        for ftype in distinct_forests:
            ftype_images = [img_id for img_id, ft in forest_types.items() if ft == ftype]
            if not ftype_images:
                continue
            sub_gt = {img_id: ground_truths_by_image.get(img_id, []) for img_id in ftype_images}
            sub_pred = {img_id: predictions_by_image.get(img_id, []) for img_id in ftype_images}
            sub_n_gt = sum(len(g) for g in sub_gt.values())
            sub_n_pred = sum(len(p) for p in sub_pred.values())

            # Evaluate at 0.50
            sub_matches = [
                m for m in matches_at_50
                if any(m.prediction_id in [p.id for p in sub_pred.get(img, [])] for img in ftype_images)
            ]
            sub_det_metrics = compute_detection_metrics(
                [m.iou for m in sub_matches], sub_n_pred, sub_n_gt
            )
            forest_breakdown[ftype] = {
                "ground_truth_trees": sub_n_gt,
                "predicted_trees": sub_n_pred,
                "precision": round(sub_det_metrics.precision, 4),
                "recall": round(sub_det_metrics.recall, 4),
                "f1": round(sub_det_metrics.f1, 4),
                "mean_iou": round(sub_det_metrics.mean_iou, 4),
            }

        return EvaluationResult(
            model_name=model_name,
            dataset_name=dataset_name,
            total_images=len(image_ids),
            total_ground_truth_trees=total_gt,
            total_predicted_trees=total_pred,
            map50=map50,
            map75=map75,
            map50_95=map50_95,
            metrics_at_50=metrics_50,
            metrics_at_75=metrics_75,
            count_mae=mae_cnt,
            count_rmse=rmse_cnt,
            area_mae_px=area_mae(truth_areas, pred_areas),
            area_ratio=ratio_area,
            segmentation_metrics=seg_metrics,
            forest_type_breakdown=forest_breakdown,
            threshold_metrics=threshold_metrics_dict,
            matches_at_50=matches_at_50,
            unmatched_ground_truth_ids=unmatched_gt_50,
            unmatched_prediction_ids=unmatched_pred_50,
        )
