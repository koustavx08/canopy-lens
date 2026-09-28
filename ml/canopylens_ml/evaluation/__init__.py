"""ML Evaluation Framework for CanopyLens.

Provides detection and segmentation metrics, dataset parsing (COCO, CSV, GeoJSON),
spatial splitting, multi-threshold mAP evaluation, and benchmark reporting.
"""

from .metrics import (
    boundary_iou,
    box_iou,
    compute_ap,
    compute_detection_metrics,
    compute_map,
    count_mae,
    count_rmse,
    dice_coefficient,
    polygon_iou,
)
from .dataset import (
    Annotation,
    AnnotatedImage,
    EvaluationDataset,
    load_coco_dataset,
    load_csv_dataset,
    load_geojson_dataset,
    spatial_block_split,
)
from .evaluator import ForestModelEvaluator, EvaluationResult
from .reports import EvaluationReport, BenchmarkComparison

__all__ = [
    "AnnotatedImage",
    "Annotation",
    "BenchmarkComparison",
    "EvaluationDataset",
    "EvaluationReport",
    "EvaluationResult",
    "ForestModelEvaluator",
    "boundary_iou",
    "box_iou",
    "compute_ap",
    "compute_detection_metrics",
    "compute_map",
    "count_mae",
    "count_rmse",
    "dice_coefficient",
    "load_coco_dataset",
    "load_csv_dataset",
    "load_geojson_dataset",
    "polygon_iou",
    "spatial_block_split",
]
