# Local dashboard · v0.3

The dashboard wraps the existing USGS ingestion, data contract, causal anomaly detector,
rolling validation, separate final holdout, and report writer. It adds no runtime dependencies.
The server uses Python's standard library and binds only to `127.0.0.1`.

## Start

Windows: double-click `Start Dashboard.cmd`. macOS/Linux: `python3 start_dashboard.py`.
The launcher uses `.venv`, installs dependencies only when needed, and opens the dashboard.
It does not activate an environment or read `.env` files. The terminal must remain open.
Python 3.11+ is required, with 3.12 recommended. First-time installation needs internet.

If already installed:

```bash
python -m groundwater dashboard
python -m groundwater dashboard --port 8766 --no-browser
python -m groundwater dashboard --project /path/to/Groundwater-main --root /path/to/results
```

`--project` identifies the extracted project containing examples and the sample study.
By default the application finds the current project or the editable source checkout.
A wheel installed without those project files needs `--project` pointing to the extracted build.
`--root` defaults to `artifacts` relative to the current working directory. The launcher always
uses the extracted project's `artifacts` folder. Reuse the same root to see saved analyses.

## User workflow

| Screen | Behavior |
| --- | --- |
| Overview | Open the precomputed Oregon result, start a guided analysis, or browse saved results. |
| Analyze a well | Choose a bundled historical snapshot or live USGS data; set dates and forecast length. |
| Well selection | Search listed city presets, click a schematic coordinate map, enter coordinates or geographic bounds, select a returned sensor series, use a saved well, or enter an exact USGS identifier. |
| Check data | Acquire and archive observations, normalize them, check coverage/gaps and all training/evaluation prerequisites; show data age and blockers. |
| Run analysis | Reuse the exact checked snapshot, revalidate it, compare the original four models, score the final holdout, refit, and save all exports. |
| Results | Explain the chosen model, distinguish validation and final test errors, show the forecast dates and data age, and link to technical tables and exports. |
| Saved results | Reopen completed or failed runs without fitting models. Filter by well, dates, status, or model. Revisit recent tasks and retry failures. |

Location search uses explicit city presets, not arbitrary address geocoding. The local map is
an approximate contiguous-U.S. coordinate selector, not a street or parcel basemap. Live
search queries a small USGS bounding box and plots returned wells. Exact coordinate inputs
also support Alaska and Hawaii. A candidate's latest observation does not establish historical
readiness; the next screen checks the requested data. A search returns separate sensor series.

Dates can span up to 15 years. Forecast lengths range from 1 to 90 days through the API (the
form offers 7, 14, 30, 60, and 90 days). There are 2–24 validation windows; advanced controls
allow nonoverlapping spacing up to 366 days and approved-only filtering. Other scientific
settings retain the existing defaults. There is no model tuning from the holdout.

The minimum calendar history is:

```text
min_train_days + 2 × horizon + (folds − 1) × validation_stride
```

The default stride equals the horizon. Readiness also checks causal filling of each 365-day
training tail, at least 100 measured training targets after lag construction, and at least one
observed target in every evaluation window. It never fits or scores a forecasting model.
Existing quality gates remain at 95% coverage and a maximum internal gap of three days.
Missing targets remain missing; no two-sided interpolation is introduced.

Bundled snapshots can be sliced to a shorter date window. Original source bytes remain
archived; the selected snapshot is preserved separately. Historical dates, requested-window
shortfalls, unapproved observations, and quarantined records are visible before training.
Data readiness is a check of these methods' prerequisites, not a claim of hydrological validity.

## Saved state and reproducibility

```text
artifacts/
  dashboard/
    jobs/<task-id>.json                status, timestamps, payload, messages, check summary
    checks/<task-id>/snapshot.geojson  immutable selected observations
    checks/<task-id>/metadata.json     config, source mode, source pages, SHA-256, check time
    wells.json                        saved site + sensor series choices
  runs/<pipeline-run-id>/              original report, forecast, quality and provenance exports
  bronze/                             original immutable acquisition pages
  silver/                             original revision-aware SQLite stores
```

There is one worker per dashboard process; task submission is bounded to 12 active/queued
jobs. Tasks run independently of the browser tab. The browser polls stages (opening/downloading,
checking, comparing, saving); comparing models is a coarse stage, not a fabricated percentage.
An operating-system file lock prevents two dashboard instances from writing the same artifact
root. It releases when the process exits, including after a crash.

Checking records does not change the silver store. Analysis verifies the checked snapshot's
hash and reuses the archived pages, keeps the original `live` or `replay` source label, and
normalizes the snapshot again. Successful pipeline provenance adds `checked_snapshot_reused`
and a `dashboard_check` containing the check ID, input SHA-256, and check timestamp.
The standard frozen config, observations hash, source pages, code hash, and package versions
remain. No source file or model score is silently substituted.

Jobs and saved wells use atomic JSON replacement. Unfinished tasks are marked `interrupted`
on restart and offer retry; there is no automatic resumption or silent retraining. A completed
pipeline run remains discoverable even if its final task notification was interrupted.

The catalog includes bundled `sample_study` / `sample_output` results and analysis runs directly
under the selected root's `runs` directory, including CLI-created runs there. It does not scan
arbitrary folders or every previous `study --root` tree. Keep `artifacts` when replacing source.
Saved results need their original CSV/JSON files. Moving/removing them produces an explanation.

## Local API and access

GET endpoints: `/api/bootstrap`, `/api/jobs/<id>`, `/api/results`, `/api/results/<id>`.
POST endpoints: `/api/discover`, `/api/check`, `/api/analyze`, `/api/wells`.

The browser receives a random per-server token through the local bootstrap response. Mutating
requests require that token in `X-Dashboard-Token`, JSON content, a bounded body, and an allowed
Origin when one is supplied. Host headers must match the bound loopback address or localhost
and port. Static assets and exports have explicit allowlists; study paths are contained inside
the bundled study folder. There are no cloud credentials, third-party browser scripts, map tile
requests, or externally loaded fonts. USGS requests still use the existing Python client,
with its pagination, timeout, retry, trusted-host, and source archive behavior.

This is a local research tool. There is no network bind option, user authentication, hosted
multi-user operation, scheduler, cancellation endpoint, calibrated prediction intervals, sparse
well modeling, or regional aggregation. API keys remain optional environment variables and
are never part of saved config.

## Verification

```bash
python -m ruff check .
node --check src/groundwater/web/app.js
python -m unittest discover -s tests -v
```

Optional browser checks (development only):

```bash
python -m pip install playwright
python -m playwright install chromium
python tests/browser_smoke.py
```

The Python suite includes offline checks for prerequisites, an actionable 92-day gap message,
short history, an entirely missing evaluation target window, checked-snapshot reuse and hash
tampering, source label preservation, read-only result loading, saved-state restart, interrupted
tasks, discovery series selection, network failures, and HTTP token/Origin/Host/path controls.
A Chromium browser walkthrough covers the overview, bundled result, readiness check, complete
analysis, reopening and filtering saved results, blocked analysis, city selection, saved wells,
mocked discovery failure/retry, map-based well selection, and a 390-pixel mobile viewport.
The original pipeline, ingestion, and study tests remain.

This build passed all 47 Python tests, Ruff, JavaScript syntax checking, the Chromium walkthrough,
and a wheel build containing the browser assets. The patch was checked against the uploaded baseline.

Live USGS discovery/downloads are tested with mocked responses. Actual live calls and the
Windows `.cmd` launcher have not been executed in this Linux environment. The browser and
complete offline analysis are exercised against a real local HTTP server.
