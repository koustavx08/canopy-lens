"""Validation of datasets before training and fine-tuning."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..evaluation.dataset import AnnotatedImage, EvaluationDataset


@dataclass
class DatasetValidationReport:
    """Findings from validating a dataset prior to training."""

    is_valid: bool
    total_images: int
    valid_images: int
    corrupted_images: list[str] = field(default_factory=list)
    total_annotations: int
    valid_annotations: int
    degenerate_boxes: int = 0
    out_of_bounds_boxes: int = 0
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "is_valid": self.is_valid,
            "total_images": self.total_images,
            "valid_images": self.valid_images,
            "corrupted_images": self.corrupted_images,
            "total_annotations": self.total_annotations,
            "valid_annotations": self.valid_annotations,
            "degenerate_boxes": self.degenerate_boxes,
            "out_of_bounds_boxes": self.out_of_bounds_boxes,
            "warnings": self.warnings,
            "errors": self.errors,
        }


def validate_training_dataset(
    dataset: EvaluationDataset,
    check_file_exists: bool = True,
) -> DatasetValidationReport:
    """Perform rigorous pre-training checks on dataset images and bounding boxes."""
    total_imgs = len(dataset.images)
    if total_imgs == 0:
        return DatasetValidationReport(
            is_valid=False,
            total_images=0,
            valid_images=0,
            total_annotations=0,
            valid_annotations=0,
            errors=["Dataset contains no images."],
        )

    valid_imgs = 0
    corrupted: list[str] = []
    total_anns = 0
    valid_anns = 0
    degenerate = 0
    out_of_bounds = 0
    warnings: list[str] = []
    errors: list[str] = []

    for img in dataset.images:
        img_ok = True
        if check_file_exists and not img.file_path.exists():
            corrupted.append(f"{img.image_id}: file not found at {img.file_path}")
            img_ok = False

        img_w = img.width
        img_h = img.height

        for ann in img.annotations:
            total_anns += 1
            x1, y1, x2, y2 = ann.bbox

            # Check for non-degenerate box
            if x2 <= x1 or y2 <= y1:
                degenerate += 1
                continue

            # Check bounds if dimensions known
            if img_w > 0 and img_h > 0:
                if x1 < 0 or y1 < 0 or x2 > img_w or y2 > img_h:
                    out_of_bounds += 1

            valid_anns += 1

        if img_ok:
            valid_imgs += 1

    if degenerate > 0:
        warnings.append(f"Encountered {degenerate} degenerate bounding boxes (xmin >= xmax or ymin >= ymax).")

    if out_of_bounds > 0:
        warnings.append(f"Encountered {out_of_bounds} bounding boxes exceeding image dimensions.")

    if total_anns == 0:
        errors.append("Dataset has no annotations; supervised fine-tuning requires labeled crowns.")

    is_valid = len(errors) == 0 and valid_imgs > 0 and valid_anns > 0

    return DatasetValidationReport(
        is_valid=is_valid,
        total_images=total_imgs,
        valid_images=valid_imgs,
        corrupted_images=corrupted,
        total_annotations=total_anns,
        valid_annotations=valid_anns,
        degenerate_boxes=degenerate,
        out_of_bounds_boxes=out_of_bounds,
        warnings=warnings,
        errors=errors,
    )
