"""End-to-end tests for the ML Intelligence API endpoints."""

from __future__ import annotations

import io
import json
import time
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import write_raster

app = create_app()
client = TestClient(app)


class TestModelEndpoints:
    def test_list_models(self):
        res = client.get("/api/models")
        assert res.status_code == 200
        models = res.json()
        assert len(models) >= 2
        ids = {m["id"] for m in models}
        assert "deepforest_neon" in ids
        assert "classical_baseline" in ids

    def test_get_model_details_success(self):
        res = client.get("/api/models/deepforest_neon")
        assert res.status_code == 200
        body = res.json()
        assert body["id"] == "deepforest_neon"
        assert body["architecture"] == "retinanet_resnet50"
        assert body["is_default"] is True

    def test_get_model_details_not_found(self):
        res = client.get("/api/models/does_not_exist_xyz")
        assert res.status_code == 404

    def test_benchmarks_endpoint(self):
        res = client.get("/api/models/benchmarks")
        assert res.status_code == 200
        body = res.json()
        assert "comparison_table" in body
        assert "markdown_table" in body
        assert len(body["comparison_table"]) >= 2
        assert "mAP@50" in body["markdown_table"]


class TestEvaluationEndpoint:
    def test_evaluate_model_with_annotations(self):
        coco_content = {
            "images": [{"id": 1, "file_name": "scene.tif", "width": 800, "height": 800}],
            "annotations": [
                {
                    "id": 1,
                    "image_id": 1,
                    "category_id": 1,
                    "bbox": [50, 50, 40, 40],
                    "score": 0.90,
                }
            ],
            "categories": [{"id": 1, "name": "Tree"}],
        }
        coco_bytes = io.BytesIO(json.dumps(coco_content).encode("utf-8"))

        res = client.post(
            "/api/models/evaluate",
            data={"model_name": "test_detector", "dataset_name": "synthetic_eval"},
            files={"annotations_file": ("test.json", coco_bytes, "application/json")},
        )
        assert res.status_code == 200
        body = res.json()
        assert body["model_name"] == "test_detector"
        assert body["summary"]["ground_truth_trees"] == 1
        assert "map50" in body["summary"]


class TestTrainingJobLifecycle:
    def test_train_job_submission_and_status(self):
        csv_data = "image_path,xmin,ymin,xmax,ymax,label\nscene.tif,10,10,40,40,Tree\n"
        csv_bytes = io.BytesIO(csv_data.encode("utf-8"))

        res = client.post(
            "/api/models/train",
            data={
                "model_name": "api_test_model",
                "epochs": 2,
                "batch_size": 2,
                "learning_rate": 0.001,
            },
            files={"dataset_file": ("train.csv", csv_bytes, "text/csv")},
        )
        assert res.status_code == 202
        accepted = res.json()
        job_id = accepted["job_id"]
        assert job_id.startswith("train_")

        # Poll status
        status_res = client.get(f"/api/models/train/{job_id}")
        assert status_res.status_code == 200
        status_body = status_res.json()
        assert status_body["job_id"] == job_id
        assert status_body["status"] in ("queued", "running", "completed")


class TestVegetationEndpoint:
    def test_vegetation_analysis_rgb(self, tmp_path: Path):
        # 3-band RGB image
        scene = np.zeros((3, 60, 60), dtype=np.uint8)
        scene[0] = 30   # Red
        scene[1] = 160  # Green
        scene[2] = 40   # Blue
        raster_path = write_raster(tmp_path / "veg_rgb.tif", scene)

        with open(raster_path, "rb") as f:
            res = client.post(
                "/api/vegetation",
                data={"index_name": "VARI"},
                files={"image": ("veg_rgb.tif", f, "image/tiff")},
            )
        assert res.status_code == 200
        body = res.json()
        assert body["index_name"] == "VARI"
        assert body["band_count"] == 3
        assert "scene_statistics" in body
        assert body["scene_statistics"]["mean"] > 0.0
        assert len(body["distribution"]) == 10

    def test_vegetation_analysis_with_crowns(self, tmp_path: Path):
        scene = np.zeros((3, 60, 60), dtype=np.uint8)
        scene[1] = 150
        raster_path = write_raster(tmp_path / "veg_crowns.tif", scene)

        crowns_fc = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "id": "c1",
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[10, 10], [30, 10], [30, 30], [10, 30], [10, 10]]],
                    },
                }
            ],
        }

        with open(raster_path, "rb") as f:
            res = client.post(
                "/api/vegetation",
                data={
                    "index_name": "ExG",
                    "crowns_geojson": json.dumps(crowns_fc),
                },
                files={"image": ("veg_crowns.tif", f, "image/tiff")},
            )
        assert res.status_code == 200
        body = res.json()
        assert body["crown_count"] == 1
        assert len(body["crown_statistics"]) == 1
        assert body["crown_statistics"][0]["crown_id"] == "c1"


class TestTemporalChangeEndpoint:
    def test_temporal_change_api(self):
        t1_geojson = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "id": "tree_1",
                    "properties": {"area_m2": 50.0, "confidence": 0.90},
                    "geometry": {"type": "Polygon", "coordinates": [[[10, 10], [30, 10], [30, 30], [10, 30], [10, 10]]]},
                }
            ],
        }
        # T2: tree_1 is gone, tree_2 is new
        t2_geojson = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "id": "tree_2",
                    "properties": {"area_m2": 45.0, "confidence": 0.85},
                    "geometry": {"type": "Polygon", "coordinates": [[[50, 50], [70, 50], [70, 70], [50, 70], [50, 50]]]},
                }
            ],
        }

        f1 = io.BytesIO(json.dumps(t1_geojson).encode("utf-8"))
        f2 = io.BytesIO(json.dumps(t2_geojson).encode("utf-8"))

        res = client.post(
            "/api/change",
            data={"min_matching_iou": 0.20},
            files={
                "t1_crowns_geojson": ("t1.geojson", f1, "application/geo+json"),
                "t2_crowns_geojson": ("t2.geojson", f2, "application/geo+json"),
            },
        )
        assert res.status_code == 200
        body = res.json()
        summary = body["summary"]
        assert summary["lost_trees"] == 1
        assert summary["recruited_trees"] == 1
        assert summary["persistent_trees"] == 0
        assert summary["canopy_loss_area_m2"] == pytest.approx(50.0)
        assert summary["canopy_gain_area_m2"] == pytest.approx(45.0)
        assert body["total_change_polygons"] == 2
        assert "notice" in body
