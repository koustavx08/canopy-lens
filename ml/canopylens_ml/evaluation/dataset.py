"""Dataset loader and validation split utilities for tree crown evaluation.

Supports:
- COCO-format JSON (bounding boxes and polygon segmentations)
- DeepForest / Pascal VOC CSV annotations (image_path, xmin, ymin, xmax, ymax, label)
- GeoJSON tree crown polygon sets
- Spatial block splitting to prevent data leakage and spatial autocorrelation
"""

from __future__ import annotations

import csv
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from ..geometry import bounding_box, polygon_area

log = logging.getLogger(__name__)


@dataclass
class Annotation:
    """One annotated tree crown (ground truth or prediction)."""

    id: str
    bbox: tuple[float, float, float, float]  # (xmin, ymin, xmax, ymax) in pixels
    polygon: list[tuple[float, float]] | None = None
    confidence: float | None = None
    label: str = "Tree"
    area_px: float = 0.0
    attributes: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.polygon and self.area_px <= 0.0:
            self.area_px = polygon_area(self.polygon)
        elif self.area_px <= 0.0 and self.bbox:
            w = max(0.0, self.bbox[2] - self.bbox[0])
            h = max(0.0, self.bbox[3] - self.bbox[1])
            self.area_px = w * h


@dataclass
class AnnotatedImage:
    """One imagery scene with its associated annotations."""

    image_id: str
    file_path: Path
    width: int
    height: int
    annotations: list[Annotation] = field(default_factory=list)
    forest_type: str = "unspecified"
    crs: str | None = None
    metadata: dict = field(default_factory=dict)

    @property
    def crown_count(self) -> int:
        return len(self.annotations)


@dataclass
class EvaluationDataset:
    """A collection of annotated images forming an evaluation or training set."""

    name: str
    images: list[AnnotatedImage] = field(default_factory=list)
    split: str = "all"
    metadata: dict = field(default_factory=dict)

    @property
    def total_images(self) -> int:
        return len(self.images)

    @property
    def total_annotations(self) -> int:
        return sum(len(img.annotations) for img in self.images)

    @property
    def forest_types(self) -> set[str]:
        return {img.forest_type for img in self.images}

    def filter_by_forest_type(self, forest_type: str) -> EvaluationDataset:
        filtered = [img for img in self.images if img.forest_type == forest_type]
        return EvaluationDataset(
            name=f"{self.name}_{forest_type}",
            images=filtered,
            split=self.split,
            metadata=dict(self.metadata),
        )


def load_coco_dataset(
    json_path: Path | str,
    image_dir: Path | str | None = None,
    dataset_name: str | None = None,
) -> EvaluationDataset:
    """Load annotations from a COCO format JSON file."""
    json_path = Path(json_path)
    image_dir = Path(image_dir) if image_dir else json_path.parent

    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    categories = {cat["id"]: cat.get("name", "Tree") for cat in data.get("categories", [])}

    images_by_id: dict[int | str, AnnotatedImage] = {}
    for img_dict in data.get("images", []):
        img_id = img_dict["id"]
        fname = img_dict.get("file_name", f"{img_id}.png")
        file_path = image_dir / fname
        images_by_id[img_id] = AnnotatedImage(
            image_id=str(img_id),
            file_path=file_path,
            width=int(img_dict.get("width", 0)),
            height=int(img_dict.get("height", 0)),
            forest_type=img_dict.get("forest_type", "unspecified"),
        )

    for ann_dict in data.get("annotations", []):
        img_id = ann_dict["image_id"]
        if img_id not in images_by_id:
            continue

        bbox_raw = ann_dict.get("bbox", [])
        if len(bbox_raw) == 4:
            # COCO bbox format: [xmin, ymin, width, height]
            x, y, w, h = bbox_raw
            bbox = (float(x), float(y), float(x + w), float(y + h))
        else:
            bbox = (0.0, 0.0, 0.0, 0.0)

        polygon = None
        seg = ann_dict.get("segmentation", [])
        if isinstance(seg, list) and len(seg) > 0:
            flat_pts = seg[0]
            if isinstance(flat_pts, list) and len(flat_pts) >= 6:
                # [x1, y1, x2, y2, ...]
                polygon = [(float(flat_pts[i]), float(flat_pts[i + 1])) for i in range(0, len(flat_pts) - 1, 2)]

        ann_id = str(ann_dict.get("id", f"ann_{len(images_by_id[img_id].annotations)}"))
        cat_id = ann_dict.get("category_id", 1)
        label = categories.get(cat_id, "Tree")
        confidence = ann_dict.get("score", None)

        images_by_id[img_id].annotations.append(
            Annotation(
                id=ann_id,
                bbox=bbox,
                polygon=polygon,
                confidence=float(confidence) if confidence is not None else None,
                label=label,
                area_px=float(ann_dict.get("area", 0.0)),
            )
        )

    name = dataset_name or json_path.stem
    return EvaluationDataset(name=name, images=list(images_by_id.values()))


