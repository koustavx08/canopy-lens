"""Tests for the fine-tuning pipeline and model registry."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from canopylens_ml.evaluation.dataset import AnnotatedImage, Annotation, EvaluationDataset
from canopylens_ml.model_registry import ModelEntry, ModelRegistry
from canopylens_ml.training import (
    TrainingConfig,
    TreeCrownTrainer,
    validate_training_dataset,
)


class TestDatasetValidator:
    def test_valid_dataset(self, tmp_path: Path):
        img_path = tmp_path / "img1.tif"
        img_path.write_bytes(b"dummy")
        anns = [Annotation(id="a1", bbox=(10.0, 10.0, 50.0, 50.0))]
        img = AnnotatedImage(image_id="img1", file_path=img_path, width=100, height=100, annotations=anns)
        ds = EvaluationDataset(name="ds1", images=[img])

        report = validate_training_dataset(ds, check_file_exists=True)
        assert report.is_valid
        assert report.valid_images == 1
        assert report.valid_annotations == 1
        assert report.degenerate_boxes == 0

    def test_corrupt_and_degenerate_dataset(self, tmp_path: Path):
        img_path = tmp_path / "missing.tif"
        # Bbox where xmin >= xmax
        anns = [Annotation(id="bad_box", bbox=(50.0, 10.0, 40.0, 50.0))]
        img = AnnotatedImage(image_id="img2", file_path=img_path, width=100, height=100, annotations=anns)
        ds = EvaluationDataset(name="ds_bad", images=[img])

        report = validate_training_dataset(ds, check_file_exists=True)
        assert not report.is_valid
        assert report.degenerate_boxes == 1
        assert len(report.corrupted_images) == 1


class TestModelRegistry:
    def test_default_models_present(self, tmp_path: Path):
        registry = ModelRegistry(registry_dir=tmp_path / "models")
        models = registry.list_models()
        ids = {m.id for m in models}
        assert "deepforest_neon" in ids
        assert "classical_baseline" in ids

    def test_register_and_get_model(self, tmp_path: Path):
        registry = ModelRegistry(registry_dir=tmp_path / "models")
        new_model = ModelEntry(
            id="pine_finetuned_v1",
            name="Pine Plantation Specialist",
            architecture="retinanet_resnet50",
            version="1.0.0",
            description="Trained on Ontario pine stands",
            checkpoint_path=str(tmp_path / "weights.pt"),
        )
        registry.register_model(new_model)

        retrieved = registry.get_model("pine_finetuned_v1")
        assert retrieved is not None
        assert retrieved.name == "Pine Plantation Specialist"
        assert retrieved.version == "1.0.0"

    def test_fallback_models_cannot_be_deleted(self, tmp_path: Path):
        registry = ModelRegistry(registry_dir=tmp_path / "models")
        with pytest.raises(ValueError) as exc:
            registry.delete_model("deepforest_neon")
        assert "fallback" in str(exc.value).lower()


class TestTrainer:
    def test_training_pipeline_execution(self, tmp_path: Path):
        registry = ModelRegistry(registry_dir=tmp_path / "models")
        output_dir = tmp_path / "training_output"

        # Construct synthetic dataset
        img_path = tmp_path / "scene.tif"
        img_path.write_bytes(b"dummy_raster")
        anns = [
            Annotation(id=f"ann_{i}", bbox=(10.0 * i, 10.0 * i, 10.0 * i + 30.0, 10.0 * i + 30.0))
            for i in range(5)
        ]
        img = AnnotatedImage(image_id="scene", file_path=img_path, width=800, height=800, annotations=anns)
        dataset = EvaluationDataset(name="synthetic_trees", images=[img])

        config = TrainingConfig(
            model_name="conifer_finetuned",
            dataset_name="synthetic_trees",
            output_dir=output_dir,
            epochs=3,
            batch_size=2,
            learning_rate=0.0005,
            early_stopping_patience=2,
            random_seed=42,
            validation_split=0.2,
        )

        trainer = TreeCrownTrainer(config=config, registry=registry)
        progress_events = []

        def on_prog(p):
            progress_events.append(p.epoch)

        entry = trainer.train(dataset, on_progress=on_prog)

        assert entry.status == "ready"
        assert entry.checkpoint_path is not None
        assert (output_dir / "training_history.json").exists()
        assert len(progress_events) > 0

        # Check registered in registry
        reg_model = registry.get_model(entry.id)
        assert reg_model is not None
        assert reg_model.name == "conifer_finetuned"
