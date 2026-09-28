"""Post-hoc confidence calibration via Temperature Scaling."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from .metrics import expected_calibration_error


@dataclass
class TemperatureScaler:
    """Calibrates raw sigmoid confidence scores via a single positive temperature scalar T:
    calibrated_p = sigmoid(logit / T)
    where logit = log(p / (1 - p)).
    """

    temperature: float = 1.0

    def fit(
        self,
        confidences: Sequence[float],
        ground_truth_labels: Sequence[int | bool],
        min_temp: float = 0.1,
        max_temp: float = 10.0,
        num_candidates: int = 100,
    ) -> float:
        """Find optimal temperature T minimizing Expected Calibration Error on validation set."""
        if len(confidences) < 5:
            return self.temperature

        confs = np.asarray(confidences, dtype=np.float64)
        labels = np.asarray(ground_truth_labels, dtype=np.float64)

        # Clip confidences to prevent numerical divergence in logit
        clipped_confs = np.clip(confs, 1e-6, 1.0 - 1e-6)
        logits = np.log(clipped_confs / (1.0 - clipped_confs))

        best_t = 1.0
        best_ece = float("inf")

        for t in np.linspace(min_temp, max_temp, num_candidates):
            scaled_logits = logits / t
            # Numerical safe sigmoid
            calibrated = 1.0 / (1.0 + np.exp(-scaled_logits))
            ece = expected_calibration_error(calibrated, labels)
            if ece < best_ece:
                best_ece = ece
                best_t = float(t)

        self.temperature = best_t
        return self.temperature

    def calibrate(self, confidences: Sequence[float]) -> list[float]:
        """Apply learned temperature scaling to a list of raw confidence scores."""
        if abs(self.temperature - 1.0) < 1e-4:
            return [float(c) for c in confidences]

        confs = np.asarray(confidences, dtype=np.float64)
        clipped = np.clip(confs, 1e-6, 1.0 - 1e-6)
        logits = np.log(clipped / (1.0 - clipped))
        scaled_logits = logits / self.temperature
        calibrated = 1.0 / (1.0 + np.exp(-scaled_logits))
        return [float(np.clip(p, 0.0, 1.0)) for p in calibrated]
