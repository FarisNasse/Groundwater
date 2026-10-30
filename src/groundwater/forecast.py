"""Same-origin recursive forecasts; rolling validation and a separate final holdout."""

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

MODELS = ("persistence", "seasonal_365", "ridge_lags", "random_forest_lags")
LAGS = (1, 7, 14, 30)


def features(history, timestamp):
    """For day t, every depth-derived feature uses values strictly before t."""
    values = np.asarray(history, dtype=float)
    if len(values) < 30:
        raise ValueError("Need at least 30 historical calendar days for lag features")
    angle = 2 * np.pi * (timestamp.dayofyear - 1) / 365.25
    return np.array([*[values[-lag] for lag in LAGS],
                     np.mean(values[-7:]), np.mean(values[-30:]),
                     np.std(values[-7:]), np.sin(angle), np.cos(angle)], dtype=float)


def training_matrix(train, max_gap_days):
    filled = train.ffill(limit=max_gap_days) if max_gap_days else train.copy()
    x, y = [], []
    for i in range(30, len(train)):
        if pd.isna(train.iloc[i]):
            continue  # targets are actual observations, never imputed labels
        row = features(filled.iloc[:i].to_numpy(), train.index[i])
        if np.isfinite(row).all():
            x.append(row)
            y.append(train.iloc[i])
    if len(y) < 100:
        raise ValueError("Too few complete training examples after causal preprocessing")
    return np.asarray(x), np.asarray(y)


def predict(model_name, train, horizon, max_gap_days=3):
    if model_name not in MODELS or horizon < 1:
        raise ValueError("Unknown model or nonpositive horizon")
    dates = pd.date_range(train.index[-1] + pd.Timedelta(days=1), periods=horizon, freq="D")
    filled = train.ffill(limit=max_gap_days) if max_gap_days else train.copy()
    if not np.isfinite(filled.iloc[-365:]).all() or len(filled) < 365:
        raise ValueError("Need a finite 365-day tail after bounded causal filling")
    if model_name == "persistence":
        return pd.Series(float(filled.iloc[-1]), index=dates)
    history = filled.to_list()
    if model_name in ("ridge_lags", "random_forest_lags"):
        x, y = training_matrix(train, max_gap_days)
        model = (make_pipeline(StandardScaler(), Ridge(alpha=10.0)) if model_name == "ridge_lags"
                 else RandomForestRegressor(n_estimators=80, max_depth=10,
                                            min_samples_leaf=5, random_state=42, n_jobs=1))
        model.fit(x, y)  # scaling and fitting are repeated within each training fold
    predictions = []
    for day in dates:
        value = (history[-365] if model_name == "seasonal_365"
                 else float(model.predict(features(history, day).reshape(1, -1))[0]))
        predictions.append(value)
        history.append(value)  # recursive: future actual observations are never fed back
    return pd.Series(predictions, index=dates)


def metrics(actual, predicted):
    mask = np.isfinite(actual) & np.isfinite(predicted)
    residual = predicted[mask].to_numpy() - actual[mask].to_numpy()
    if not len(residual):
        raise ValueError("No observed targets in evaluation window")
    return {"mae_ft": float(np.mean(np.abs(residual))),
            "rmse_ft": float(np.sqrt(np.mean(residual ** 2))),
            "bias_ft": float(np.mean(residual)), "n_observed": len(residual)}


