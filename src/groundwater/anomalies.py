"""Causal rolling median/MAD scores; flags are investigation leads, not diagnoses."""

import numpy as np
import pandas as pd


def detect_anomalies(series, window=60, threshold=4.5, scale_floor_ft=0.05):
    # shift(1) keeps the observation being scored OUT of its reference distribution.
    # min_periods counts observed days; gaps are not synthetic measurements.
    history = series.shift(1).rolling(window, min_periods=max(10, window // 2))
    center = history.median()
    mad = history.apply(lambda values: np.nanmedian(np.abs(values - np.nanmedian(values))), raw=True)
    # A configurable floor avoids infinite scores in a constant or rounded sensor history.
    scale = (1.4826 * mad).clip(lower=scale_floor_ft)
    score = (series - center).abs() / scale
    result = pd.DataFrame({"depth_ft": series, "reference_median_ft": center,
                           "robust_scale_ft": scale, "score": score,
                           "is_anomaly": score.gt(threshold) & series.notna(),
                           "scorable": score.notna()})
    result.index.name = "date"
    return result
