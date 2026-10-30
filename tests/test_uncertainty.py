"""Regression checks for experimental bands, saved results, and report geometry."""

from dataclasses import replace
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from groundwater.anomalies import detect_anomalies, summarize_anomaly_episodes
from groundwater.config import Config
from groundwater.dashboard import Dashboard
from groundwater.forecast import calibrate_intervals, hydrologic_summary
from groundwater.pipeline import run
from groundwater.report import chart

PROJECT = Path(__file__).resolve().parents[1]
BOUNDS = ["lower_80_ft", "upper_80_ft", "lower_95_ft", "upper_95_ft"]


def validation(errors, leads=None):
    return pd.DataFrame({"model": "persistence", "actual_ft": 0.0,
                         "predicted_ft": errors,
                         "lead_day": leads if leads is not None else np.ones(len(errors), int)})


def points(count=2):
    return pd.Series(10.0, index=pd.date_range("2025-01-01", periods=count))


class EmpiricalBandTests(unittest.TestCase):
    def test_error_quantiles_and_measured_holdout_coverage(self):
        # With errors 1..20, the higher 80th/95th percentiles are 17 and 20.
        valid = validation(np.arange(1, 21))
        actual = pd.Series([28.0, np.nan, 31.0], index=points(3).index)
        forecast, holdout, diagnostics = calibrate_intervals(
            valid, "persistence", points(1), actual, points(3))
        self.assertEqual(forecast.iloc[0].lower_80_ft, -7.0)
        self.assertEqual(forecast.iloc[0].upper_95_ft, 30.0)
        self.assertEqual(diagnostics["holdout_n_scored_95"], 1)
        self.assertEqual(diagnostics["holdout_coverage_95"], 1.0)
        # Leads without any validation targets must not inherit invented bands.
        self.assertTrue(holdout.iloc[1:][BOUNDS].isna().all().all())
        self.assertFalse(diagnostics["coverage_guarantee"])
        json.dumps(diagnostics, allow_nan=False)

    def test_coverage_uses_observed_targets_and_available_bounds(self):
        valid = validation(np.tile(np.arange(1, 21), 2), [1] * 20 + [2] * 20)
        actual = pd.Series([28.0, 31.0], index=points().index)
        _, _, diagnostics = calibrate_intervals(valid, "persistence", points(), actual, points())
        self.assertEqual(diagnostics["holdout_n_scored_80"], 2)
        self.assertEqual(diagnostics["holdout_coverage_80"], 0.0)
        self.assertEqual(diagnostics["holdout_coverage_95"], 0.5)
        actual.iloc[1] = np.nan
        _, _, sparse = calibrate_intervals(valid, "persistence", points(), actual, points())
        self.assertEqual(sparse["holdout_n_scored_95"], 1)
        self.assertEqual(sparse["holdout_coverage_95"], 1.0)

    def test_holdout_values_and_predictions_never_tune_widths(self):
        valid = validation(np.tile(np.arange(1, 21), 2), [1] * 20 + [2] * 20)
        first, held1, diag1 = calibrate_intervals(
            valid, "persistence", points(), points(), points())
        second, held2, diag2 = calibrate_intervals(
            valid, "persistence", points(), points() + 100, points() + 5)
        pd.testing.assert_frame_equal(first, second)
        np.testing.assert_allclose(held1.upper_95_ft - held1.predicted_ft,
                                   held2.upper_95_ft - held2.predicted_ft)
        self.assertEqual(diag1["calibration_by_lead"], diag2["calibration_by_lead"])
        self.assertEqual(diag1["holdout_coverage_95"], 1.0)
        self.assertEqual(diag2["holdout_coverage_95"], 0.0)

    def test_empty_or_sparse_errors_produce_missing_bounds_not_zero_width(self):
        for count in (0, 2, 19):
            with self.subTest(count=count):
                forecast, held, diagnostics = calibrate_intervals(
                    validation(np.ones(count)), "persistence", points(), points(), points())
                self.assertTrue(forecast[BOUNDS].isna().all().all())
                self.assertTrue(held[BOUNDS].isna().all().all())
                self.assertFalse(diagnostics["available"])
                self.assertIsNone(diagnostics["mean_width_95_ft"])
                self.assertIsNone(diagnostics["holdout_coverage_95"])
                self.assertEqual(diagnostics["holdout_n_scored_95"], 0)
                json.dumps(diagnostics, allow_nan=False)

    def test_lead_specific_errors_and_disclosed_pooling(self):
        valid = validation([1.0] * 20 + [10.0] * 20 + [3.0], [1] * 20 + [2] * 20 + [3])
        forecast, _, diagnostics = calibrate_intervals(valid, "persistence", points(4))
        self.assertEqual(forecast.iloc[0].upper_95_ft, 11.0)
        self.assertEqual(forecast.iloc[1].upper_95_ft, 20.0)
        self.assertEqual(diagnostics["calibration_by_lead"][2]["source"], "pooled_validation_leads")
        self.assertEqual(diagnostics["calibration_by_lead"][3]["source"], "unavailable")
        self.assertTrue(forecast.iloc[3][BOUNDS].isna().all())

    def test_nonfinite_targets_and_other_models_do_not_calibrate_bands(self):
        good = validation(np.arange(1, 21))
        bad = validation([1e6, np.nan, np.inf])
        bad.loc[0, "model"] = "other_model"
        combined = pd.concat([good, bad], ignore_index=True)
        expected, _, _ = calibrate_intervals(good, "persistence", points(1))
        actual, _, diagnostics = calibrate_intervals(combined, "persistence", points(1))
        pd.testing.assert_frame_equal(actual, expected)
        self.assertEqual(diagnostics["calibration_samples"], 20)

    def test_misaligned_or_incomplete_holdout_is_rejected(self):
        valid = validation(np.ones(20))
        with self.assertRaisesRegex(ValueError, "both"):
            calibrate_intervals(valid, "persistence", points(), holdout_actual=points())
        shifted = points().set_axis(pd.date_range("2025-01-02", periods=2))
        with self.assertRaisesRegex(ValueError, "identical dates"):
            calibrate_intervals(valid, "persistence", points(), points(), shifted)


