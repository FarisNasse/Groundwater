from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from groundwater.anomalies import detect_anomalies, summarize_anomaly_episodes
from groundwater.config import Config
from groundwater.demo import synthetic_features
from groundwater.forecast import evaluate, features, hydrologic_summary, predict, training_matrix
from groundwater.pipeline import run
from groundwater.quality import daily_series, normalize
from groundwater.storage import upsert
from groundwater.usgs import BASE, USGSClient


def observation(value=12.3, day="2023-01-01", **overrides):
    properties = {"monitoring_location_id": Config().site, "time_series_id": "one-series",
                  "parameter_code": "72019", "statistic_id": "00003", "time": day,
                  "value": value, "unit_of_measure": "ft", "approval_status": "Approved",
                  "last_modified": "2025-01-01T00:00:00Z", "qualifier": None}
    properties.update(overrides)
    return {"type": "Feature", "properties": properties}


class ContractTests(unittest.TestCase):
    def test_quarantine_bad_units_nonfinite_dates_and_parameters(self):
        items = [observation(), observation(unit_of_measure="m"), observation(value="NaN"),
                 observation(value=-999999), observation(day="2023-01-01T00:00:00Z"),
                 observation(parameter_code="00065")]
        valid, quarantine, quality = normalize(items, Config())
        self.assertEqual(len(valid), 1)
        self.assertEqual(len(quarantine), 5)
        self.assertEqual(quality["quarantined_records"], 5)

    def test_negative_depth_is_valid_and_provisional_policy_is_explicit(self):
        items = [observation(value=-1, approval_status="Provisional")]
        self.assertEqual(len(normalize(items, Config())[0]), 1)
        self.assertTrue(normalize(items, replace(Config(), approved_only=True))[0].empty)

    def test_revisions_choose_latest_without_averaging(self):
        data, _, quality = normalize([observation(value=9), observation(value=10,
            last_modified="2025-02-01T00:00:00Z")], Config())
        self.assertEqual(data.depth_ft.iloc[0], 10)
        self.assertEqual(quality["duplicate_revisions_removed"], 1)

    def test_ambiguous_series_and_conflicting_revisions_fail(self):
        with self.assertRaisesRegex(ValueError, "Multiple time series"):
            normalize([observation(), observation(time_series_id="other")], Config())
        with self.assertRaisesRegex(ValueError, "Conflicting"):
            normalize([observation(value=9), observation(value=10)], Config())

    def test_gap_is_retained_and_excessive_gap_blocks_forecast(self):
        data, _, _ = normalize([observation(), observation(day="2023-01-10")], Config())
        series, quality, issues = daily_series(data, Config())
        self.assertEqual(int(series.isna().sum()), 8)
        self.assertEqual(quality["longest_gap_days"], 8)
        self.assertTrue(issues)

    def test_sql_upsert_is_idempotent_and_older_revisions_cannot_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "test.sqlite"
            newer, _, _ = normalize([observation(value=11,
                                    last_modified="2025-03-01T00:00:00Z")], Config())
            older, _, _ = normalize([observation(value=10)], Config())
            for frame in [newer, newer, older]:
                upsert(frame, db)
            with sqlite3.connect(db) as connection:
                self.assertEqual(connection.execute("SELECT count(*), depth_ft FROM observations").fetchone(),
                                 (1, 11.0))


