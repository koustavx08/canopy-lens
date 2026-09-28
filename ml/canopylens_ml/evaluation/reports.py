"""Reporting and benchmark comparison utilities for model evaluation."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from .evaluator import EvaluationResult


@dataclass
class EvaluationReport:
    """A persistent, machine-readable evaluation report."""

    result: EvaluationResult
    model_version: str = "1.0.0"
    dataset_version: str = "1.0.0"
    config: dict = field(default_factory=dict)
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict:
        return {
            "metadata": {
                "model_name": self.result.model_name,
                "model_version": self.model_version,
                "dataset_name": self.result.dataset_name,
                "dataset_version": self.dataset_version,
                "timestamp": self.timestamp,
                "config": self.config,
            },
            "evaluation": self.result.to_dict(),
        }

    def save_json(self, destination: Path | str) -> Path:
        dest = Path(destination)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)
        return dest

    @classmethod
    def load_json(cls, source: Path | str) -> dict:
        with open(source, "r", encoding="utf-8") as f:
            return json.load(f)

    def to_markdown(self) -> str:
        s = self.result.summary
        m50 = self.result.metrics_at_50
        lines = [
            f"# Evaluation Report: {self.result.model_name}",
            f"- **Dataset**: {self.result.dataset_name} ({s['total_images']} images, {s['ground_truth_trees']} ground truth crowns)",
            f"- **Timestamp**: {self.timestamp}",
            f"- **Model Version**: {self.model_version}",
            "",
            "## Detection Metrics",
            "| Metric | Value |",
            "| --- | --- |",
            f"| mAP@50 | {s['map50']:.4f} |",
            f"| mAP@75 | {s['map75']:.4f} |",
            f"| mAP@50:95 | {s['map50_95']:.4f} |",
            f"| Precision@50 | {m50.precision:.4f} |",
            f"| Recall@50 | {m50.recall:.4f} |",
            f"| F1@50 | {m50.f1:.4f} |",
            f"| Mean IoU@50 | {m50.mean_iou:.4f} |",
            f"| Tree Count MAE | {s['count_mae']:.2f} |",
            f"| Tree Count RMSE | {s['count_rmse']:.2f} |",
            f"| Area Ratio (Pred/Truth) | {s['area_ratio']:.4f} |",
        ]

        if self.result.segmentation_metrics:
            lines.extend([
                "",
                "## Segmentation Metrics",
                "| Metric | Value |",
                "| --- | --- |",
            ])
            for k, v in self.result.segmentation_metrics.items():
                lines.append(f"| {k} | {v:.4f} |")

        if self.result.forest_type_breakdown:
            lines.extend([
                "",
                "## Performance by Forest Type",
                "| Forest Type | GT Trees | Pred Trees | Precision | Recall | F1 | Mean IoU |",
                "| --- | --- | --- | --- | --- | --- | --- |",
            ])
            for ft, metrics in self.result.forest_type_breakdown.items():
                lines.append(
                    f"| {ft} | {metrics['ground_truth_trees']} | {metrics['predicted_trees']} | "
                    f"{metrics['precision']:.4f} | {metrics['recall']:.4f} | {metrics['f1']:.4f} | {metrics['mean_iou']:.4f} |"
                )

        return "\n".join(lines)


class BenchmarkComparison:
    """Compares multiple models evaluated on the same benchmark under identical conditions."""

    def __init__(self, results: Sequence[EvaluationResult]):
        self.results = list(results)

    def to_dict(self) -> dict:
        return {
            "models": [r.to_dict() for r in self.results],
            "comparison_table": self.build_table_rows(),
        }

    def build_table_rows(self) -> list[dict]:
        rows = []
        for r in self.results:
            rows.append({
                "model_name": r.model_name,
                "dataset_name": r.dataset_name,
                "map50": round(r.map50, 4),
                "map50_95": round(r.map50_95, 4),
                "precision_50": round(r.metrics_at_50.precision, 4),
                "recall_50": round(r.metrics_at_50.recall, 4),
                "f1_50": round(r.metrics_at_50.f1, 4),
                "count_mae": round(r.count_mae, 2),
                "count_rmse": round(r.count_rmse, 2),
                "area_ratio": round(r.area_ratio, 4),
            })
        return rows

    def to_markdown(self) -> str:
        lines = [
            "# Model Benchmark Comparison",
            "| Model | Dataset | mAP@50 | mAP@50:95 | Precision@50 | Recall@50 | F1@50 | Count MAE | Area Ratio |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for row in self.build_table_rows():
            lines.append(
                f"| **{row['model_name']}** | {row['dataset_name']} | {row['map50']:.4f} | {row['map50_95']:.4f} | "
                f"{row['precision_50']:.4f} | {row['recall_50']:.4f} | {row['f1_50']:.4f} | "
                f"{row['count_mae']:.2f} | {row['area_ratio']:.4f} |"
            )
        return "\n".join(lines)
