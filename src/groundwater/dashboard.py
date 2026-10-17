"""Loopback-only, dependency-free UI over the existing scientific pipeline."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import math
import os
from pathlib import Path
import re
import secrets
import threading
from urllib.parse import unquote, urlsplit
import uuid
import webbrowser

import pandas as pd

from groundwater.config import Config
from groundwater.forecast import readiness_issues, required_calendar_days
from groundwater.pipeline import acquire, run, write_json
from groundwater.quality import daily_series, normalize
from groundwater.usgs import USGSClient, utc_now

LOG = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "web"
MODEL_NAMES = {"persistence": "Last observed level", "seasonal_365": "Same time last year",
               "ridge_lags": "Regularized trend model", "random_forest_lags": "Random forest"}
EXAMPLES = {
    "oregon_recent": {"name": "Klamath County, Oregon · 2023–2025",
        "site": "USGS-420318121312201", "start": "2023-01-01", "end": "2025-12-31",
        "file": "oregon_daily_2023_2025.geojson", "series_id": "d750bc81e1d6455190d36fab703385a1",
        "coordinates": [-121.523861111111, 42.0548333333333]},
    "oregon_earlier": {"name": "Klamath County, Oregon · 2018–2020",
        "site": "USGS-420318121312201", "start": "2018-01-01", "end": "2020-12-31",
        "file": "oregon_daily_2018_2020.geojson", "series_id": "d750bc81e1d6455190d36fab703385a1",
        "coordinates": [-121.523861111111, 42.0548333333333]},
    "reference": {"name": "Maine reference well · 2023–2024",
        "site": "USGS-444302070252401", "start": "2023-01-01", "end": "2024-12-31",
        "file": "usgs_daily_2023_2024.geojson", "series_id": None,
        "coordinates": [-70.423333, 44.717222]},
}
REGIONS = [
    {"name": "Portland, Oregon", "lat": 45.52, "lon": -122.68},
    {"name": "Beaverton, Oregon", "lat": 45.49, "lon": -122.80},
    {"name": "Klamath Falls, Oregon", "lat": 42.22, "lon": -121.78},
    {"name": "Bend, Oregon", "lat": 44.06, "lon": -121.32},
    {"name": "Corvallis, Oregon", "lat": 44.56, "lon": -123.26},
    {"name": "Seattle, Washington", "lat": 47.61, "lon": -122.33},
    {"name": "Los Angeles, California", "lat": 34.05, "lon": -118.24},
    {"name": "San Diego, California", "lat": 32.72, "lon": -117.16},
    {"name": "Phoenix, Arizona", "lat": 33.45, "lon": -112.07},
    {"name": "Denver, Colorado", "lat": 39.74, "lon": -104.99},
    {"name": "Dallas, Texas", "lat": 32.78, "lon": -96.80},
    {"name": "Chicago, Illinois", "lat": 41.88, "lon": -87.63},
    {"name": "New York, New York", "lat": 40.71, "lon": -74.01},
    {"name": "Miami, Florida", "lat": 25.76, "lon": -80.19},
    {"name": "Augusta, Maine", "lat": 44.31, "lon": -69.78},
]
ARTIFACTS = {"report.html", "forecast.csv", "observations.csv", "leaderboard.csv", "folds.csv",
             "holdout.json", "holdout_predictions.csv", "quality.json", "quarantine.json",
             "provenance.json", "config.json", "anomalies.csv", "validation_predictions.csv",
             "errors_by_lead_day.csv", "errors_by_season.csv", "errors_by_year.csv"}


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(".tmp")
    write_json(temporary, value)
    temporary.replace(path)


def project_directory(path=None):
    candidates = [Path(path)] if path else [Path.cwd(), Path(__file__).resolve().parents[2]]
    for candidate in candidates:
        if (candidate / "examples").is_dir() and (candidate / "sample_study").is_dir():
            return candidate.resolve()
    raise ValueError("Open the extracted Groundwater project folder first, or use --project PATH")


def bounded_int(value, label, low, high):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f"{label} must be a whole number from {low} to {high}")
    return value


def analysis_config(payload):
    source = payload.get("source", "")
    example = EXAMPLES.get(source)
    if not example and source != "live":
        raise ValueError("Choose a bundled example or a live USGS well")
    site = example["site"] if example else str(payload.get("site", "")).strip()
    if not re.fullmatch(r"USGS-\d{8,15}", site):
        raise ValueError("Enter the full well identifier, for example USGS-420318121312201")
    start, end = str(payload.get("start", "")), str(payload.get("end", ""))
    try:
        first, last = date.fromisoformat(start), date.fromisoformat(end)
    except ValueError as exc:
        raise ValueError("Choose valid start and end dates") from exc
    if last < first or (last - first).days > 15 * 366:
        raise ValueError("Choose an ordered date range of at most 15 years")
    if last > date.today():
        raise ValueError("The observation end date cannot be in the future")
    if example and (start < example["start"] or end > example["end"]):
        raise ValueError(f"This snapshot covers {example['start']} through {example['end']}. "
                         "Choose dates inside that period, or switch to live data.")
    series = example["series_id"] if example else payload.get("series_id") or None
    if series is not None and (not isinstance(series, str) or len(series) > 120):
        raise ValueError("The sensor series identifier must be text of at most 120 characters")
    approved = payload.get("approved_only", False)
    if not isinstance(approved, bool):
        raise ValueError("Approved observations only must be true or false")
    horizon = bounded_int(payload.get("horizon", 14), "Forecast length", 1, 90)
    folds = bounded_int(payload.get("folds", 4), "Validation windows", 2, 24)
    stride = payload.get("stride")
    if stride is not None:
        bounded_int(stride, "Window spacing", horizon, 366)
    return Config(site=site, start=start, end=end, horizon=horizon, folds=folds,
                  validation_stride_days=stride, series_id=series, approved_only=approved)


def explain_error(error):
    message = str(error)
    if "USGS" in message and any(s in message for s in ("HTTP", "connection", "Pagination")):
        return ("USGS could not finish the download. Check your connection and try again. "
                "If the service is busy, wait a minute or open the bundled example offline.")
    if "Multiple time series" in message:
        return "This well has more than one sensor series. Select a discovered series or enter its series ID."
    if "No valid" in message:
        return ("No usable daily depth observations were found for these dates and settings. "
                "Try a different period or well; if approved-only is selected, try including provisional data.")
    if "finite 365-day" in message:
        return ("A model training window has a gap that cannot be filled within the three-day limit. "
                "Choose a denser period or another well.")
    if "Permission" in message or "Read-only" in message:
        return "The program cannot save here. Restart with --root pointing to a writable folder."
    return message


def readiness(frame, ingest_quality, config):
    if frame.empty:
        return {"ready": False, "quality": ingest_quality, "blockers": [explain_error("No valid")],
                "warnings": [], "required_days": required_calendar_days(config), "points": []}
    series, quality, _ = daily_series(frame, config)
    quality = {**ingest_quality, **quality}
    blockers = []
    if quality["longest_gap_days"] > config.max_gap_days:
        blockers.append(f"This well has a {quality['longest_gap_days']}-day gap. The current "
                        f"forecasting method supports gaps up to {config.max_gap_days} days. "
                        "Choose another period or another well.")
    if quality["coverage_within_observed_span"] < config.min_coverage:
        blockers.append(f"Only {quality['coverage_within_observed_span']:.1%} of days have observations; "
                        f"at least {config.min_coverage:.0%} is required. Choose a denser period or well.")
    if not blockers:
        blockers.extend(explain_error(issue) for issue in readiness_issues(series, config))
    warnings = []
    if quality["age_days_at_run"] > 30:
        warnings.append(f"The latest observation is {quality['last_observed']} "
                        f"({quality['age_days_at_run']} days old). Forecasts begin after that date, not today.")
    if quality["missing_requested_leading_days"] or quality["missing_requested_trailing_days"]:
        warnings.append(f"The observed period is {quality['first_observed']} to {quality['last_observed']}; "
                        "it does not cover the entire requested date range.")
    if quality["quarantined_records"]:
        warnings.append(f"{quality['quarantined_records']} records failed the data contract and were excluded.")
    if quality["provisional_or_other_records"]:
        warnings.append(f"{quality['provisional_or_other_records']} observations are provisional or unapproved.")
    return {"ready": not blockers, "quality": quality, "blockers": blockers, "warnings": warnings,
            "required_days": required_calendar_days(config),
            "points": [{"date": str(day.date()), "value": None if pd.isna(value) else float(value)}
                       for day, value in series.items()]}


class Dashboard:
    def __init__(self, project, root):
        self.project = Path(project).resolve()
        self.root = Path(root).resolve()
        self.state = self.root / "dashboard"
        (self.state / "jobs").mkdir(parents=True, exist_ok=True)
        (self.state / "checks").mkdir(exist_ok=True)
        self._root_lock = (self.state / "server.lock").open("a+b")
        try:
            if os.name == "nt":
                import msvcrt
                if self._root_lock.tell() == 0:
                    self._root_lock.write(b"0")
                    self._root_lock.flush()
                self._root_lock.seek(0)
                msvcrt.locking(self._root_lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._root_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self._root_lock.close()
            raise ValueError("Another dashboard is using this results folder. Open its existing "
                             "browser window, or use a different --root folder.") from exc
        self.token = secrets.token_urlsafe(32)
        self.lock = threading.RLock()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="groundwater")
        self.jobs = {}
        for path in (self.state / "jobs").glob("*.json"):
            try:
                job = read_json(path)
                if job["status"] in ("queued", "running"):
                    job.update(status="interrupted", stage="interrupted", finished_at=utc_now(),
                               message="The program stopped before this task finished. Retry to start again.")
                    atomic_json(path, job)
                self.jobs[job["id"]] = job
            except (ValueError, KeyError, OSError):
                LOG.warning("Could not read saved task %s", path.name)

    def close(self):
        try:
            self.executor.shutdown(wait=True)
        finally:
            self._root_lock.close()

    def _update(self, job_id, **fields):
        with self.lock:
            self.jobs[job_id].update(fields)
            atomic_json(self.state / "jobs" / f"{job_id}.json", self.jobs[job_id])

    def job(self, job_id):
        with self.lock:
            if job_id not in self.jobs:
                raise KeyError("Task not found")
            return json.loads(json.dumps(self.jobs[job_id]))

    def submit(self, kind, payload):
        if kind == "check":
            analysis_config(payload)
        elif kind == "analyze":
            checked = self.job(str(payload.get("check_id", "")))
            if checked["kind"] != "check" or checked["status"] != "complete" or not checked["result"]["ready"]:
                raise ValueError("Check the data and resolve readiness issues before starting an analysis")
        elif kind == "discover":
            self.bbox(payload.get("bbox", ""))
        else:
            raise ValueError("Unknown task")
        with self.lock:
            if sum(j["status"] in ("queued", "running") for j in self.jobs.values()) >= 12:
                raise ValueError("Several tasks are already waiting. Let them finish before starting another.")
            job_id = uuid.uuid4().hex
            self.jobs[job_id] = {"id": job_id, "kind": kind, "payload": payload,
                "status": "queued", "stage": "queued", "message": "Waiting for the current task",
                "created_at": utc_now()}
            self._update(job_id)
            self.executor.submit(self._work, job_id)
        return self.job(job_id)

    @staticmethod
    def bbox(value):
        try:
            west, south, east, north = map(float, str(value).split(","))
            if not all(math.isfinite(v) for v in (west, south, east, north)):
                raise ValueError()
            if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
                raise ValueError()
            if east - west > 5 or north - south > 5:
                raise ValueError()
        except ValueError as exc:
            raise ValueError("Search a box up to 5° wide and high, ordered west,south,east,north") from exc
        return ",".join(map(str, (west, south, east, north)))

    def _work(self, job_id):
        job = self.job(job_id)
        self._update(job_id, status="running", started_at=utc_now())
        try:
            if job["kind"] == "discover":
                self._update(job_id, stage="downloading", message="Finding wells with daily depth observations")
                features, _ = USGSClient().discover(self.bbox(job["payload"]["bbox"]),
                                                    self.root / "bronze" / "discovery")
                candidates = {}
                for feature in features:
                    p = feature.get("properties", {})
                    site = p.get("monitoring_location_id", "")
                    series = p.get("time_series_id") or p.get("timeseries_id")
                    if not re.fullmatch(r"USGS-\d{8,15}", site) or p.get("unit_of_measure") != "ft":
                        continue
                    candidates[(site, str(series))] = {"site": site, "series_id": series,
                        "name": p.get("monitoring_location_name") or site,
                        "latest_date": p.get("time"),
                        "coordinates": (feature.get("geometry") or {}).get("coordinates")}
                result = {"candidates": list(candidates.values())}
            elif job["kind"] == "check":
                result = self._check(job_id, job["payload"])
            else:
                checked = self.job(job["payload"]["check_id"])
                metadata = read_json(self.state / "checks" / checked["id"] / "metadata.json")
                raw = (self.state / "checks" / checked["id"] / "snapshot.geojson").read_bytes()
                if hashlib.sha256(raw).hexdigest() != metadata["sha256"]:
                    raise ValueError("The checked snapshot changed. Check the data again before running.")
                config = Config(**metadata["config"])
                output = run(config, self.root, metadata["mode"],
                    prepared=(json.loads(raw)["features"], metadata["pages"]),
                    progress=lambda stage, message: self._update(job_id, stage=stage, message=message))
                provenance = read_json(output / "provenance.json")
                provenance["dashboard_check"] = {"id": checked["id"],
                    "snapshot_sha256": metadata["sha256"], "checked_at": metadata["checked_at"]}
                atomic_json(output / "provenance.json", provenance)
                result = {"result_id": self.result_id(output), "site": config.site}
            self._update(job_id, status="complete", stage="complete", message="Complete",
                         result=result, finished_at=utc_now())
        except Exception as exc:
            LOG.exception("Dashboard task %s failed", job_id)
            self._update(job_id, status="failed", stage="failed", message=explain_error(exc),
                         technical_detail=str(exc), finished_at=utc_now())

    def _check(self, job_id, payload):
        config = analysis_config(payload)
        example = EXAMPLES.get(payload["source"])
        mode = "replay" if example else "live"
        self._update(job_id, stage="downloading", message=("Opening bundled USGS data" if example
                                                          else "Downloading daily observations from USGS"))
        features, pages = acquire(config, self.root, mode,
                                 self.project / "examples" / example["file"] if example else None)
        # A bundled file can be wider than the guided date window; retain its archived source intact.
        if example:
            features = [f for f in features if config.start <= str(
                f.get("properties", {}).get("time", "")) <= config.end]
        self._update(job_id, stage="checking", message="Checking coverage, gaps, and all model windows")
        frame, _, quality = normalize(features, config)
        result = readiness(frame, quality, config)
        result.update(config=config.as_dict(), source=payload["source"], mode=mode)
        directory = self.state / "checks" / job_id
        directory.mkdir()
        raw = json.dumps({"type": "FeatureCollection", "features": features}).encode("utf-8")
        (directory / "snapshot.geojson").write_bytes(raw)
        atomic_json(directory / "metadata.json", {"config": config.as_dict(), "mode": mode,
            "pages": pages, "checked_at": utc_now(), "sha256": hashlib.sha256(raw).hexdigest()})
        return result

    def wells(self):
        path = self.state / "wells.json"
        return read_json(path) if path.exists() else []

    def save_well(self, payload):
        site = str(payload.get("site", "")).strip()
        if not re.fullmatch(r"USGS-\d{8,15}", site):
            raise ValueError("Enter a valid USGS monitoring location identifier")
        series = payload.get("series_id") or None
        if series is not None and (not isinstance(series, str) or len(series) > 120):
            raise ValueError("Invalid sensor series ID")
        name = str(payload.get("name") or site).strip()[:150]
        with self.lock:
            wells = [w for w in self.wells() if (w["site"], w.get("series_id")) != (site, series)]
            wells.append({"site": site, "series_id": series, "name": name})
            atomic_json(self.state / "wells.json", wells[-100:])
        return {"saved": True}

    @staticmethod
    def result_id(directory):
        return hashlib.sha256(str(directory.resolve()).encode()).hexdigest()[:24]

    def catalog(self):
        directories = list((self.project / "sample_study").glob("*/runs/*/provenance.json"))
        directories += list((self.root / "runs").glob("*/provenance.json"))
        if (self.project / "sample_output" / "provenance.json").exists():
            directories.append(self.project / "sample_output" / "provenance.json")
        entries = {}
        for path in directories:
            try:
                p = read_json(path)
                config = p.get("config") or read_json(path.parent / "config.json")
                identifier = self.result_id(path.parent)
                entries[identifier] = {"id": identifier, "site": config["site"],
                    "start": config["start"], "end": config["end"], "horizon": config["horizon"],
                    "status": p["status"], "created_at": p["started_at"],
                    "model": MODEL_NAMES.get(p.get("selected_model"), p.get("selected_model")),
                    "source_mode": p["source_mode"], "error": explain_error(p.get("error", "")),
                    "bundled": path.is_relative_to(self.project / "sample_study") or
                               path.is_relative_to(self.project / "sample_output"),
                    "directory": path.parent}
            except (KeyError, ValueError, OSError):
                continue
        return entries

    def results(self):
        return sorted([{k: v for k, v in row.items() if k != "directory"}
                       for row in self.catalog().values()], key=lambda r: r["created_at"], reverse=True)

    def result_directory(self, identifier):
        entry = self.catalog().get(identifier)
        if entry is None:
            raise KeyError("Saved result not found")
        return entry["directory"]

    def result(self, identifier):
        directory = self.result_directory(identifier)
        provenance = read_json(directory / "provenance.json")
        config = read_json(directory / "config.json")
        quality = read_json(directory / "quality.json") if (directory / "quality.json").exists() else {}
        if quality.get("last_observed"):
            quality["age_days_today"] = (date.today() - date.fromisoformat(quality["last_observed"])).days
        result = {"id": identifier, "config": config, "quality": quality,
                  "provenance": provenance, "models": MODEL_NAMES,
                  "files": sorted(f.name for f in directory.iterdir() if f.name in ARTIFACTS)}
        if provenance["status"] != "complete":
            result["error"] = explain_error(provenance.get("error", "Analysis did not finish"))
            return result
        result["holdout"] = read_json(directory / "holdout.json")
        result["leaderboard"] = pd.read_csv(directory / "leaderboard.csv").to_dict("records")
        result["forecast"] = pd.read_csv(directory / "forecast.csv").to_dict("records")
        frame = pd.read_csv(directory / "observations.csv")
        series, _, _ = daily_series(frame, Config(**config))
        result["observations"] = [{"date": str(day.date()), "value": None if pd.isna(value) else float(value)}
                                  for day, value in series.items()]
        return result

    def bootstrap(self):
        examples = [{"id": key, **{k: v for k, v in row.items() if k != "file"}}
                    for key, row in EXAMPLES.items()]
        with self.lock:
            jobs = sorted(self.jobs.values(), key=lambda j: j["created_at"], reverse=True)
            recent = [{k: v for k, v in j.items() if k != "result"} for j in jobs[:30]]
        return {"token": self.token, "examples": examples, "regions": REGIONS,
                "wells": self.wells(), "jobs": recent, "results": self.results(),
                "today": date.today().isoformat()}


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False

    def __init__(self, address, app):
        self.app = app
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        LOG.debug(format, *args)

    def _send(self, status, data, mime="application/json; charset=utf-8"):
        raw = json.dumps(data, allow_nan=False).encode() if mime.startswith("application/json") else data
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; "
                         "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                         "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers()
        self.wfile.write(raw)

    def _host_allowed(self):
        port = self.server.server_port
        return self.headers.get("Host") in (f"127.0.0.1:{port}", f"localhost:{port}")

    def _dispatch(self, method):
        if not self._host_allowed():
            self._send(403, {"error": "Open the dashboard using its local browser address"})
            return
        try:
            path = unquote(urlsplit(self.path).path)
            app = self.server.app
            if method == "POST":
                origin = self.headers.get("Origin")
                if (self.headers.get("X-Dashboard-Token") != app.token or
                    (origin is not None and origin != f"http://{self.headers['Host']}")):
                    self._send(403, {"error": "Refresh the dashboard before trying again"})
                    return
                if self.headers.get_content_type() != "application/json":
                    raise ValueError("Send a JSON request")
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 16384:
                    raise ValueError("The request is empty or too large")
                payload = json.loads(self.rfile.read(size))
                if not isinstance(payload, dict):
                    raise ValueError("Expected an object")
                if path in ("/api/check", "/api/discover", "/api/analyze"):
                    self._send(202, app.submit(path.rsplit("/", 1)[1], payload))
                elif path == "/api/wells":
                    self._send(200, app.save_well(payload))
                else:
                    self._send(404, {"error": "Page not found"})
                return
            if path == "/api/bootstrap":
                self._send(200, app.bootstrap())
            elif path.startswith("/api/jobs/"):
                self._send(200, app.job(path.rsplit("/", 1)[1]))
            elif path == "/api/results":
                self._send(200, app.results())
            elif path.startswith("/api/results/"):
                self._send(200, app.result(path.rsplit("/", 1)[1]))
            elif path.startswith("/files/"):
                parts = path.split("/")
                if len(parts) != 4 or parts[3] not in ARTIFACTS:
                    raise KeyError("Export not found")
                file = app.result_directory(parts[2]) / parts[3]
                self._file(file)
            elif path.startswith("/study/"):
                base = app.project / "sample_study"
                file = (base / path.removeprefix("/study/")).resolve()
                if not file.is_relative_to(base) or file.suffix not in (".html", ".csv", ".json"):
                    raise KeyError("Study file not found")
                self._file(file)
            elif path in ("/", "/app.js", "/style.css"):
                self._file(STATIC / {"/": "index.html", "/app.js": "app.js", "/style.css": "style.css"}[path])
            else:
                self._send(404, {"error": "Page not found"})
        except KeyError as exc:
            self._send(404, {"error": str(exc).strip("'")})
        except (ValueError, TypeError) as exc:
            self._send(400, {"error": explain_error(exc)})
        except (FileNotFoundError, NotADirectoryError):
            self._send(404, {"error": "The saved file was moved or removed. Check its project folder."})
        except Exception:
            LOG.exception("Dashboard request failed")
            self._send(500, {"error": "The request could not finish. Check the terminal and retry."})

    def _file(self, path):
        mime = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                ".css": "text/css; charset=utf-8", ".csv": "text/csv; charset=utf-8",
                ".json": "application/json; charset=utf-8"}.get(path.suffix, "application/octet-stream")
        raw = path.read_bytes()
        # Keep exported JSON as file bytes, not a JSON-encoded string.
        if path.suffix == ".json":
            self._send(200, json.loads(raw), mime)
        else:
            self._send(200, raw, mime)

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")


def serve(root=Path("artifacts"), port=8765, project=None, open_browser=True):
    project = project_directory(project)
    app = Dashboard(project, root)
    try:
        with DashboardServer(("127.0.0.1", port), app) as server:
            url = f"http://127.0.0.1:{server.server_port}"
            print(f"Groundwater dashboard: {url}\nKeep this terminal open. Press Ctrl+C to stop.", flush=True)
            if open_browser:
                webbrowser.open(url)
            try:
                server.serve_forever(poll_interval=0.3)
            except KeyboardInterrupt:
                print("\nFinishing any active analysis and saving results…", flush=True)
    except OSError as exc:
        raise RuntimeError(f"Could not start the dashboard: {exc}. Try --port 8766.") from exc
    finally:
        app.close()
