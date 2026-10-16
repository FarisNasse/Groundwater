"""Audited incremental snapshots. Never treat a partial/invalid response as a withdrawal."""

from contextlib import contextmanager
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import uuid

import pandas as pd

from groundwater.pipeline import source_digest, write_json
from groundwater.quality import COLUMNS, normalize
from groundwater.usgs import USGSClient, archive, utc_now

KEYS = ("site", "series_id", "date", "parameter_code", "statistic_id")


def scope_id(config):
    # End grows as monitoring continues. Start and quality/series policy identify the dataset.
    identity = {k: getattr(config, k) for k in
                ("site", "start", "parameter_code", "statistic_id", "series_id", "approved_only")}
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


@contextmanager
def connect(database):
    Path(database).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(database, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS ingestion_events (
            event_id TEXT PRIMARY KEY, dataset_id TEXT NOT NULL, started_at TEXT NOT NULL,
            finished_at TEXT, query_start TEXT NOT NULL, query_end TEXT NOT NULL,
            mode TEXT NOT NULL, status TEXT NOT NULL, counts_json TEXT, error TEXT);
        CREATE TABLE IF NOT EXISTS watermarks (
            dataset_id TEXT PRIMARY KEY, completed_through TEXT NOT NULL,
            last_full_refresh_at TEXT NOT NULL, event_id TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS measurements (
            dataset_id TEXT NOT NULL, site TEXT NOT NULL, series_id TEXT NOT NULL,
            date TEXT NOT NULL, parameter_code TEXT NOT NULL, statistic_id TEXT NOT NULL,
            payload_json TEXT NOT NULL, last_modified TEXT NOT NULL,
            withdrawn INTEGER NOT NULL DEFAULT 0, last_seen_event TEXT NOT NULL,
            PRIMARY KEY(dataset_id,site,series_id,date,parameter_code,statistic_id));
        CREATE TABLE IF NOT EXISTS source_revisions (
            dataset_id TEXT NOT NULL, record_key TEXT NOT NULL, last_modified TEXT NOT NULL,
            payload_sha256 TEXT NOT NULL, payload_json TEXT NOT NULL,
            first_seen_at TEXT NOT NULL, event_id TEXT NOT NULL,
            PRIMARY KEY(dataset_id,record_key,last_modified,payload_sha256));
        CREATE TABLE IF NOT EXISTS withdrawals (
            event_id TEXT NOT NULL, dataset_id TEXT NOT NULL, record_key TEXT NOT NULL,
            observed_at TEXT NOT NULL, reason TEXT NOT NULL,
            PRIMARY KEY(event_id,dataset_id,record_key));
        CREATE TABLE IF NOT EXISTS well_metadata (
            site TEXT NOT NULL, payload_sha256 TEXT NOT NULL, payload_json TEXT NOT NULL,
            first_seen_at TEXT NOT NULL, event_id TEXT NOT NULL,
            PRIMARY KEY(site,payload_sha256));
    ''')
    try:
        yield conn
    finally:
        conn.close()


def plan_query(config, database, lookback_days=30, full_refresh=False, rebuild_days=30,
               backfill=None, now=None, connection=None):
    if lookback_days < 1 or rebuild_days < 1:
        raise ValueError("lookback_days and rebuild_days must be positive")
    now = now or datetime.now(timezone.utc)
    if connection is None:
        with connect(database) as conn:
            state = conn.execute("SELECT * FROM watermarks WHERE dataset_id=?",
                                 (scope_id(config),)).fetchone()
    else:
        state = connection.execute("SELECT * FROM watermarks WHERE dataset_id=?",
                                   (scope_id(config),)).fetchone()
    if backfill:
        start, end = backfill
        if not config.start <= start <= end <= config.end:
            raise ValueError("Backfill must be within the configured dataset window")
        date.fromisoformat(start)
        date.fromisoformat(end)
        return replace(config, start=start, end=end), "backfill"
    if state and config.end < state["completed_through"]:
        raise ValueError("end precedes watermark; use an explicit backfill window")
    due = state and (now - datetime.fromisoformat(state["last_full_refresh_at"])).days >= rebuild_days
    if full_refresh or not state or due:
        return config, "full_refresh"
    start = max(config.start, (date.fromisoformat(state["completed_through"])
                              - timedelta(days=lookback_days - 1)).isoformat())
    return replace(config, start=start), "incremental"


def record_key(row):
    return json.dumps([row[k] for k in KEYS], separators=(",", ":"))


def commit_snapshot(conn, config, query, frame, event_id, kind, now, metadata=None):
    """One complete request window, all mutations and watermark in one transaction."""
    dataset = scope_id(config)
    counts = dict(inserted=0, revised=0, unchanged=0, stale_revisions=0,
                  withdrawn=0, restored=0, new_revision_records=0)
    existing = {record_key(dict(r)): r for r in conn.execute(
        "SELECT * FROM measurements WHERE dataset_id=? AND date BETWEEN ? AND ?",
        (dataset, query.start, query.end))}
    incoming = {record_key(row): row for row in frame.to_dict("records")}
    # Without an explicit series selection, a new sensor series requires a new dataset.
    old_series = {r[0] for r in conn.execute(
        "SELECT DISTINCT series_id FROM measurements WHERE dataset_id=?", (dataset,))}
    new_series = set(frame.series_id)
    if len(old_series | new_series) > 1:
        raise ValueError("Series changed; select series_id explicitly and create a separate dataset")
    for key, row in incoming.items():
        payload = json.dumps(row, sort_keys=True, separators=(",", ":"), allow_nan=False)
        digest = hashlib.sha256(payload.encode()).hexdigest()
        conflicts = conn.execute('''SELECT payload_sha256 FROM source_revisions
            WHERE dataset_id=? AND record_key=? AND last_modified=?''',
            (dataset, key, row["last_modified"])).fetchall()
        if any(r[0] != digest for r in conflicts):
            raise ValueError("Conflicting payloads with identical source revision timestamp")
        cursor = conn.execute("INSERT OR IGNORE INTO source_revisions VALUES (?,?,?,?,?,?,?)",
                              (dataset, key, row["last_modified"], digest, payload, now, event_id))
        counts["new_revision_records"] += cursor.rowcount
        old = existing.get(key)
        if old is None:
            conn.execute("INSERT INTO measurements VALUES (?,?,?,?,?,?,?,?,?,?)",
                         (dataset, *[row[k] for k in KEYS], payload,
                          row["last_modified"], 0, event_id))
            counts["inserted"] += 1
        elif row["last_modified"] < old["last_modified"]:
            counts["stale_revisions"] += 1
            # A stale payload cannot restore a withdrawn record or replace a newer revision.
        else:
            counts["restored"] += int(old["withdrawn"])
            counts["unchanged" if payload == old["payload_json"] else "revised"] += 1
            conn.execute('''UPDATE measurements SET payload_json=?,last_modified=?,
                withdrawn=0,last_seen_event=? WHERE dataset_id=? AND site=? AND series_id=?
                AND date=? AND parameter_code=? AND statistic_id=?''',
                (payload, row["last_modified"], event_id, dataset, *[row[k] for k in KEYS]))
    for key, old in existing.items():
        if key in incoming or old["withdrawn"]:
            continue
        conn.execute('''UPDATE measurements SET withdrawn=1 WHERE dataset_id=? AND site=?
            AND series_id=? AND date=? AND parameter_code=? AND statistic_id=?''',
            (dataset, *[old[k] for k in KEYS]))
        conn.execute("INSERT INTO withdrawals VALUES (?,?,?,?,?)",
                     (event_id, dataset, key, now, "absent_from_complete_validated_response"))
        counts["withdrawn"] += 1
    if metadata is not None:
        payload = json.dumps(metadata, sort_keys=True, separators=(",", ":"))
        conn.execute("INSERT OR IGNORE INTO well_metadata VALUES (?,?,?,?,?)",
                     (config.site, hashlib.sha256(payload.encode()).hexdigest(), payload, now, event_id))
    if kind != "backfill":
        previous = conn.execute("SELECT last_full_refresh_at FROM watermarks WHERE dataset_id=?",
                                (dataset,)).fetchone()
        full_at = now if kind == "full_refresh" else previous[0]
        conn.execute('''INSERT INTO watermarks VALUES (?,?,?,?) ON CONFLICT(dataset_id)
            DO UPDATE SET completed_through=excluded.completed_through,
            last_full_refresh_at=excluded.last_full_refresh_at,event_id=excluded.event_id''',
            (dataset, query.end, full_at, event_id))
    return counts


def active_snapshot(conn, config):
    rows = conn.execute("SELECT payload_json FROM measurements WHERE dataset_id=? AND withdrawn=0"
                        " AND date BETWEEN ? AND ? ORDER BY date",
                        (scope_id(config), config.start, config.end)).fetchall()
    return pd.DataFrame([json.loads(r[0]) for r in rows], columns=COLUMNS)


def ingest(config, root, lookback_days=30, full_refresh=False, rebuild_days=30,
           replay_file=None, backfill=None, client=None):
    """Ingestion and forecasting are separate; no model trains on a partial update window."""
    root = Path(root).resolve()
    mode = "replay" if replay_file else "live"
    database = root / "silver" / f"{mode}_ingestion.sqlite"
    event_id = utc_now().replace(":", "-") + "-" + uuid.uuid4().hex[:8]
    directory = root / "ingestions" / event_id
    directory.mkdir(parents=True)
    started = utc_now()
    manifest = {"event_id": event_id, "dataset_id": scope_id(config), "status": "started",
                "source_mode": mode, "config": config.as_dict(),
                "started_at": started, "pages": [],
                "code_sha256": source_digest(), "lookback_days": lookback_days,
                "rebuild_days": rebuild_days}
    with connect(database) as conn:
        conn.execute("BEGIN IMMEDIATE")
        query, kind = plan_query(config, database, lookback_days, full_refresh,
                                 rebuild_days, backfill, connection=conn)
        expected_watermark = conn.execute("SELECT event_id FROM watermarks WHERE dataset_id=?",
                                           (scope_id(config),)).fetchone()
        expected_watermark = expected_watermark[0] if expected_watermark else None
        manifest.update(query=query.as_dict(), query_kind=kind)
        conn.execute("INSERT INTO ingestion_events VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (event_id, scope_id(config), started, None, query.start, query.end,
                      kind, "started", None, None))
        conn.commit()
        try:
            metadata = None
            if replay_file:
                raw = Path(replay_file).read_bytes()
                page = json.loads(raw)
                if (page.get("type") != "FeatureCollection"
                        or not isinstance(page.get("features"), list)
                        or any(x.get("rel") == "next" for x in page.get("links", []))):
                    raise ValueError("Replay ingestion requires a complete GeoJSON FeatureCollection")
                values = page["features"]
                manifest["pages"] = [{**archive(raw, root / "bronze" / "ingest_replay"),
                                       "input_file": str(Path(replay_file).resolve())}]
            else:
                client = client or USGSClient()
                values, manifest["pages"] = client.daily(query, root / "bronze" / "ingest_live")
                if kind == "full_refresh":
                    metadata, page = client.location(config.site, root / "bronze" / "metadata")
                    manifest["metadata_page"] = page
            frame, rejected, quality = normalize(values, query)
            write_json(directory / "quarantine.json", rejected)
            write_json(directory / "quality.json", quality)
            # Withdrawals are inferred only from complete, valid, unfiltered source snapshots.
            # Approved-only absence is policy filtering, not proof of a source withdrawal.
            if rejected:
                raise ValueError("Quarantined response: no ledger changes or watermark advancement")
            # BEGIN IMMEDIATE prevents two writers from committing against the same watermark.
            conn.execute("BEGIN IMMEDIATE")
            current_watermark = conn.execute("SELECT event_id FROM watermarks WHERE dataset_id=?",
                                              (scope_id(config),)).fetchone()
            current_watermark = current_watermark[0] if current_watermark else None
            # Also reject a concurrent backfill, which deliberately leaves the watermark alone.
            concurrent = conn.execute("SELECT 1 FROM ingestion_events WHERE dataset_id=?"
                " AND status='complete' AND finished_at>? LIMIT 1",
                (scope_id(config), started)).fetchone()
            if current_watermark != expected_watermark or concurrent:
                raise ValueError("Watermark changed during fetch; retry ingestion")
            counts = commit_snapshot(conn, config, query, frame, event_id, kind, utc_now(), metadata)
            snapshot = active_snapshot(conn, config)
            snapshot.to_csv(directory / "observations.csv", index=False)
            manifest["snapshot_records"] = len(snapshot)
            manifest["observations_sha256"] = hashlib.sha256(
                (directory / "observations.csv").read_bytes()).hexdigest()
            if not snapshot.empty:
                latest = date.fromisoformat(snapshot.date.max())
                manifest["freshness_days"] = (datetime.now(timezone.utc).date() - latest).days
                manifest["missing_requested_days"] = ((date.fromisoformat(config.end)
                    - date.fromisoformat(config.start)).days + 1 - len(snapshot))
                # First seen here is ingestion availability, not the source publication time.
                manifest["newest_observation_age_at_ingestion_days"] = manifest["freshness_days"]
            manifest.update(status="complete", counts=counts, quality=quality, finished_at=utc_now())
            conn.execute("UPDATE ingestion_events SET status=?,finished_at=?,counts_json=?"
                         " WHERE event_id=?", ("complete", manifest["finished_at"],
                                               json.dumps(counts), event_id))
            conn.commit()
        except Exception as exc:
            conn.rollback()
            manifest.update(status="failed", error=str(exc), finished_at=utc_now())
            conn.execute("UPDATE ingestion_events SET status=?,finished_at=?,error=? WHERE event_id=?",
                         ("failed", manifest["finished_at"], str(exc), event_id))
            conn.commit()
            raise
        finally:
            write_json(directory / "manifest.json", manifest)
    return directory