def calibrate_intervals(validation_predictions: pd.DataFrame, model_name: str,
                        point_forecast: pd.Series, holdout_actual: pd.Series | None = None,
                        holdout_pred: pd.Series | None = None
                        ) -> tuple[pd.DataFrame, pd.DataFrame | None, dict]:
    """Experimental empirical bands, with widths determined before the final holdout.

    Use a lead's errors when at least 20 exist, otherwise pool validation leads.
    These are descriptive error quantiles, not guaranteed conformal intervals:
    errors are dependent and the same validation set also selects the model.
    """
    minimum_samples = 20
    if (holdout_actual is None) != (holdout_pred is None):
        raise ValueError("Supply both holdout actuals and predictions, or neither")
    if holdout_actual is not None and not holdout_actual.index.equals(holdout_pred.index):
        raise ValueError("Holdout actuals and predictions must have identical dates")
    sub = validation_predictions[validation_predictions["model"] == model_name].copy()
    valid = sub[np.isfinite(sub["actual_ft"]) & np.isfinite(sub["predicted_ft"])].copy()
    valid["abs_err"] = (valid["predicted_ft"] - valid["actual_ft"]).abs()
    pooled_errors = valid["abs_err"].to_numpy(dtype=float)
    n_leads = max(len(point_forecast), len(holdout_pred) if holdout_pred is not None else 0)
    widths = np.full((n_leads, 2), np.nan)
    calibration = []
    for lead in range(1, n_leads + 1):
        lead_errs = valid.loc[valid["lead_day"] == lead, "abs_err"].to_numpy(dtype=float)
        if len(lead_errs) >= minimum_samples:
            errors, source = lead_errs, "lead_day"
        elif len(lead_errs) and len(pooled_errors) >= minimum_samples:
            errors, source = pooled_errors, "pooled_validation_leads"
        else:
            errors, source = np.array([]), "unavailable"
        if len(errors):
            widths[lead - 1] = np.quantile(errors, [0.80, 0.95], method="higher")
        calibration.append({"lead_day": lead, "source": source,
                            "lead_samples": int(len(lead_errs)), "samples": int(len(errors))})

    def band_frame(points, column):
        frame = pd.DataFrame({column: points})
        for i, level in enumerate((80, 95)):
            frame[f"lower_{level}_ft"] = points.to_numpy(dtype=float) - widths[:len(points), i]
            frame[f"upper_{level}_ft"] = points.to_numpy(dtype=float) + widths[:len(points), i]
        frame.index.name = "date"
        return frame

    forecast_frame = band_frame(point_forecast, "predicted_depth_ft")
    available = bool(len(point_forecast) and np.isfinite(widths[:len(point_forecast)]).all())
    diagnostics = {
        "method": "validation_residual_empirical",
        "available": available,
        "coverage_guarantee": False,
        "calibration_samples": int(len(pooled_errors)),
        "minimum_calibration_samples": minimum_samples,
        "calibration_by_lead": calibration,
        "warning": ("Experimental bands from validation absolute-error quantiles. "
                    "Sparse leads pool errors across forecast days; pooled coverage is not "
                    "lead-specific coverage. Dependent errors, model selection, and refitting "
                    "mean nominal 80%/95% coverage is not guaranteed."),
    }
    if not available:
        diagnostics["warning"] = (
            "Bands are unavailable for some or all forecast days: require observed validation "
            "targets at that lead and at least 20 usable errors in the chosen group. "
            + diagnostics["warning"]
        )
    for i, level in enumerate((80, 95)):
        finite = widths[:len(point_forecast), i]
        finite = finite[np.isfinite(finite)]
        diagnostics[f"mean_width_{level}_ft"] = float(2 * finite.mean()) if len(finite) else None
    holdout_frame = None
    if holdout_pred is not None:
        holdout_frame = band_frame(holdout_pred, "predicted_ft")
        holdout_frame.insert(0, "actual_ft", holdout_actual)
        for level in (80, 95):
            cols = ["actual_ft", "predicted_ft", f"lower_{level}_ft", f"upper_{level}_ft"]
            scored = holdout_frame.loc[np.isfinite(holdout_frame[cols]).all(axis=1)]
            count = len(scored)
            covered = ((scored.actual_ft >= scored[f"lower_{level}_ft"]) &
                       (scored.actual_ft <= scored[f"upper_{level}_ft"]))
            diagnostics[f"holdout_n_scored_{level}"] = count
            diagnostics[f"holdout_coverage_{level}"] = float(covered.mean()) if count else None
    return forecast_frame, holdout_frame, diagnostics


def hydrologic_summary(series: pd.Series) -> dict:
    """Describe depth, monthly-median amplitude, and approximate median pairwise slope."""
    clean = series.dropna()
    if clean.empty:
        return {}
    values = clean.to_numpy(dtype=float)
    days = (clean.index - clean.index[0]).days.to_numpy(dtype=float)
    step = max(1, int(np.ceil(len(values) / 180)))
    idx_v, idx_d = values[::step], days[::step]
    slopes = []
    for i in range(len(idx_v)):
        dt = idx_d[i + 1:] - idx_d[i]
        valid = dt >= 30.0
        if np.any(valid):
            slopes.extend(((idx_v[i + 1:][valid] - idx_v[i]) / (dt[valid] / 365.25)).tolist())
    sens_slope = float(np.median(slopes)) if slopes else 0.0
    monthly = clean.groupby(clean.index.month).median()
    seasonal_amp = float(monthly.max() - monthly.min()) if len(monthly) >= 2 else 0.0
    diffs = series.diff().dropna()
    return {
        "median_depth_ft": round(float(np.median(values)), 3),
        "p10_depth_ft": round(float(np.percentile(values, 10)), 3),
        "p90_depth_ft": round(float(np.percentile(values, 90)), 3),
        "seasonal_amplitude_ft": round(seasonal_amp, 3),
        "sens_slope_ft_per_year": round(sens_slope, 4),
        "trend_direction": ("deepening" if sens_slope > 0.05
                            else "recovering" if sens_slope < -0.05 else "stable"),
        "daily_change_std_ft": round(float(diffs.std()) if len(diffs) > 1 else 0.0, 4),
    }


