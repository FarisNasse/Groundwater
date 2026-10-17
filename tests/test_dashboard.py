from contextlib import contextmanager
from dataclasses import replace
import hashlib
import http.client
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from groundwater.config import Config
from groundwater.dashboard import (Dashboard, DashboardServer, analysis_config,
                                   project_directory, readiness)
from groundwater.demo import synthetic_features
from groundwater.forecast import required_calendar_days
from groundwater.pipeline import run
from groundwater.quality import normalize

PROJECT = Path(__file__).resolve().parents[1]


def payload(**changes):
    return {"source": "oregon_recent", "start": "2023-01-01", "end": "2025-12-31",
            "horizon": 7, "folds": 2, **changes}


def finished(app, job):
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        current = app.job(job["id"])
        if current["status"] not in ("queued", "running"):
            return current
        time.sleep(0.02)
    raise AssertionError("Task did not finish")


@contextmanager
def dashboard():
    with tempfile.TemporaryDirectory() as directory:
        app = Dashboard(PROJECT, Path(directory) / "artifacts")
        try:
            yield app
        finally:
            app.close()


class ReadinessTests(unittest.TestCase):
    def test_forecast_history_formula_includes_holdout_and_stride(self):
        config = replace(Config(), horizon=14, folds=4, validation_stride_days=30)
        self.assertEqual(required_calendar_days(config), 365 + 28 + 90)

    def test_long_gap_has_actionable_explanation(self):
        config = Config()
        items = synthetic_features(config)
        items = [item for item in items if not "2023-05-01" <= item["properties"]["time"] <= "2023-07-31"]
        frame, _, quality = normalize(items, config)
        result = readiness(frame, quality, config)
        self.assertFalse(result["ready"])
        self.assertTrue(any("92-day gap" in s and "three" not in s and "3 days" in s
                            for s in result["blockers"]))
        self.assertTrue(any(p["value"] is None for p in result["points"]))

    def test_short_history_is_blocked_before_training(self):
        config = replace(Config(), end="2023-06-30")
        frame, _, quality = normalize(synthetic_features(config), config)
        result = readiness(frame, quality, config)
        self.assertFalse(result["ready"])
        self.assertIn("Need at least", result["blockers"][0])

    def test_missing_entire_evaluation_window_is_blocked(self):
        config = replace(Config(), horizon=1, folds=2)
        items = synthetic_features(config)
        # The final holdout's actual is missing; missing trailing days change series end,
        # so make an earlier validation target missing instead.
        del items[-3]
        frame, _, quality = normalize(items, config)
        result = readiness(frame, quality, config)
        self.assertFalse(result["ready"])
        self.assertTrue(any("No observed targets" in s for s in result["blockers"]))

    def test_form_limits_and_snapshot_bounds_are_enforced_on_server(self):
        for change in ({"horizon": 91}, {"folds": True}, {"start": "2010-01-01"},
                       {"end": "2020-01-01"}, {"source": "../secret"}, {"horizon": "7"},
                       {"approved_only": "false"}, {"stride": 1}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                analysis_config(payload(**change))
        self.assertEqual(analysis_config(payload()).site, "USGS-420318121312201")


class DashboardTests(unittest.TestCase):
    def test_only_one_dashboard_can_write_a_root_and_lock_releases(self):
        with tempfile.TemporaryDirectory() as directory:
            first = Dashboard(PROJECT, directory)
            try:
                with self.assertRaisesRegex(ValueError, "Another dashboard"):
                    Dashboard(PROJECT, directory)
            finally:
                first.close()
            second = Dashboard(PROJECT, directory)
            second.close()

    def test_bundled_results_open_without_training(self):
        with dashboard() as app, patch("groundwater.dashboard.run", side_effect=AssertionError("No fitting")):
            rows = app.results()
            self.assertGreaterEqual(len(rows), 3)
            result = app.result(next(r["id"] for r in rows if r["status"] == "complete"))
            self.assertTrue(result["forecast"])
            self.assertIn("age_days_today", result["quality"])
            self.assertEqual(result["leaderboard"][0]["model"], result["holdout"]["selected_model"])

    def test_check_and_run_reuse_identical_snapshot_and_survive_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "artifacts"
            app = Dashboard(PROJECT, root)
            try:
                checked = finished(app, app.submit("check", payload()))
                self.assertEqual(checked["status"], "complete", checked)
                self.assertTrue(checked["result"]["ready"])
                with patch("groundwater.pipeline.acquire", side_effect=AssertionError("No second download")):
                    complete = finished(app, app.submit("analyze", {"check_id": checked["id"]}))
                self.assertEqual(complete["status"], "complete", complete)
                result = app.result(complete["result"]["result_id"])
                self.assertTrue(result["provenance"]["checked_snapshot_reused"])
                raw = (app.state / "checks" / checked["id"] / "snapshot.geojson").read_bytes()
                self.assertEqual(result["provenance"]["dashboard_check"]["snapshot_sha256"],
                                 hashlib.sha256(raw).hexdigest())
                self.assertEqual(result["provenance"]["source_mode"], "replay")
                self.assertEqual(result["quality"]["observed_days"], checked["result"]["quality"]["observed_days"])
                identifier = complete["result"]["result_id"]
            finally:
                app.close()
            reopened = Dashboard(PROJECT, root)
            try:
                self.assertEqual(reopened.job(complete["id"])["status"], "complete")
                self.assertEqual(reopened.result(identifier)["holdout"], result["holdout"])
            finally:
                reopened.close()

    def test_live_checked_input_retains_live_source_and_page_provenance(self):
        with dashboard() as app:
            request = payload(source="live", site=Config().site, end="2024-12-31")
            config = analysis_config(request)
            pages = [{"request_url": "https://api.waterdata.usgs.gov/example", "sha256": "test"}]
            with patch("groundwater.dashboard.acquire", return_value=(synthetic_features(config), pages)):
                checked = finished(app, app.submit("check", request))
            self.assertTrue(checked["result"]["ready"])
            with patch("groundwater.dashboard.run", return_value=app.root / "fake") as runner:
                (app.root / "fake").mkdir()
                (app.root / "fake" / "provenance.json").write_text('{}')
                complete = finished(app, app.submit("analyze", {"check_id": checked["id"]}))
                self.assertEqual(complete["status"], "complete")
                self.assertEqual(runner.call_args.args[2], "live")
                self.assertEqual(runner.call_args.kwargs["prepared"][1], pages)

    def test_blocked_and_changed_snapshots_cannot_run(self):
        with dashboard() as app:
            short = finished(app, app.submit("check", payload(end="2023-03-01")))
            self.assertFalse(short["result"]["ready"])
            with self.assertRaisesRegex(ValueError, "readiness"):
                app.submit("analyze", {"check_id": short["id"]})
            good = finished(app, app.submit("check", payload()))
            (app.state / "checks" / good["id"] / "snapshot.geojson").write_text('{}')
            bad = finished(app, app.submit("analyze", {"check_id": good["id"]}))
            self.assertEqual(bad["status"], "failed")
            self.assertIn("Check the data again", bad["message"])

    def test_failed_network_task_is_saved_and_retryable(self):
        with dashboard() as app, patch("groundwater.dashboard.USGSClient.discover",
                                      side_effect=RuntimeError("USGS HTTP 429; retries exhausted")):
            failed = finished(app, app.submit("discover", {"bbox": "-123,45,-122,46"}))
            self.assertEqual(failed["status"], "failed")
            self.assertIn("bundled example", failed["message"])
            self.assertTrue((app.state / "jobs" / f"{failed['id']}.json").exists())

    def test_stopped_jobs_become_interrupted_and_wells_are_deduplicated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            jobs = root / "dashboard" / "jobs"
            jobs.mkdir(parents=True)
            (jobs / "one.json").write_text(json.dumps({"id": "one", "status": "running"}))
            app = Dashboard(PROJECT, root)
            try:
                self.assertEqual(app.job("one")["status"], "interrupted")
                app.save_well({"site": Config().site, "series_id": "a"})
                app.save_well({"site": Config().site, "series_id": "a", "name": "My well"})
                self.assertEqual(len(app.wells()), 1)
                self.assertEqual(app.wells()[0]["name"], "My well")
            finally:
                app.close()

    def test_discovery_returns_individual_series_and_filters_wrong_units(self):
        def feature(series, unit):
            return {"properties": {"monitoring_location_id": Config().site, "time_series_id": series,
                    "unit_of_measure": unit, "time": "2025-01-01"},
                    "geometry": {"coordinates": [-70, 44]}}
        with dashboard() as app, patch("groundwater.dashboard.USGSClient.discover",
                    return_value=([feature("a", "ft"), feature("b", "ft"), feature("c", "m")], [])):
            job = finished(app, app.submit("discover", {"bbox": "-71,43,-69,45"}))
            self.assertEqual(len(job["result"]["candidates"]), 2)

    def test_pipeline_progress_and_prepared_mode_keep_existing_outputs(self):
        config = replace(Config(), horizon=7, folds=2)
        stages = []
        with tempfile.TemporaryDirectory() as directory:
            output = run(config, directory, "live", prepared=(synthetic_features(config), []),
                         progress=lambda stage, _: stages.append(stage))
            self.assertEqual(stages, ["downloading", "checking", "comparing", "saving"])
            self.assertTrue((output / "report.html").exists())
            self.assertEqual(json.loads((output / "provenance.json").read_text())["source_mode"], "live")

    def test_bbox_rejects_unbounded_and_nonfinite_searches(self):
        for value in ("-180,-90,180,90", "NaN,1,2,3", "-123,46,-122,45", "bad"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                Dashboard.bbox(value)

    def test_project_resolution_does_not_depend_on_working_directory(self):
        self.assertEqual(project_directory(PROJECT), PROJECT)


class HTTPTests(unittest.TestCase):
    def test_loopback_api_security_static_assets_and_file_allowlist(self):
        with dashboard() as app, DashboardServer(("127.0.0.1", 0), app) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
            try:
                def request(path, method="GET", body=None, headers=None):
                    connection.request(method, path, body, headers or {})
                    response = connection.getresponse()
                    data = response.read()
                    return response.status, data, response.headers
                for asset in ("/", "/app.js", "/style.css"):
                    status, data, headers = request(asset)
                    self.assertEqual(status, 200)
                    self.assertTrue(data)
                    self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
                self.assertEqual(request("/api/bootstrap", headers={"Host": "evil.example"})[0], 403)
                self.assertEqual(request("/api/wells", "POST", '{}')[0], 403)
                valid = {"Content-Type": "application/json", "X-Dashboard-Token": app.token}
                evil = {**valid, "Origin": "https://evil.example"}
                self.assertEqual(request("/api/wells", "POST", '{}', evil)[0], 403)
                self.assertEqual(request("/api/wells", "POST", '[]', valid)[0], 400)
                status, raw, _ = request("/api/bootstrap")
                bootstrap = json.loads(raw)
                self.assertEqual(status, 200)
                row = next(r for r in bootstrap["results"] if r["status"] == "complete")
                self.assertEqual(request(f"/files/{row['id']}/forecast.csv")[0], 200)
                self.assertEqual(request(f"/files/{row['id']}/../../.env")[0], 404)
                self.assertEqual(request("/study/%2e%2e/pyproject.toml")[0], 404)
                self.assertEqual(request("/study/index.html")[0], 200)
                self.assertEqual(request("/api/results/missing")[0], 404)
            finally:
                connection.close()
                server.shutdown()
                thread.join()
