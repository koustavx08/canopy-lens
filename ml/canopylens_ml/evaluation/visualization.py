"""Visual evaluation overlays comparing ground truth and predictions."""

from __future__ import annotations

import numpy as np

try:
    import cv2
    _HAS_CV2 = True
except ImportError:
    _HAS_CV2 = False

from .dataset import Annotation


# Colour palette (BGR for OpenCV, RGB for Pillow)
COLOR_GT_RGB = (56, 189, 248)       # Cyan for Ground Truth
COLOR_TP_RGB = (74, 222, 128)       # Bright Green for True Positives
COLOR_FP_RGB = (239, 68, 68)        # Red for False Positives
COLOR_FN_RGB = (245, 158, 11)       # Amber for False Negatives


def draw_evaluation_overlay(
    image: np.ndarray,
    ground_truths: list[Annotation],
    predictions: list[Annotation],
    matches: list[tuple[str, str, float]] | None = None,
    thickness: int = 2,
) -> np.ndarray:
    """Render an RGB image overlay showing Ground Truth vs True Positives, False Positives, and False Negatives.

    `matches` is a list of (truth_id, pred_id, iou) at the desired threshold (typically 0.50).
    """
    canvas = image.copy()
    if canvas.ndim == 2:
        canvas = np.stack([canvas] * 3, axis=-1)
    elif canvas.shape[2] == 4:
        canvas = canvas[:, :, :3]

    matched_gt_ids = set()
    matched_pred_ids = set()
    iou_by_pred_id: dict[str, float] = {}

    if matches:
        for t_id, p_id, iou in matches:
            matched_gt_ids.add(t_id)
            matched_pred_ids.add(p_id)
            iou_by_pred_id[p_id] = iou

    if _HAS_CV2:
        # Convert RGB canvas to BGR for OpenCV drawing
        bgr = cv2.cvtColor(canvas, cv2.COLOR_RGB2BGR)

        # 1. Draw Ground Truth False Negatives (Amber)
        for gt in ground_truths:
            is_matched = gt.id in matched_gt_ids
            col = (248, 189, 56) if is_matched else (11, 158, 245)  # BGR
            if gt.polygon and len(gt.polygon) >= 3:
                pts = np.asarray(gt.polygon, dtype=np.int32).reshape((-1, 1, 2))
                cv2.polylines(bgr, [pts], isClosed=True, color=col, thickness=thickness, lineType=cv2.LINE_AA)
            else:
                x1, y1, x2, y2 = [int(v) for v in gt.bbox]
                cv2.rectangle(bgr, (x1, y1), (x2, y2), col, thickness)

        # 2. Draw Predictions: Green if TP, Red if FP
        for pred in predictions:
            is_tp = pred.id in matched_pred_ids
            col = (128, 222, 74) if is_tp else (68, 68, 239)  # BGR

            if pred.polygon and len(pred.polygon) >= 3:
                pts = np.asarray(pred.polygon, dtype=np.int32).reshape((-1, 1, 2))
                cv2.polylines(bgr, [pts], isClosed=True, color=col, thickness=thickness + 1, lineType=cv2.LINE_AA)
            else:
                x1, y1, x2, y2 = [int(v) for v in pred.bbox]
                cv2.rectangle(bgr, (x1, y1), (x2, y2), col, thickness + 1)

            # Draw IoU / score label
            label_text = f"{pred.confidence:.2f}" if pred.confidence is not None else ""
            if pred.id in iou_by_pred_id:
                label_text += f" (IoU:{iou_by_pred_id[pred.id]:.2f})"
            if label_text:
                x1, y1 = int(pred.bbox[0]), int(pred.bbox[1])
                cv2.putText(
                    bgr,
                    label_text,
                    (x1, max(12, y1 - 4)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.38,
                    col,
                    1,
                    cv2.LINE_AA,
                )

        return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)

    return canvas