class ScienceTests(unittest.TestCase):
    def setUp(self):
        self.config = replace(Config(), horizon=7, folds=2)
        frame, _, _ = normalize(synthetic_features(self.config), self.config)
        self.series = daily_series(frame, self.config)[0]

    def test_anomaly_reference_excludes_current_and_future_values(self):
        values = pd.Series(np.ones(150), index=pd.date_range("2020-01-01", periods=150))
        changed = values.copy()
        changed.iloc[100] = 10
        base = detect_anomalies(values)
        scored = detect_anomalies(changed)
        pd.testing.assert_frame_equal(base.iloc[:100], scored.iloc[:100])
        self.assertEqual(scored.reference_median_ft.iloc[100], 1)
        self.assertTrue(scored.is_anomaly.iloc[100])
        self.assertFalse(scored.scorable.iloc[0])

    def test_lags_use_past_values_and_missing_targets_are_excluded(self):
        past = np.arange(50, dtype=float)
        vector = features(past, pd.Timestamp("2024-01-01"))
        np.testing.assert_equal(vector[:4], [49, 43, 36, 20])
        sample = self.series.iloc[:400].copy()
        sample.iloc[100] = np.nan
        _, y = training_matrix(sample, 3)
        self.assertEqual(len(y), 369)  # 400 - first 30 - one missing observed label

    def test_forecasts_do_not_change_when_future_data_change(self):
        train = self.series.iloc[:500]
        original = predict("ridge_lags", train, 14)
        changed = self.series.copy()
        changed.iloc[500:] += 1000
        pd.testing.assert_series_equal(original, predict("ridge_lags", changed.iloc[:500], 14))

    def test_final_holdout_does_not_influence_model_selection(self):
        first = evaluate(self.series, self.config)
        changed = self.series.copy()
        changed.iloc[-7:] += 100
        second = evaluate(changed, self.config)
        pd.testing.assert_frame_equal(first["leaderboard"], second["leaderboard"])
        pd.testing.assert_frame_equal(first["folds"], second["folds"])
        self.assertNotEqual(first["holdout"]["mae_ft"], second["holdout"]["mae_ft"])
        self.assertLess(first["folds"].test_end.max(), first["holdout"]["start"])

    def test_insufficient_history_fails_clearly(self):
        with self.assertRaisesRegex(ValueError, "Need at least"):
            evaluate(self.series.iloc[:200], self.config)

    def test_empirical_bands_hydrology_and_anomaly_episodes(self):
        result = evaluate(self.series, replace(self.config, folds=4))
        forecast = result["forecast"]
        self.assertTrue({"lower_80_ft", "upper_80_ft", "lower_95_ft", "upper_95_ft"}.issubset(forecast.columns))
        self.assertTrue((forecast["lower_95_ft"] <= forecast["lower_80_ft"]).all())
        self.assertTrue((forecast["upper_80_ft"] <= forecast["upper_95_ft"]).all())
        hydro = hydrologic_summary(self.series)
        self.assertIn("sens_slope_ft_per_year", hydro)
        self.assertGreater(hydro["seasonal_amplitude_ft"], 0)
        episodes = summarize_anomaly_episodes(detect_anomalies(self.series))
        self.assertGreaterEqual(len(episodes), 1)


class IngestionTests(unittest.TestCase):
    def test_pagination_archives_all_pages_and_preserves_filters(self):
        next_url = BASE + "/collections/daily/items?offset=1&f=json"
        page1 = json.dumps({"type": "FeatureCollection", "features": [observation()],
                            "links": [{"rel": "next", "href": next_url}]}).encode()
        page2 = json.dumps({"type": "FeatureCollection", "features": [observation(day="2023-01-02")],
                            "links": []}).encode()
        client = USGSClient()
        with tempfile.TemporaryDirectory() as d, patch.object(client, "_get", side_effect=[page1, page2]) as get:
            data, pages = client.daily(Config(), d)
            self.assertEqual(len(data), 2)
            self.assertEqual(len(pages), 2)
            self.assertIn("statistic_id=00003", get.call_args_list[0].args[0])
            self.assertEqual(get.call_args_list[1].args[0], next_url)
            self.assertTrue(all(Path(p["path"]).exists() for p in pages))

    def test_external_pagination_is_rejected_before_second_request(self):
        page = json.dumps({"type": "FeatureCollection", "features": [], "links": [
            {"rel": "next", "href": "https://example.com/steal"}]}).encode()
        client = USGSClient(api_key="test-key")
        with tempfile.TemporaryDirectory() as d, patch.object(client, "_get", return_value=page) as get:
            with self.assertRaisesRegex(ValueError, "untrusted"):
                client.daily(Config(), d)
            self.assertEqual(get.call_count, 1)

    def test_failure_provenance_is_written(self):
        with tempfile.TemporaryDirectory() as d:
            invalid = Path(d) / "empty.json"
            invalid.write_text('{"type":"FeatureCollection","features":[]}')
            with self.assertRaisesRegex(ValueError, "No valid"):
                run(Config(), Path(d) / "out", mode="replay", replay_file=invalid)
            manifest = next((Path(d) / "out" / "runs").glob("*/provenance.json"))
            self.assertEqual(json.loads(manifest.read_text())["status"], "failed")


if __name__ == "__main__":
    unittest.main()
