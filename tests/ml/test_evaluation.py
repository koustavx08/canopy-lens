"""Tests for the ML Evaluation Framework."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from canopylens_ml.evaluation import (
    Annotation,
    AnnotatedImage,
    BenchmarkComparison,
    EvaluationDataset,
    EvaluationReport,
    ForestModelEvaluator,
    boundary_iou,
    box_iou,
    compute_ap,
    compute_detection_metrics,
    count_mae,
    count_rmse,
    dice_coefficient,
    load_coco_dataset,
    load_csv_dataset,
    load_geojson_dataset,
    polygon_iou,
    spatial_block_split,
)


class TestMetrics:
    def test_box_iou_identical(self):
        box = (10.0, 10.0, 50.0, 50.0)
        assert box_iou(box, box) == pytest.approx(1.0)

    def test_box_iou_disjoint(self):
        b1 = (0.0, 0.0, 10.0, 10.0)
        b2 = (20.0, 20.0, 30.0, 30.0)
        assert box_iou(b1, b2) == 0.0

    def test_box_iou_partial(self):
        b1 = (0.0, 0.0, 20.0, 20.0)  # Area 400
        b2 = (10.0, 0.0, 30.0, 20.0)  # Area 400
        # Intersection: (10, 0, 20, 20) -> Area 200
        # Union: 400 + 400 - 200 = 600
        # IoU = 200 / 600 = 1/3
        assert box_iou(b1, b2) == pytest.approx(1.0 / 3.0)

    def test_polygon_iou_and_dice(self):
        p1 = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]
        p2 = [(5.0, 0.0), (15.0, 0.0), (15.0, 10.0), (5.0, 10.0)]
        iou = polygon_iou(p1, p2)
        # Inter = 50, union = 150 -> 1/3
        assert iou == pytest.approx(1.0 / 3.0)
        dice = dice_coefficient(p1, p2)
        # 2 * 50 / (100 + 100) = 0.5
        assert dice == pytest.approx(0.5)

    def test_boundary_iou(self):
        p1 = [(0.0, 0.0), (20.0, 0.0), (20.0, 20.0), (0.0, 20.0)]
        assert boundary_iou(p1, p1, buffer_width=1.0) == pytest.approx(1.0)

    def test_detection_metrics(self):
        # 3 TP, 1 FP (4 total preds), 2 FN (5 total truth)
        metrics = compute_detection_metrics(
            matched_ious=[0.8, 0.7, 0.9],
            num_predictions=4,
            num_ground_truths=5,
        )
        assert metrics.precision == pytest.approx(3 / 4)
        assert metrics.recall == pytest.approx(3 / 5)
        expected_f1 = 2 * (3 / 4) * (3 / 5) / ((3 / 4) + (3 / 5))
        assert metrics.f1 == pytest.approx(expected_f1)
        assert metrics.mean_iou == pytest.approx(0.8)

    def test_compute_ap(self):
        recalls = [0.2, 0.4, 0.6, 0.8, 1.0]
        precisions = [1.0, 1.0, 0.8, 0.6, 0.4]
        ap = compute_ap(recalls, precisions)
        assert 0.0 < ap <= 1.0

    def test_count_mae_and_rmse(self):
        truth = [10, 20, 30]
        pred = [12, 18, 33]
        # Diff: 2, 2, 3 -> MAE = 7/3
        assert count_mae(truth, pred) == pytest.approx(7.0 / 3.0)
        # Diff^2: 4, 4, 9 -> Mean = 17/3 -> RMSE = sqrt(17/3)
        assert count_rmse(truth, pred) == pytest.approx(np.sqrt(17.0 / 3.0))


class TestDatasetLoadingAndSplitting:
    def test_load_coco_dataset(self, tmp_path: Path):
        coco_data = {
            "images": [
                {"id": 1, "file_name": "scene1.tif", "width": 800, "height": 800, "forest_type": "pine"}
            ],
            "annotations": [
                {
                    "id": 101,
                    "image_id": 1,
                    "category_id": 1,
                    "bbox": [50.0, 50.0, 40.0, 40.0],
                    "segmentation": [[50.0, 50.0, 90.0, 50.0, 90.0, 90.0, 50.0, 90.0]],
                    "area": 1600.0,
                    "score": 0.92,
                }
            ],
            "categories": [{"id": 1, "name": "Tree"}],
        }
        json_file = tmp_path / "test_coco.json"
        json_file.write_text(json.dumps(coco_data), encoding="utf-8")

        dataset = load_coco_dataset(json_file, image_dir=tmp_path)
        assert dataset.total_images == 1
        assert dataset.total_annotations == 1
        ann = dataset.images[0].annotations[0]
        assert ann.bbox == (50.0, 50.0, 90.0, 90.0)
        assert ann.polygon is not None
        assert ann.confidence == 0.92

    def test_load_csv_dataset(self, tmp_path: Path):
        csv_file = tmp_path / "annotations.csv"
        csv_content = (
            "image_path,xmin,ymin,xmax,ymax,label,score\n"
            "scene1.tif,10,15,50,55,Tree,0.88\n"
            "scene1.tif,60,65,100,105,Tree,0.75\n"
        )
        csv_file.write_text(csv_content, encoding="utf-8")

        dataset = load_csv_dataset(csv_file, image_dir=tmp_path)
        assert dataset.total_images == 1
        assert dataset.total_annotations == 2
        assert dataset.images[0].annotations[0].bbox == (10.0, 15.0, 50.0, 55.0)

    def test_load_geojson_dataset(self, tmp_path: Path):
        geojson_data = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "id": "crown_1",
                    "properties": {"confidence": 0.85, "label": "Pine"},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[10, 10], [50, 10], [50, 50], [10, 50], [10, 10]]],
                    },
                }
            ],
        }
        geo_file = tmp_path / "crowns.geojson"
        geo_file.write_text(json.dumps(geojson_data), encoding="utf-8")

        dataset = load_geojson_dataset(geo_file, tmp_path / "scene.tif")
        assert dataset.total_annotations == 1
        assert dataset.images[0].annotations[0].bbox == (10.0, 10.0, 50.0, 50.0)

    def test_spatial_block_split(self, tmp_path: Path):
        images = [
            AnnotatedImage(image_id=f"img_{i}", file_path=tmp_path / f"img_{i}.tif", width=800, height=800)
            for i in range(10)
        ]
        ds = EvaluationDataset(name="test_split", images=images)
        train, val, test = spatial_block_split(ds, train_ratio=0.7, val_ratio=0.15, test_ratio=0.15)

        assert train.total_images + val.total_images + test.total_images == 10
        # Ensure no image appears in multiple splits
        train_ids = {img.image_id for img in train.images}
        val_ids = {img.image_id for img in val.images}
        test_ids = {img.image_id for img in test.images}
        assert train_ids.isdisjoint(val_ids)
        assert train_ids.isdisjoint(test_ids)
        assert val_ids.isdisjoint(test_ids)


class TestEvaluatorAndReports:
    def test_evaluator_multi_threshold(self):
        evaluator = ForestModelEvaluator(iou_thresholds=(0.50, 0.75))

        gt = {
            "img_1": [
                Annotation(id="t1", bbox=(10.0, 10.0, 40.0, 40.0)),
                Annotation(id="t2", bbox=(60.0, 60.0, 90.0, 90.0)),
            ]
        }
        # Predictions: 1 exact match (p1), 1 partial match (p2 with IoU ~ 0.52), 1 false positive (p3)
        preds = {
            "img_1": [
                Annotation(id="p1", bbox=(10.0, 10.0, 40.0, 40.0), confidence=0.95),
                Annotation(id="p2", bbox=(65.0, 65.0, 95.0, 95.0), confidence=0.85),
                Annotation(id="p3", bbox=(150.0, 150.0, 180.0, 180.0), confidence=0.60),
            ]
        }

        result = evaluator.evaluate_predictions(gt, preds, model_name="test_model", dataset_name="test_ds")

        assert result.total_images == 1
        assert result.total_ground_truth_trees == 2
        assert result.total_predicted_trees == 3
        # At 0.50: 2 TP (p1, p2), 1 FP (p3) -> precision = 2/3
        assert result.metrics_at_50.true_positives == 2
        assert result.metrics_at_50.false_positives == 1
        assert result.metrics_at_50.precision == pytest.approx(2 / 3)
        assert result.metrics_at_50.recall == pytest.approx(1.0)
        assert result.count_mae == pytest.approx(1.0)

    def test_report_json_and_markdown(self, tmp_path: Path):
        evaluator = ForestModelEvaluator(iou_thresholds=(0.50,))
        gt = {"img_1": [Annotation(id="t1", bbox=(10.0, 10.0, 30.0, 30.0))]}
        pred = {"img_1": [Annotation(id="p1", bbox=(10.0, 10.0, 30.0, 30.0), confidence=0.9)]}
        result = evaluator.evaluate_predictions(gt, pred, model_name="Model_A", dataset_name="Benchmark_1")

        report = EvaluationReport(result=result, model_version="2.1.0")
        json_path = tmp_path / "report.json"
        saved = report.save_json(json_path)
        assert saved.exists()

        loaded = EvaluationReport.load_json(json_path)
        assert loaded["metadata"]["model_name"] == "Model_A"
        assert loaded["metadata"]["model_version"] == "2.1.0"

        md = report.to_markdown()
        assert "# Evaluation Report: Model_A" in md
        assert "mAP@50" in md

    def test_benchmark_comparison(self):
        evaluator = ForestModelEvaluator(iou_thresholds=(0.50,))
        gt = {"img_1": [Annotation(id="t1", bbox=(10.0, 10.0, 30.0, 30.0))]}
        pred_a = {"img_1": [Annotation(id="p1", bbox=(10.0, 10.0, 30.0, 30.0), confidence=0.9)]}
        pred_b = {"img_1": [Annotation(id="p2", bbox=(50.0, 50.0, 70.0, 70.0), confidence=0.8)]}

        res_a = evaluator.evaluate_predictions(gt, pred_a, model_name="DeepForest_Pretrained")
        res_b = evaluator.evaluate_predictions(gt, pred_b, model_name="Classical_Engine")

        benchmark = BenchmarkComparison([res_a, res_b])
        md = benchmark.to_markdown()
        assert "DeepForest_Pretrained" in md
        assert "Classical_Engine" in md
        assert len(benchmark.build_table_rows()) == 2
