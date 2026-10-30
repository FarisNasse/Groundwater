from dataclasses import replace
import hashlib
from importlib.metadata import version
import json
import logging
from pathlib import Path
import platform
import uuid

from groundwater.anomalies import detect_anomalies, summarize_anomaly_episodes
from groundwater.demo import synthetic_features
from groundwater.forecast import evaluate, hydrologic_summary
from groundwater.quality import daily_series, normalize
from groundwater.report import write_report
from groundwater.storage import upsert
from groundwater.usgs import USGSClient, archive, utc_now

LOG = logging.getLogger(__name__)


def write_json(path, payload):
    path.write_text(json.dumps(payload, indent=2, allow_nan=False), encoding="utf-8")


def source_digest():
    digest = hashlib.sha256()
    for file in sorted(Path(__file__).parent.glob("*.py")):
        digest.update(file.name.encode())
        digest.update(file.read_bytes())
    return digest.hexdigest()


def acquire(config, root, mode="live", replay_file=None):
    """Acquire a complete, archived input without training or changing silver storage."""
    raw_dir = Path(root) / "bronze" / mode
    if mode == "live":
        return USGSClient().daily(config, raw_dir)
    if mode == "replay":
        raw = Path(replay_file).read_bytes()
        page = json.loads(raw)
        if page.get("type") != "FeatureCollection" or not isinstance(page.get("features"), list):
            raise ValueError("Replay requires a GeoJSON FeatureCollection")
        if any(x.get("rel") == "next" for x in page.get("links", [])):
            raise ValueError("Replay file is incomplete (has next page); ingest the full query live")
        pages = [{**archive(raw, raw_dir), "input_file": str(Path(replay_file).resolve()),
                  "note": "Replayed input; verify original retrieval provenance"}]
        return page["features"], pages
    if mode == "synthetic":
        measurements = synthetic_features(config)
        raw = json.dumps({"type": "FeatureCollection", "features": measurements}).encode()
        return measurements, [{**archive(raw, raw_dir), "note": "Generated fixture; NOT USGS data"}]
    raise ValueError("Unknown pipeline mode")


def run(config, root, mode="live", replay_file=None, *, prepared=None, progress=None):
    if mode not in ("live", "replay", "synthetic"):
        raise ValueError("Unknown pipeline mode")
    if mode == "synthetic":
        config = replace(config, site="USGS-SYNTHETIC", approved_only=False)
    root = Path(root).resolve()
    run_id = utc_now().replace(":", "-") + "-" + uuid.uuid4().hex[:8]
    directory = root / "runs" / run_id
    directory.mkdir(parents=True)
    def notify(stage, message):
        if progress is not None:
            progress(stage, message)

    manifest = {"run_id": run_id, "started_at": utc_now(), "source_mode": mode,
                "config": config.as_dict(), "status": "started", "pages": [],
                "code_sha256": source_digest(),
                "environment": {"python": platform.python_version(),
                                **{p: version(p) for p in ("numpy", "pandas", "scikit-learn")}}}
    write_json(directory / "config.json", config.as_dict())
    try:
        LOG.info("run=%s mode=%s site=%s", run_id, mode, config.site)
        notify("downloading", "Opening the checked data snapshot" if prepared is not None
               else "Downloading USGS observations" if mode == "live"
               else "Opening the bundled observations")
        measurements, manifest["pages"] = (prepared if prepared is not None
                                           else acquire(config, root, mode, replay_file))
        if prepared is not None:
            manifest["checked_snapshot_reused"] = True
        notify("checking", "Checking observations and scientific quality gates")
        frame, rejected, ingest_quality = normalize(measurements, config)
        write_json(directory / "quarantine.json", rejected)
        write_json(directory / "quality.json", ingest_quality)
        if frame.empty:
            raise ValueError("No valid measurements; inspect quarantine.json")
        frame.to_csv(directory / "observations.csv", index=False)
        manifest["observations_sha256"] = hashlib.sha256(
            (directory / "observations.csv").read_bytes()).hexdigest()
        upsert(frame, root / "silver" / f"{mode}.sqlite")
        series, series_quality, issues = daily_series(frame, config)
        LOG.info("validated=%s quarantined=%s coverage=%.3f", len(frame), len(rejected),
                 series_quality["coverage_within_observed_span"])
        anomalies = detect_anomalies(series, config.anomaly_window, config.anomaly_threshold,
                                     config.anomaly_scale_floor_ft)
        anomalies.to_csv(directory / "anomalies.csv")
        episodes = summarize_anomaly_episodes(anomalies)
        quality = {**ingest_quality, **series_quality, "forecast_blockers": issues,
                   "anomaly_episodes": episodes, "hydrology": hydrologic_summary(series)}
        write_json(directory / "quality.json", quality)
        if issues:
            raise ValueError("Forecast blocked: " + "; ".join(issues))
        notify("comparing", "Comparing four models with rolling validation and a final holdout")
        result = evaluate(series, config)
        notify("saving", "Saving the forecast, report, and reproducible data exports")
        for name in ("leaderboard", "folds", "validation_predictions",
                     "errors_by_lead_day", "errors_by_season", "errors_by_year"):
            result[name].to_csv(directory / f"{name}.csv", index=False)
        for name in ("holdout_predictions", "forecast"):
            result[name].to_csv(directory / f"{name}.csv")
        write_json(directory / "holdout.json", result["holdout"])
        result["series"] = series
        write_report(directory / "report.html", config, mode, quality, anomalies, result)
        manifest.update(status="complete", selected_model=result["holdout"]["selected_model"],
                        holdout_mae_ft=result["holdout"]["mae_ft"])
        LOG.info("selected=%s holdout_mae_ft=%.4f", manifest["selected_model"],
                 manifest["holdout_mae_ft"])
    except Exception as exc:
        manifest.update(status="failed", error=str(exc))
        raise
    finally:
        manifest["finished_at"] = utc_now()
        write_json(directory / "provenance.json", manifest)
    return directory