def validation_diagnostics(predictions):
    """Validation-only error slices; season is meteorological season of the target date."""
    data = predictions.copy()
    dates = pd.to_datetime(data.date)
    data["year"] = dates.dt.year
    data["season"] = dates.dt.month.map({12: "DJF", 1: "DJF", 2: "DJF",
        3: "MAM", 4: "MAM", 5: "MAM", 6: "JJA", 7: "JJA", 8: "JJA",
        9: "SON", 10: "SON", 11: "SON"})
    output = {}
    for dimension in ("lead_day", "season", "year"):
        rows = []
        for (name, value), group in data.groupby(["model", dimension], sort=True):
            if not (np.isfinite(group.actual_ft) & np.isfinite(group.predicted_ft)).any():
                continue
            rows.append({"model": name, dimension: value,
                         **metrics(group.actual_ft, group.predicted_ft)})
        output[f"errors_by_{dimension}"] = pd.DataFrame(rows)
    return output


def required_calendar_days(config):
    stride = config.validation_stride_days or config.horizon
    return config.min_train_days + 2 * config.horizon + (config.folds - 1) * stride


def readiness_issues(series, config):
    """Check all model prerequisites without fitting models or looking at their scores."""
    needed = required_calendar_days(config)
    if len(series) < needed:
        return [f"Need at least {needed} calendar days; got {len(series)}"]
    horizon = config.horizon
    development = series.iloc[:-horizon]
    stride = config.validation_stride_days or horizon
    windows = []
    for fold in range(config.folds):
        end = len(development) - horizon - (config.folds - fold - 1) * stride
        windows.append((development.iloc[:end], development.iloc[end:end + horizon]))
    windows.append((development, series.iloc[-horizon:]))
    windows.append((series, None))
    for train, actual in windows:
        filled = train.ffill(limit=config.max_gap_days) if config.max_gap_days else train
        if len(train) < 365 or not np.isfinite(filled.iloc[-365:]).all():
            return ["Need a finite 365-day tail after bounded causal filling"]
        try:
            training_matrix(train, config.max_gap_days)
        except ValueError as exc:
            return [str(exc)]
        if actual is not None and not actual.notna().any():
            return ["No observed targets in evaluation window"]
    return []


def evaluate(series, config):
    horizon = config.horizon
    # Final horizon is locked away from model selection. Earlier windows do not overlap.
    development = series.iloc[:-horizon]
    stride = config.validation_stride_days or horizon
    needed = required_calendar_days(config)
    if len(series) < needed:
        raise ValueError(f"Need at least {needed} calendar days; got {len(series)}")
    rows, forecasts = [], []
    for fold in range(config.folds):
        end = len(development) - horizon - (config.folds - fold - 1) * stride
        train, actual = development.iloc[:end], development.iloc[end:end + horizon]
        for name in MODELS:
            pred = predict(name, train, horizon, config.max_gap_days)
            rows.append({"fold": fold + 1, "model": name,
                         "forecast_origin": train.index[-1].date().isoformat(),
                         "train_end": train.index[-1].date().isoformat(),
                         "test_start": actual.index[0].date().isoformat(),
                         "test_end": actual.index[-1].date().isoformat(), **metrics(actual, pred)})
            for lead, (day, value) in enumerate(pred.items(), 1):
                forecasts.append({"fold": fold + 1, "model": name, "date": str(day.date()),
                                  "forecast_origin": str(train.index[-1].date()),
                                  "lead_day": lead, "actual_ft": actual.loc[day],
                                  "predicted_ft": value})
    folds = pd.DataFrame(rows)
    predictions = pd.DataFrame(forecasts)
    # Pooled errors weight individual observed targets, rather than sparse folds equally.
    comparison = []
    for name in MODELS:
        subset = predictions[predictions.model == name]
        comparison.append({"model": name, **metrics(subset.actual_ft, subset.predicted_ft)})
    leaderboard = pd.DataFrame(comparison).sort_values(["mae_ft", "model"]).reset_index(drop=True)
    chosen = str(leaderboard.iloc[0].model)
    holdout_actual = series.iloc[-horizon:]
    holdout_prediction = predict(chosen, development, horizon, config.max_gap_days)
    future = predict(chosen, series, horizon, config.max_gap_days)
    future_frame, holdout_frame, uncertainty = calibrate_intervals(
        predictions, chosen, future, holdout_actual, holdout_prediction
    )
    holdout = {"selected_model": chosen, "selection_metric": "pooled validation MAE (ft)",
               "start": str(holdout_actual.index[0].date()),
               "end": str(holdout_actual.index[-1].date()),
               **metrics(holdout_actual, holdout_prediction),
               "uncertainty": uncertainty}
    return {"leaderboard": leaderboard, "folds": folds, "validation_predictions": predictions,
            "holdout": holdout, "holdout_predictions": holdout_frame,
            "forecast": future_frame, "uncertainty": uncertainty,
            "hydrology": hydrologic_summary(series), **validation_diagnostics(predictions)}