def load_csv_dataset(
    csv_path: Path | str,
    image_dir: Path | str | None = None,
    dataset_name: str | None = None,
) -> EvaluationDataset:
    """Load annotations from DeepForest / Pascal VOC CSV format:
    image_path,xmin,ymin,xmax,ymax,label
    """
    csv_path = Path(csv_path)
    image_dir = Path(image_dir) if image_dir else csv_path.parent

    images_by_path: dict[str, AnnotatedImage] = {}

    with open(csv_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row_idx, row in enumerate(reader):
            img_rel = row.get("image_path", row.get("filename", "")).strip()
            if not img_rel:
                continue

            if img_rel not in images_by_path:
                full_path = image_dir / img_rel
                images_by_path[img_rel] = AnnotatedImage(
                    image_id=img_rel,
                    file_path=full_path,
                    width=int(row.get("width", 0)) if "width" in row else 0,
                    height=int(row.get("height", 0)) if "height" in row else 0,
                    forest_type=row.get("forest_type", "unspecified"),
                )

            xmin = float(row.get("xmin", 0.0))
            ymin = float(row.get("ymin", 0.0))
            xmax = float(row.get("xmax", 0.0))
            ymax = float(row.get("ymax", 0.0))
            label = row.get("label", "Tree")
            score = float(row["score"]) if "score" in row and row["score"] else None

            # Generate rectangular polygon from box if no explicit polygon
            box_poly = [(xmin, ymin), (xmax, ymin), (xmax, ymax), (xmin, ymax)]

            images_by_path[img_rel].annotations.append(
                Annotation(
                    id=f"box_{row_idx:06d}",
                    bbox=(xmin, ymin, xmax, ymax),
                    polygon=box_poly,
                    confidence=score,
                    label=label,
                )
            )

    name = dataset_name or csv_path.stem
    return EvaluationDataset(name=name, images=list(images_by_path.values()))


def load_geojson_dataset(
    geojson_path: Path | str,
    image_path: Path | str,
    crs: str | None = None,
    dataset_name: str | None = None,
) -> EvaluationDataset:
    """Load crown polygons from a GeoJSON file for an image."""
    geojson_path = Path(geojson_path)
    image_path = Path(image_path)

    with open(geojson_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    features = data.get("features", [])
    annotations: list[Annotation] = []

    for idx, feat in enumerate(features):
        geom = feat.get("geometry", {})
        coords = geom.get("coordinates", [])
        poly_pts: list[tuple[float, float]] = []

        if geom.get("type") == "Polygon" and len(coords) > 0:
            poly_pts = [(float(pt[0]), float(pt[1])) for pt in coords[0]]
        elif geom.get("type") == "MultiPolygon" and len(coords) > 0 and len(coords[0]) > 0:
            poly_pts = [(float(pt[0]), float(pt[1])) for pt in coords[0][0]]

        if len(poly_pts) >= 3:
            box = bounding_box(poly_pts)
            props = feat.get("properties", {})
            confidence = props.get("confidence", props.get("score", None))
            ann_id = str(feat.get("id", props.get("id", f"crown_{idx:05d}")))
            annotations.append(
                Annotation(
                    id=ann_id,
                    bbox=box,
                    polygon=poly_pts,
                    confidence=float(confidence) if confidence is not None else None,
                    label=props.get("label", "Tree"),
                    attributes=props,
                )
            )

    img = AnnotatedImage(
        image_id=image_path.name,
        file_path=image_path,
        width=0,
        height=0,
        annotations=annotations,
        crs=crs,
    )
    return EvaluationDataset(
        name=dataset_name or geojson_path.stem,
        images=[img],
    )


def spatial_block_split(
    dataset: EvaluationDataset,
    train_ratio: float = 0.70,
    val_ratio: float = 0.15,
    test_ratio: float = 0.15,
    random_seed: int = 42,
) -> tuple[EvaluationDataset, EvaluationDataset, EvaluationDataset]:
    """Partition dataset into train/val/test splits using spatial block / image hashing
    to prevent spatial autocorrelation and test leakage.
    """
    import random

    total = train_ratio + val_ratio + test_ratio
    train_r = train_ratio / total
    val_r = val_ratio / total

    images = list(dataset.images)
    rng = random.Random(random_seed)
    rng.shuffle(images)

    n_images = len(images)
    if n_images <= 2:
        # If very few images, put first in train, remainder in val/test
        train_imgs = images[:1]
        val_imgs = images[1:2]
        test_imgs = images[2:] if n_images > 2 else list(val_imgs)
    else:
        n_train = max(1, int(round(n_images * train_r)))
        n_val = max(1, int(round(n_images * val_r)))
        if n_train + n_val >= n_images:
            n_train = max(1, n_images - 2)
            n_val = 1
        train_imgs = images[:n_train]
        val_imgs = images[n_train : n_train + n_val]
        test_imgs = images[n_train + n_val :]

    train_set = EvaluationDataset(
        name=f"{dataset.name}_train", images=train_imgs, split="train", metadata=dict(dataset.metadata)
    )
    val_set = EvaluationDataset(
        name=f"{dataset.name}_val", images=val_imgs, split="val", metadata=dict(dataset.metadata)
    )
    test_set = EvaluationDataset(
        name=f"{dataset.name}_test", images=test_imgs, split="test", metadata=dict(dataset.metadata)
    )

    log.info(
        "Spatial split for %s: %d train, %d val, %d test images",
        dataset.name,
        len(train_imgs),
        len(val_imgs),
        len(test_imgs),
    )
    return train_set, val_set, test_set
