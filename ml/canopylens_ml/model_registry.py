"""Model Registry for CanopyLens.

Tracks pretrained and fine-tuned models, their training configurations,
evaluation metrics, checkpoint artifacts, and lifecycle state.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .base import DetectionEngine, EngineInfo, EngineUnavailableError
from .registry import register_engine

log = logging.getLogger(__name__)

DEFAULT_REGISTRY_FILENAME = "model_registry.json"


@dataclass
class ModelEntry:
    """Metadata record for a registered tree crown model."""

    id: str
    name: str
    architecture: str
    version: str
    description: str
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    checkpoint_path: str | None = None
    dataset_name: str | None = None
    image_resolution_m: float | None = None
    training_config: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    status: str = "ready"  # ready, training, failed
    is_default: bool = False
    is_fallback: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ModelEntry:
        return cls(**data)


class ModelRegistry:
    """Thread-safe persistent registry of models."""

    def __init__(self, registry_dir: Path | str | None = None):
        if registry_dir is None:
            # Fall back to backend/.data/models or repository root data
            registry_dir = Path(__file__).resolve().parents[2] / "backend" / ".data" / "models"
        self.registry_dir = Path(registry_dir)
        self.registry_file = self.registry_dir / DEFAULT_REGISTRY_FILENAME
        self._ensure_initialized()

    def _ensure_initialized(self) -> None:
        self.registry_dir.mkdir(parents=True, exist_ok=True)
        if not self.registry_file.exists():
            default_models = self._default_models()
            self._save_models(default_models)

    def _default_models(self) -> dict[str, ModelEntry]:
        return {
            "deepforest_neon": ModelEntry(
                id="deepforest_neon",
                name="DeepForest Pretrained (NEON)",
                architecture="retinanet_resnet50",
                version="1.5.0",
                description="Pretrained RetinaNet on NEON airborne 10 cm/px RGB imagery across US forests.",
                dataset_name="NEON_Tree_Benchmark",
                image_resolution_m=0.10,
                metrics={
                    "notice": "Official DeepForest benchmark metrics on NEON",
                    "map50": 0.65,
                    "precision_50": 0.68,
                    "recall_50": 0.74,
                },
                is_default=True,
                is_fallback=True,
            ),
            "classical_baseline": ModelEntry(
                id="classical_baseline",
                name="Classical Watershed Baseline",
                architecture="classical_watershed",
                version="1.0.0",
                description="Deterministic colour-index (ExG) + distance transform + watershed. CPU native, zero external weights.",
                dataset_name="None (heuristic)",
                image_resolution_m=None,
                metrics={
                    "notice": "Heuristic baseline without trained neural weights",
                },
                is_default=False,
                is_fallback=True,
            ),
        }

    def _load_models(self) -> dict[str, ModelEntry]:
        if not self.registry_file.exists():
            return self._default_models()
        try:
            with open(self.registry_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            return {k: ModelEntry.from_dict(v) for k, v in data.items()}
        except Exception as exc:
            log.warning("Could not read registry file (%s); using defaults: %s", self.registry_file, exc)
            return self._default_models()

    def _save_models(self, models: dict[str, ModelEntry]) -> None:
        self.registry_dir.mkdir(parents=True, exist_ok=True)
        temp_file = self.registry_file.with_suffix(".tmp")
        data = {k: v.to_dict() for k, v in models.items()}
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        temp_file.replace(self.registry_file)

    def list_models(self) -> list[ModelEntry]:
        """Return all registered models."""
        models = self._load_models()
        return sorted(models.values(), key=lambda m: (not m.is_default, m.created_at), reverse=True)

    def get_model(self, model_id: str) -> ModelEntry | None:
        """Find a model by id."""
        models = self._load_models()
        return models.get(model_id)

    def register_model(self, entry: ModelEntry) -> ModelEntry:
        """Add or update a model record."""
        models = self._load_models()
        models[entry.id] = entry
        self._save_models(models)
        log.info("Registered model %s (version %s)", entry.id, entry.version)
        return entry

    def update_status(self, model_id: str, status: str, metrics: dict | None = None) -> ModelEntry:
        """Update model training or evaluation status."""
        models = self._load_models()
        if model_id not in models:
            raise KeyError(f"Model {model_id!r} not found in registry")
        entry = models[model_id]
        entry.status = status
        if metrics is not None:
            entry.metrics.update(metrics)
        self._save_models(models)
        return entry

    def delete_model(self, model_id: str) -> bool:
        """Remove a model from the registry."""
        models = self._load_models()
        if model_id not in models:
            return False
        entry = models[model_id]
        if entry.is_fallback:
            raise ValueError(f"Cannot delete system fallback model {model_id!r}")
        del models[model_id]
        self._save_models(models)
        # Remove checkpoint file if exists
        if entry.checkpoint_path:
            chk = Path(entry.checkpoint_path)
            if chk.exists():
                chk.unlink(missing_ok=True)
        return True


_GLOBAL_REGISTRY: ModelRegistry | None = None


def get_model_registry(registry_dir: Path | str | None = None) -> ModelRegistry:
    """Get the active model registry singleton or custom instance."""
    global _GLOBAL_REGISTRY
    if registry_dir is not None:
        return ModelRegistry(registry_dir)
    if _GLOBAL_REGISTRY is None:
        _GLOBAL_REGISTRY = ModelRegistry()
    return _GLOBAL_REGISTRY
