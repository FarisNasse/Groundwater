from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from groundwater.cli import main
from groundwater.config import Config
from groundwater.forecast import evaluate, validation_diagnostics
from groundwater.study import run_study


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.config = replace(Config(), horizon=7, folds=4, validation_stride_days=90)
        dates = pd.date_range('2020-01-01', periods=760)
        self.series = pd.Series(10 + np.sin(np.arange(760) / 50), index=dates)

    def test_spaced_origins_nonoverlap_and_diagnostics_exclude_holdout(self):
        first = evaluate(self.series, self.config)
        folds = first['folds'].query("model == 'persistence'")
        origins = pd.to_datetime(folds.forecast_origin)
        self.assertTrue((origins.diff().dropna().dt.days == 90).all())
        self.assertLess(folds.test_end.max(), first['holdout']['start'])
        self.assertEqual(len(first['validation_predictions']), 4 * 4 * 7)
        for key in ('errors_by_lead_day', 'errors_by_season', 'errors_by_year'):
            self.assertTrue((first[key].groupby('model').n_observed.sum() == 28).all())
        changed = self.series.copy()
        changed.iloc[-7:] += 100
        second = evaluate(changed, self.config)
        for key in ('leaderboard', 'errors_by_lead_day', 'errors_by_season', 'errors_by_year'):
            pd.testing.assert_frame_equal(first[key], second[key])

    def test_diagnostic_season_mapping_and_missing_target_counts(self):
        frame = pd.DataFrame({'model': ['persistence'] * 5,
            'date': ['2024-01-01', '2024-04-01', '2024-07-01', '2024-10-01', '2024-12-01'],
            'lead_day': [1, 2, 3, 4, 5], 'actual_ft': [1, 2, 3, 4, np.nan],
            'predicted_ft': [2, 1, 5, 4, 10]})
        result = validation_diagnostics(frame)
        seasons = result['errors_by_season'].set_index('season')
        self.assertEqual(set(seasons.index), {'DJF', 'MAM', 'JJA', 'SON'})
        self.assertEqual(seasons.n_observed.sum(), 4)
        self.assertEqual(seasons.loc['JJA', 'mae_ft'], 2)
        self.assertEqual(seasons.loc['MAM', 'bias_ft'], -1)
        self.assertEqual(result['errors_by_year'].iloc[0].n_observed, 4)

    def test_overlap_configuration_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'nonoverlapping'):
            replace(self.config, validation_stride_days=6)


class StudyTests(unittest.TestCase):
    def test_failed_experiment_is_reported_without_suppressing_other_results(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'valid.toml').write_text('horizon=7\nfolds=2\n')
            declaration = root / 'study.json'
            declaration.write_text(json.dumps({'experiments': [
                {'id': 'one', 'config': 'missing.toml', 'snapshot': 'data.geojson'},
                {'id': 'two', 'config': 'valid.toml', 'snapshot': 'missing.geojson'}]}))
            directory, succeeded = run_study(declaration, root / 'out')
            self.assertFalse(succeeded)
            manifest = json.loads((directory / 'study.json').read_text())
            self.assertEqual(len(manifest['experiments']), 2)
            self.assertEqual(manifest['status'], 'failed')
            self.assertTrue((directory / 'index.html').exists())

    def test_duplicate_unsafe_and_malformed_experiment_ids_are_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            file = Path(d) / 'study.json'
            for experiments in (
                [{'id': '../escape', 'config': 'c', 'snapshot': 's'}],
                [{'id': 'x', 'config': 'c', 'snapshot': 's'}] * 2,
                [{'id': None}], 'wrong-shape'):
                file.write_text(json.dumps({'experiments': experiments}))
                with self.assertRaises(ValueError):
                    run_study(file, Path(d) / 'out')

    def test_cli_reports_study_failure_and_requires_both_backfill_dates(self):
        with patch('groundwater.cli.run_study', return_value=(Path('out'), False)):
            self.assertEqual(main(['study']), 1)
        self.assertEqual(main(['ingest', '--backfill-start', '2023-01-01']), 1)


if __name__ == '__main__':
    unittest.main()
