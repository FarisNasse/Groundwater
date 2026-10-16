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
    mask = actual.notna() & predicted.notna()
    residual = predicted[mask].to_numpy() - actual[mask].to_numpy()
    if not len(residual):
        raise ValueError("No observed targets in evaluation window")
    return {"mae_ft": float(np.mean(np.abs(residual))),
            "rmse_ft": float(np.sqrt(np.mean(residual ** 2))),
            "bias_ft": float(np.mean(residual)), "n_observed": len(residual)}


def evaluate(series, config):
    horizon = config.horizon
    # Final horizon is locked away from model selection. Earlier windows do not overlap.
    development = series.iloc[:-horizon]
    needed = config.min_train_days + config.folds * horizon
    if len(development) < needed:
        raise ValueError(f"Need at least {needed + horizon} calendar days; got {len(series)}")
    rows, forecasts = [], []
    for fold in range(config.folds):
        end = len(development) - (config.folds - fold) * horizon
        train, actual = development.iloc[:end], development.iloc[end:end + horizon]
        for name in MODELS:
            pred = predict(name, train, horizon, config.max_gap_days)
            rows.append({"fold": fold + 1, "model": name,
                         "train_end": train.index[-1].date().isoformat(),
                         "test_start": actual.index[0].date().isoformat(),
                         "test_end": actual.index[-1].date().isoformat(), **metrics(actual, pred)})
            for lead, (day, value) in enumerate(pred.items(), 1):
                forecasts.append({"fold": fold + 1, "model": name, "date": str(day.date()),
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
    holdout = {"selected_model": chosen, "selection_metric": "pooled validation MAE (ft)",
               "start": str(holdout_actual.index[0].date()),
               "end": str(holdout_actual.index[-1].date()),
               **metrics(holdout_actual, holdout_prediction)}
    holdout_frame = pd.DataFrame({"actual_ft": holdout_actual,
                                  "predicted_ft": holdout_prediction})
    future = predict(chosen, series, horizon, config.max_gap_days)
    future_frame = pd.DataFrame({"predicted_depth_ft": future})
    for frame in (holdout_frame, future_frame):
        frame.index.name = "date"
    return {"leaderboard": leaderboard, "folds": folds, "validation_predictions": predictions,
            "holdout": holdout, "holdout_predictions": holdout_frame,
            "forecast": future_frame}
