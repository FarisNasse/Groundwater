from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from groundwater.config import Config
from groundwater.ingestion import connect, ingest, plan_query, scope_id
from groundwater.quality import normalize
from groundwater.storage import upsert
from test_pipeline import observation


class IncrementalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = replace(Config(), start='2023-01-01', end='2023-01-03')
        self.database = self.root / 'silver' / 'replay_ingestion.sqlite'
        self.counter = 0

    def replay(self, items, config=None, **options):
        self.counter += 1
        file = self.root / f'input-{self.counter}.json'
        file.write_text(json.dumps({'type': 'FeatureCollection', 'features': items, 'links': []}))
        return ingest(config or self.config, self.root, replay_file=file, **options)

    def rows(self, table):
        with sqlite3.connect(self.database) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(x) for x in conn.execute('SELECT * FROM ' + table)]

    def test_rerun_is_idempotent_and_watermark_tracks_requested_end(self):
        items = [observation(day='2023-01-01'), observation(day='2023-01-02')]
        self.replay(items)
        output = self.replay(items, lookback_days=3)
        counts = json.loads((output / 'manifest.json').read_text())['counts']
        self.assertEqual(counts['inserted'], 0)
        self.assertEqual(counts['unchanged'], 2)
        self.assertEqual(len(self.rows('source_revisions')), 2)
        self.assertEqual(self.rows('watermarks')[0]['completed_through'], '2023-01-03')
        self.assertEqual(len(self.rows('ingestion_events')), 2)

    def test_overlap_revision_withdrawal_restore_and_older_revision(self):
        self.replay([observation(day='2023-01-01'), observation(day='2023-01-02')])
        revised = observation(day='2023-01-02', value=99, last_modified='2025-02-01T00:00:00Z')
        result = self.replay([revised], lookback_days=2)
        # Query covers Jan 2-3; Jan 1 is outside the replacement window.
        self.assertEqual(len(pd.read_csv(result / 'observations.csv')), 2)
        self.replay([], lookback_days=2)
        self.assertEqual(len(self.rows('withdrawals')), 1)
        result = self.replay([observation(day='2023-01-02')], lookback_days=2)
        self.assertEqual(len(pd.read_csv(result / 'observations.csv')), 1)
        result = self.replay([revised], lookback_days=2)
        self.assertEqual(len(pd.read_csv(result / 'observations.csv')), 2)
        self.assertEqual(len(self.rows('source_revisions')), 3)
        self.assertEqual(len(self.rows('withdrawals')), 1)

    def test_quarantine_rolls_back_without_false_withdrawals_or_watermark_advance(self):
        self.replay([observation(day='2023-01-01')])
        before = self.rows('watermarks')
        with self.assertRaisesRegex(ValueError, 'Quarantined'):
            self.replay([observation(unit_of_measure='m')],
                        config=replace(self.config, end='2023-01-04'))
        self.assertEqual(self.rows('watermarks'), before)
        self.assertEqual(len(self.rows('measurements')), 1)
        self.assertEqual(self.rows('withdrawals'), [])
        self.assertEqual(self.rows('ingestion_events')[-1]['status'], 'failed')

    def test_conflicting_equal_revision_rolls_back_whole_snapshot(self):
        self.replay([observation()])
        with self.assertRaisesRegex(ValueError, 'Conflicting payloads'):
            self.replay([observation(value=2), observation(day='2023-01-02')])
        self.assertEqual(len(self.rows('measurements')), 1)
        self.assertEqual(len(self.rows('source_revisions')), 1)

    def test_legacy_cache_also_rejects_equal_revision_conflicts_across_runs(self):
        first, _, _ = normalize([observation()], self.config)
        second, _, _ = normalize([observation(value=99)], self.config)
        database = self.root / 'cache.sqlite'
        upsert(first, database)
        with self.assertRaisesRegex(ValueError, 'Conflicting cached'):
            upsert(second, database)

    def test_periodic_full_refresh_and_backfill_watermark(self):
        self.replay([observation(), observation(day='2023-01-03')])
        now = datetime.now(timezone.utc) + timedelta(days=31)
        query, kind = plan_query(self.config, self.database, now=now)
        self.assertEqual(kind, 'full_refresh')
        self.assertEqual(query.start, self.config.start)
        before = self.rows('watermarks')
        self.replay([], backfill=('2023-01-01', '2023-01-01'))
        self.assertEqual(self.rows('watermarks'), before)
        self.assertEqual(len(self.rows('withdrawals')), 1)
        with self.assertRaisesRegex(ValueError, 'end precedes'):
            plan_query(replace(self.config, end='2023-01-02'), self.database)

    def test_network_failure_keeps_watermark_and_records_failure(self):
        class BrokenClient:
            def daily(self, config, directory):
                raise RuntimeError('network unavailable')
        with self.assertRaisesRegex(RuntimeError, 'network unavailable'):
            ingest(self.config, self.root, client=BrokenClient())
        with sqlite3.connect(self.root / 'silver/live_ingestion.sqlite') as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM watermarks').fetchone()[0], 0)
            self.assertEqual(conn.execute('SELECT status FROM ingestion_events').fetchone()[0], 'failed')

    def test_sensor_change_is_not_merged_and_explicit_series_filter_is_counted(self):
        self.replay([observation()])
        with self.assertRaisesRegex(ValueError, 'Series changed'):
            self.replay([observation(day='2023-01-02', time_series_id='new-sensor')])
        frame, rejected, quality = normalize([observation(), observation(time_series_id='other')],
                                             replace(self.config, series_id='one-series'))
        self.assertEqual(len(frame), 1)
        self.assertEqual(rejected, [])
        self.assertEqual(quality['filtered_other_series'], 1)
        self.assertNotEqual(scope_id(self.config), scope_id(replace(self.config, series_id='one-series')))

    def test_export_failure_rolls_back_transaction(self):
        self.replay([observation()])
        before = self.rows('watermarks')
        with patch.object(pd.DataFrame, 'to_csv', side_effect=OSError('disk full')):
            with self.assertRaisesRegex(OSError, 'disk full'):
                self.replay([observation(value=90, last_modified='2025-03-01T00:00:00Z')])
        self.assertEqual(self.rows('watermarks'), before)
        self.assertEqual(len(self.rows('source_revisions')), 1)

    def test_concurrent_update_is_rejected_before_snapshot_commit(self):
        self.replay([observation()])
        original = normalize

        def other_writer(*args, **kwargs):
            with connect(self.database) as conn:
                conn.execute('UPDATE watermarks SET event_id=?', ('other-writer',))
                conn.commit()
            return original(*args, **kwargs)

        with patch('groundwater.ingestion.normalize', side_effect=other_writer):
            with self.assertRaisesRegex(ValueError, 'Watermark changed'):
                self.replay([observation(value=99, last_modified='2025-03-01T00:00:00Z')])
        self.assertEqual(len(self.rows('source_revisions')), 1)


if __name__ == '__main__':
    unittest.main()
