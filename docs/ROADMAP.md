# Build-out roadmap

**v0.2 progress:** the first milestone now has a reproducible two-well case study; much of
the local ingestion milestone is implemented, and saved experiments have a read-only catalog.
See [RUSH_REVIEW.md](RUSH_REVIEW.md) for delivered work, measured evidence, and remaining items.
The sections below retain the long-term goals; they are not all marked complete.

## 1. Own the baseline experiment

- Reproduce the supplied real-data run and explain each quality field and error metric.
- Add one Oregon well with sufficient dense daily data; preserve site and source metadata.
- Plot errors by lead day and season. Keep the final holdout separate from design decisions.
- Add an experiment comparing persistence and lag models across multiple years. Publish cases
  where the simpler model wins; measured results matter more than model complexity.

Deliverable: a concise case study with reproducible commands, data provenance, and limitations.

## 2. Strengthen the data pipeline

- Add incremental ingestion with a persisted watermark and an overlapping revision lookback.
  Periodically rebuild older periods because revisions can fall outside the lookback.
- Separate well metadata, measurements, source revisions, ingestion events, and withdrawals.
  Preserve agency IDs as strings, sensor changes, vertical datums, well construction, and units.
- Add dataset-level manifests and ingestion counts. Monitor arrival delay, quality failures,
  freshness, and missingness separately from water-level anomalies.
- Expand to multiple wells, one isolated analysis per series; then add regional summaries
  with explicit spatial assumptions rather than merging unrelated depths.
- Move large analytical history to partitioned Parquet and DuckDB, or a suitable warehouse,
  once measured scale justifies it. Keep SQLite as the easy local starting point.
- Add CI linting and a pinned dependency lock. Schedule runs in a separate orchestration layer
  such as Prefect, Dagster, or a managed job runner; the CLI already provides an exit status.

Deliverable: an ingestion job that can be rerun, backfilled, and audited without duplicate rows.

## 3. Improve the science

- Add precipitation and temperature from documented government sources; include delayed
  recharge response, lagged rainfall totals, and seasonal effects as hypotheses to test.
- Audit **availability time**, not only measurement date. Future realized rainfall cannot be
  treated as a known predictor of a real future forecast. Use lagged observations or archived
  weather forecasts available at each origin.
- Compare ETS/state-space models and gradient boosting against the existing baselines using
  the same origins and observed targets. Put hyperparameter tuning inside training/validation.
- Add a residual calibration split, lead-specific prediction intervals, coverage/width reporting,
  and evaluation across hydrologic regimes. Preserve the final test period.
- Obtain reviewed event labels for anomaly evaluation; assess false alarms per well/month,
  missed events, detection delay, and sensitivity to seasonal transitions.
- Add as-of snapshots before claiming an operational backtest free from revision leakage.

Deliverable: an experiment report with benchmarks, uncertainty diagnostics, and error analysis.

## 4. Add an operational interface

- Build a read-only dashboard for well selection, location metadata, source age, missing data,
  anomalies, forecasts, and model comparison. Show units and forecast origin beside each chart.
- Expose stable read APIs over versioned run outputs rather than retraining on each request.
- Add alerts only after reviewing thresholds and delivery requirements. Include deduplication,
  cooldowns, missing-data alerts, and a way to mark reviewed events.
- Add deployment logs and health checks. Measure latency and data volume before selecting
  distributed services, containers, or a cloud database.

Deliverable: a small reliable demo that explains its evidence and stays useful when data is late.

## Portfolio evidence to collect

Keep an architecture diagram, a data dictionary, examples of rejected input, regression tests
for temporal leakage, a baseline leaderboard, and a documented failed-model experiment. Report
your actual number of wells, records, runs, errors, and processing times. Add numbers to your
resume only after measuring them; do not describe this starter as production deployment.
