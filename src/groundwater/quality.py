"""An explicit data contract; raw measurements are never silently interpolated."""

import json
import math
from datetime import date
import numpy as np
import pandas as pd

COLUMNS = ["site", "series_id", "date", "depth_ft", "parameter_code", "statistic_id",
           "unit", "approval_status", "qualifier", "last_modified"]


def normalize(features, config):
    accepted, rejected, filtered = [], [], 0
    for feature in features:
        try:
            if not isinstance(feature, dict) or not isinstance(feature.get("properties"), dict):
                raise ValueError("invalid_feature_properties")
            p = feature["properties"]
            site = p["monitoring_location_id"]
            if site != config.site:
                raise ValueError("site_mismatch")
            if p.get("parameter_code") != config.parameter_code:
                raise ValueError("parameter_mismatch")
            if p.get("statistic_id") != config.statistic_id:
                raise ValueError("statistic_mismatch")
            if p.get("unit_of_measure") != "ft":
                raise ValueError("unit_mismatch")
            # Do not truncate a timestamp or change timezones: daily values are calendar dates.
            day = date.fromisoformat(p["time"]).isoformat()
            if not config.start <= day <= config.end:
                raise ValueError("outside_requested_window")
            value = float(p["value"])
            if not math.isfinite(value) or value == -999999:
                raise ValueError("missing_or_nonfinite_value")
            # Negative depth can represent water above land surface; do not reject it wholesale.
            series_id = p.get("time_series_id") or p.get("timeseries_id")
            if not series_id:
                raise ValueError("missing_series_id")
            if config.series_id is not None and str(series_id) != config.series_id:
                filtered += 1
                continue
            status = p.get("approval_status", p.get("approvals_status", "Unknown"))
            statuses = status if isinstance(status, list) else [status]
            if config.approved_only and statuses != ["Approved"]:
                raise ValueError("not_approved")
            modified = pd.Timestamp(p["last_modified"])
            if pd.isna(modified) or modified.tzinfo is None:
                raise ValueError("invalid_revision_timestamp")
            accepted.append({
                "site": site, "series_id": str(series_id), "date": day, "depth_ft": value,
                "parameter_code": p["parameter_code"], "statistic_id": p["statistic_id"],
                "unit": "ft", "approval_status": json.dumps(statuses),
                "qualifier": json.dumps(p.get("qualifier")),
                "last_modified": modified.tz_convert("UTC").isoformat(),
            })
        except (KeyError, ValueError, TypeError, OverflowError) as exc:
            rejected.append({"reason": str(exc), "feature": feature})
    frame = pd.DataFrame(accepted, columns=COLUMNS)
    duplicate_count = 0
    if not frame.empty:
        keys = ["site", "series_id", "date", "parameter_code", "statistic_id"]
        # An equally revised pair with different values is ambiguous: fail, never choose arbitrarily.
        conflicts = frame.groupby(keys + ["last_modified"])[
            ["depth_ft", "approval_status", "qualifier"]].nunique()
        if (conflicts > 1).any().any():
            raise ValueError("Conflicting values with identical series/date/revision")
        duplicate_count = int(frame.duplicated(keys).sum())
        frame = frame.sort_values("last_modified").drop_duplicates(keys, keep="last")
        if frame.duplicated(["site", "date"]).any():
            raise ValueError("Multiple time series on the same day; select a single series explicitly")
        frame = frame.sort_values("date").reset_index(drop=True)
    report = {"input_records": len(features), "valid_records": len(frame),
              "quarantined_records": len(rejected), "filtered_other_series": filtered,
              "duplicate_revisions_removed": duplicate_count}
    return frame, rejected, report


def daily_series(frame, config):
    if frame.empty:
        raise ValueError("No valid daily observations; inspect quality.json and quarantine.json")
    if frame.site.nunique() != 1 or frame.series_id.nunique() != 1:
        raise ValueError("Each analysis must use one monitoring location and one time series")
    observed = pd.Series(frame.depth_ft.to_numpy(), index=pd.to_datetime(frame.date), name="depth_ft")
    if observed.index.duplicated().any():
        raise ValueError("Duplicate dates in analysis series")
    # Preserve missing calendar days. No bfill or two-sided interpolation is allowed.
    observed = observed.sort_index().asfreq("D")
    missing = observed.isna()
    groups = (missing != missing.shift()).cumsum()
    longest_gap = int(missing.groupby(groups).sum().max())
    report = {"first_observed": observed.index[0].date().isoformat(),
              "last_observed": observed.index[-1].date().isoformat(),
              "calendar_days": len(observed), "observed_days": int(observed.notna().sum()),
              "missing_days": int(missing.sum()), "longest_gap_days": longest_gap,
              "coverage_within_observed_span": float(observed.notna().mean()),
              "requested_start": config.start, "requested_end": config.end,
              "missing_requested_leading_days": max(0, (observed.index[0].date()
                                                       - date.fromisoformat(config.start)).days),
              "missing_requested_trailing_days": max(0, (date.fromisoformat(config.end)
                                                        - observed.index[-1].date()).days),
              "age_days_at_run": (pd.Timestamp.now(tz="UTC").date()
                                  - observed.index[-1].date()).days,
              "provisional_or_other_records": int((frame.approval_status != '["Approved"]').sum()),
              "qualified_records": int((frame.qualifier != "null").sum())}
    # Quality reporting is possible even when forecasting must stop.
    issues = []
    if report["coverage_within_observed_span"] < config.min_coverage:
        issues.append("coverage below configured minimum")
    if longest_gap > config.max_gap_days:
        issues.append("gap too long for bounded forward filling; use another well or a sparse-data model")
    return observed.astype(np.float64), report, issues
