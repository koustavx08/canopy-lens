"""DeepForest crown detection engine.

DeepForest is a RetinaNet trained on hand-annotated NEON airborne RGB imagery
of North American forests. It is the pretrained model CanopyLens uses when the
optional deep-learning dependencies are installed: it detects individual trees
far more reliably than a colour index in closed canopy, where the classical
engine tends to merge neighbouring crowns.

Two things are worth being explicit about.

First, DeepForest predicts *boxes*, not crown outlines. Reporting box area as
canopy area would overstate it by roughly a quarter, so each box is refined
into a polygon by `canopylens_ml.refine`. Boxes that cannot be refined are kept
as rectangles and flagged with `refined: False` in the detection attributes, so
the overestimate is visible rather than hidden.

Second, the training data is NEON imagery at about 10 cm/pixel over US forest
sites. Accuracy on imagery from a different sensor, season, region or
resolution is unknown rather than merely lower - this is the domain-shift
limitation the application states in its own interface.

Weights are downloaded from Hugging Face on first use and cached by
`huggingface_hub`, so the first run needs network access.
"""

from __future__ import annotations

import logging

import numpy as np

from ..base import DetectionEngine
from ..geometry import bounding_box, polygon_area, polygon_centroid
from ..refine import refine_box_to_polygon
from ..types import CrownDetection, DetectionParams

log = logging.getLogger(__name__)

MODEL_ID = "weecology/deepforest-tree"


class DeepForestEngine(DetectionEngine):
    name = "deepforest"
    title = "DeepForest (pretrained RetinaNet)"
    description = (
        "Pretrained tree detector from the DeepForest project, trained on NEON "
        "airborne RGB imagery at roughly 10 cm/pixel. Predicts one box per "
        "tree; each box is refined into a crown polygon using the vegetation "
        "index so that reported areas are canopy, not bounding rectangles."
    )
    produces = "polygon"
    requires_download = True
    confidence_kind = "model"

    def __init__(self) -> None:
        self._model = None

    def check(self) -> tuple[bool, str | None]:
        try:
            import torch  # noqa: F401
        except ImportError:
            return False, (
                "PyTorch is not installed. Install the optional deep-learning "
                "extras: pip install torch torchvision --index-url "
                "https://download.pytorch.org/whl/cpu"
            )
        try:
            import deepforest  # noqa: F401
        except ImportError:
            return False, (
                "DeepForest is not installed. Install it with: "
                "pip install -r backend/requirements-ml.txt"
            )
        try:
            # Import the module that actually builds the model, not just the
            # package. DeepForest pulls in albumentations, pytorch-lightning
            # and others at this point, and an incompatible version of any of
            # them fails here rather than at the top-level import. Probing the
            # real entry point is the difference between reporting "engine
            # unavailable, here is why" and crashing halfway through a run.
            from deepforest import main  # noqa: F401
        except Exception as exc:
            return False, (
                "DeepForest is installed but could not be imported: "
                f"{type(exc).__name__}: {exc}. This usually means a dependency "
                "version conflict; reinstall with "
                "pip install -r backend/requirements-ml.txt"
            )
        try:
            import cv2  # noqa: F401
        except ImportError:
            return False, "OpenCV is required to refine boxes into crown polygons."
        return True, None

    def load(self):
        """Load and cache the pretrained model.

        Separated from `detect_tile` so the caller can pay the download and
        initialisation cost once, up front, rather than inside the first tile.
        """
        if self._model is not None:
            return self._model

        available, reason = self.check()
        if not available:
            raise RuntimeError(reason)

        from deepforest import main as deepforest_main

        model = deepforest_main.deepforest()
        # DeepForest moved from use_release() to load_model() during the 1.x
        # series; support both so the engine is not tied to one point release.
        if hasattr(model, "load_model"):
            model.load_model(MODEL_ID)
        elif hasattr(model, "use_release"):
            model.use_release()
        else:  # pragma: no cover - defensive, no known release lacks both
            raise RuntimeError(
                "This DeepForest build exposes neither load_model() nor "
                "use_release(); cannot obtain pretrained weights."
            )
        model.eval()
        self._model = model
        log.info("DeepForest model loaded (%s)", MODEL_ID)
        return model

    def detect_tile(self, image: np.ndarray, params: DetectionParams) -> list[CrownDetection]:
        model = self.load()

        image = np.asarray(image)
        if image.ndim == 3 and image.shape[2] == 4:
            image = image[:, :, :3]
        if image.dtype != np.uint8:
            image = np.clip(image, 0, 255).astype(np.uint8)

        predictions = model.predict_image(image=image.astype(np.float32))
        if predictions is None or len(predictions) == 0:
            return []

        minimum_area = max(params.min_crown_area_px, 1.0)
        detections: list[CrownDetection] = []

        for _, row in predictions.iterrows():
            score = float(row["score"])
            box = (
                float(row["xmin"]),
                float(row["ymin"]),
                float(row["xmax"]),
                float(row["ymax"]),
            )
            ring, refined = refine_box_to_polygon(image, box)
            area_px = polygon_area(ring)
            if area_px < minimum_area:
                continue
            if params.max_crown_area_px is not None and area_px > params.max_crown_area_px:
                continue

            detections.append(
                CrownDetection(
                    polygon=ring,
                    confidence=score,
                    bbox=bounding_box(ring),
                    area_px=area_px,
                    engine=self.name,
                    attributes={
                        "centroid_px": polygon_centroid(ring),
                        "confidence_kind": self.confidence_kind,
                        "refined": refined,
                        "box_px": box,
                        "label": str(row.get("label", "Tree")),
                    },
                )
            )

        return detections
