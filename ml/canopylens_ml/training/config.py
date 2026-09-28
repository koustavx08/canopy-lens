"""Training configuration settings."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class TrainingConfig:
    """Hyperparameters and configuration for tree crown detector fine-tuning."""

    model_name: str
    dataset_name: str
    output_dir: Path | str
    epochs: int = 10
    batch_size: int = 4
    learning_rate: float = 0.001
    weight_decay: float = 0.0001
    image_size: int = 800
    backbone_frozen: bool = False
    early_stopping_patience: int = 3
    random_seed: int = 42
    device: str = "auto"  # auto, cpu, cuda
    pretrained_weights: str | None = None
    validation_split: float = 0.20
    custom_parameters: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["output_dir"] = str(self.output_dir)
        return d
