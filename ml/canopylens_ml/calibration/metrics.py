"""Calibration metrics: Expected Calibration Error (ECE) and Brier Score."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class CalibrationBin:
    bin_lower: float
    bin_upper: float
    count: int
    mean_confidence: float
    accuracy: float
    gap: float


def expected_calibration_error(
    confidences: Sequence[float],
    ground_truth_labels: Sequence[int | bool],
    num_bins: int = 10,
) -> float:
    """Calculate Expected Calibration Error (ECE).

    Partitions [0, 1] into `num_bins` equal-width bins, computes the absolute
    difference between mean confidence and empirical accuracy in each bin, and
    weights by bin frequency.
    """
    if len(confidences) == 0 or len(confidences) != len(ground_truth_labels):
        return 0.0

    confs = np.asarray(confidences, dtype=np.float64)
    labels = np.asarray(ground_truth_labels, dtype=np.float64)
    n = len(confs)

    bin_boundaries = np.linspace(0.0, 1.0, num_bins + 1)
    ece = 0.0

    for i in range(num_bins):
        bin_lower = bin_boundaries[i]
        bin_upper = bin_boundaries[i + 1]

        if i == num_bins - 1:
            in_bin = (confs >= bin_lower) & (confs <= bin_upper)
        else:
            in_bin = (confs >= bin_lower) & (confs < bin_upper)

        count = np.sum(in_bin)
        if count > 0:
            bin_acc = float(np.mean(labels[in_bin]))
            bin_conf = float(np.mean(confs[in_bin]))
            ece += (count / n) * abs(bin_acc - bin_conf)

    return float(ece)


def brier_score(
    confidences: Sequence[float],
    ground_truth_labels: Sequence[int | bool],
) -> float:
    """Calculate Brier Score: mean squared error between confidence and binary outcome."""
    if len(confidences) == 0 or len(confidences) != len(ground_truth_labels):
        return 0.0

    confs = np.asarray(confidences, dtype=np.float64)
    labels = np.asarray(ground_truth_labels, dtype=np.float64)
    return float(np.mean((confs - labels) ** 2))


def reliability_diagram(
    confidences: Sequence[float],
    ground_truth_labels: Sequence[int | bool],
    num_bins: int = 10,
) -> list[CalibrationBin]:
    """Generate binned data for a reliability diagram curve."""
    if len(confidences) == 0 or len(confidences) != len(ground_truth_labels):
        return []

    confs = np.asarray(confidences, dtype=np.float64)
    labels = np.asarray(ground_truth_labels, dtype=np.float64)

    bin_boundaries = np.linspace(0.0, 1.0, num_bins + 1)
    bins = []

    for i in range(num_bins):
        bin_lower = float(bin_boundaries[i])
        bin_upper = float(bin_boundaries[i + 1])

        if i == num_bins - 1:
            in_bin = (confs >= bin_lower) & (confs <= bin_upper)
        else:
            in_bin = (confs >= bin_lower) & (confs < bin_upper)

        count = int(np.sum(in_bin))
        if count > 0:
            bin_acc = float(np.mean(labels[in_bin]))
            bin_conf = float(np.mean(confs[in_bin]))
            gap = abs(bin_acc - bin_conf)
        else:
            bin_acc = 0.0
            bin_conf = (bin_lower + bin_upper) / 2.0
            gap = 0.0

        bins.append(
            CalibrationBin(
                bin_lower=round(bin_lower, 2),
                bin_upper=round(bin_upper, 2),
                count=count,
                mean_confidence=round(bin_conf, 4),
                accuracy=round(bin_acc, 4),
                gap=round(gap, 4),
            )
        )

    return bins
