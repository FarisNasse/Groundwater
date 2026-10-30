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


def summarize_anomaly_episodes(anomalies: pd.DataFrame) -> list[dict]:
    """Group consecutive flagged observations into interpretable anomaly episodes."""
    if anomalies.empty or "is_anomaly" not in anomalies.columns:
        return []
    flagged = anomalies[anomalies["is_anomaly"].fillna(False)].copy()
    if flagged.empty:
        return []
    flagged.index = pd.to_datetime(flagged.index)
    flagged = flagged.sort_index()
    dates = flagged.index
    flagged["_date"] = dates
    episode_ids = (dates.to_series().diff().dt.days.gt(1).fillna(True)).cumsum().to_numpy()
    flagged["_episode"] = episode_ids
    episodes = []
    for _, group in flagged.groupby("_episode", sort=True):
        deviations = group["depth_ft"] - group["reference_median_ft"]
        peak_idx = group["score"].astype(float).idxmax()
        peak_row = group.loc[peak_idx]
        signed_dev = float(peak_row["depth_ft"] - peak_row["reference_median_ft"])
        episodes.append({
            "start_date": str(pd.Timestamp(group.index[0]).date()),
            "end_date": str(pd.Timestamp(group.index[-1]).date()),
            "duration_days": int(len(group)),
            "peak_date": str(pd.Timestamp(peak_idx).date()),
            "peak_depth_ft": float(peak_row["depth_ft"]),
            "reference_median_ft": float(peak_row["reference_median_ft"]),
            "peak_score": round(float(peak_row["score"]), 3),
            "max_deviation_ft": round(float(deviations.abs().max()), 3),
            "direction": "drawdown_spike" if signed_dev > 0 else "recharge_or_upward_spike",
        })
    return episodes
