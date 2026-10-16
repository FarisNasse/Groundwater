"""Deliberately synthetic fixture; never presented as government measurements."""

import numpy as np
import pandas as pd


def synthetic_features(config):
    dates = pd.date_range(config.start, config.end, freq="D")
    rng = np.random.default_rng(42)
    t = np.arange(len(dates))
    values = 20 + 2 * np.sin(2 * np.pi * t / 365.25) + 0.001 * t + rng.normal(0, 0.06, len(t))
    if len(values) > 500:
        values[500] += 4  # injected spike for detector demonstrations
    result = []
    for day, value in zip(dates, values):
        result.append({"type": "Feature", "id": f"synthetic-{day.date()}", "geometry": None,
                       "properties": {
                           "monitoring_location_id": config.site,
                           "time_series_id": "SYNTHETIC-NOT-USGS-DATA",
                           "parameter_code": "72019", "statistic_id": "00003",
                           "time": str(day.date()), "value": str(value),
                           "unit_of_measure": "ft", "approval_status": "Synthetic",
                           "qualifier": None, "last_modified": "2025-01-01T00:00:00+00:00"}})
    return result