class PresentationRegressionTests(unittest.TestCase):
    def test_interval_bounds_do_not_count_as_extra_chart_dates(self):
        series = pd.Series(10.0, index=pd.date_range("2024-01-01", periods=365))
        future = points(14).to_frame("predicted_depth_ft")
        for level, radius in ((80, 1.0), (95, 2.0)):
            future[f"lower_{level}_ft"] = 10.0 - radius
            future[f"upper_{level}_ft"] = 10.0 + radius
        html = chart(series, detect_anomalies(series), future)
        line = re.search(r'<polyline points="([^"]+)" fill="none" stroke="#a36c00"', html)
        self.assertIsNotNone(line)
        self.assertEqual(float(line.group(1).split()[-1].split(",")[0]), 930.0)
        future[BOUNDS] = np.nan
        html = chart(series, detect_anomalies(series), future)
        self.assertNotIn("<polygon", html)
        self.assertNotIn("nan", html)

    def test_anomaly_episodes_sort_dates_and_split_at_gaps(self):
        series = pd.Series(10.0, index=pd.date_range("2024-01-01", periods=150))
        series.iloc[[100, 101, 103]] = 20.0
        anomalies = detect_anomalies(series)
        episodes = summarize_anomaly_episodes(anomalies.iloc[::-1])
        self.assertEqual([e["duration_days"] for e in episodes], [2, 1])
        self.assertEqual(episodes[0]["direction"], "drawdown_spike")
        self.assertEqual(episodes[0]["max_deviation_ft"], 10.0)

    def test_trend_respects_depth_sign_and_elapsed_time(self):
        dates = pd.date_range("2020-01-01", periods=1200)
        series = pd.Series(10 + np.arange(1200) / 365.25, index=dates)
        self.assertAlmostEqual(hydrologic_summary(series)["sens_slope_ft_per_year"], 1.0)
        self.assertEqual(hydrologic_summary(series)["trend_direction"], "deepening")
        self.assertEqual(hydrologic_summary(-series)["trend_direction"], "recovering")

    def test_older_runs_retain_their_original_forecast_and_report_bytes(self):
        with tempfile.TemporaryDirectory() as root:
            app = Dashboard(PROJECT, root)
            try:
                identifier = next(r["id"] for r in app.results() if r["status"] == "complete")
                directory = app.result_directory(identifier)
                names = ("forecast.csv", "holdout.json", "report.html")
                before = {name: (directory / name).read_bytes() for name in names}
                with patch("groundwater.dashboard.run", side_effect=AssertionError("No new run")):
                    result = app.result(identifier)
                original = pd.read_csv(directory / "forecast.csv").to_dict("records")
                self.assertEqual(result["forecast"], original)
                self.assertFalse(result["uncertainty"]["available"])
                self.assertEqual(before, {name: (directory / name).read_bytes() for name in names})
                json.dumps(result, allow_nan=False)
            finally:
                app.close()

    def test_new_run_saved_bounds_and_dashboard_agree_including_missing_values(self):
        with tempfile.TemporaryDirectory() as root:
            config = replace(Config(), horizon=1, folds=2)
            directory = run(config, root, mode="synthetic")
            app = Dashboard(PROJECT, root)
            try:
                result = app.result(app.result_id(directory))
                self.assertFalse(result["uncertainty"]["available"])
                self.assertIsNone(result["forecast"][0]["lower_95_ft"])
                saved = pd.read_csv(directory / "forecast.csv")
                self.assertTrue(pd.isna(saved.iloc[0].lower_95_ft))
                self.assertIn("too few validation errors", (directory / "report.html").read_text())
                json.dumps(result, allow_nan=False)
            finally:
                app.close()


if __name__ == "__main__":
    unittest.main()
