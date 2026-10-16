# Methodology and boundaries

## Question and target

Can daily mean groundwater depth at one well be forecast over the next 14 days more accurately
than persistence? Forecasts describe this variable in feet. They do not estimate aquifer volume,
explain causal drivers, or prove regional drought. The sample well is chosen for verifiable,
dense public data, not because it represents Oregon.

## Measurement contract

The parser requires USGS parameter 72019, statistic 00003, unit ft, one site, and one time series.
Daily observations use the source calendar date without timezone conversion. Source revision
timestamps are converted to UTC. Negative depths are retained; nonfinite values and the sentinel
-999999 are quarantined. Invalid date/metadata/units are quarantined. Multiple competing series
or equally revised conflicting values fail the run rather than being averaged or guessed.

The native approval status and qualifiers are stored as JSON strings in SQLite/CSV. By default,
provisional/other/qualified values are included and counted. `approved_only=true` accepts only
an explicit Approved status. A future policy may exclude specific qualifier codes after review
of official definitions. Filtering them indiscriminately can remove useful observations.

The forecasting cache is a latest-valid-observation cache, not a complete bitemporal ledger. A later quarantined
record does not delete the older valid database row. Analysis deliberately uses the current
run's snapshot, so this older cache entry is never silently substituted. Raw pages and run
snapshots retain the audit history. The separate `ingest` ledger now records inferred withdrawals, first-seen availability, and raw monitoring-location metadata versions; see INGESTION.md. It does not reconstruct historical availability or full sensor deployment history.

## Missingness and staleness

A daily calendar is inserted between the first and last accepted observations. Coverage is
the fraction of these calendar days that have observations. Requested-window leading/trailing
gaps are reported separately. The default minimum internal coverage is 95%; gaps longer than
three days block forecasting. Predictor filling is forward-only, bounded, and recalculated
inside each training window. Targets and exported observations are never filled.

The historical snapshot is intentionally stale. A run reports observation age but does not
block historical replay on staleness. A production monitoring job should separately gate
freshness relative to expected reporting cadence and distinguish missing data from hydrologic
anomalies. Sparse manual well readings require a different model and calendar, not heavy
interpolation into artificial daily measurements.

## Anomalies

For depth y at day t, compute the median and median absolute deviation (MAD) over the preceding
60 calendar days. The robust score is |y - median| / max(1.4826 × MAD, 0.05 feet). The default flag
threshold is 4.5. The current value is excluded from its reference window. At least 30 observed
prior values are needed under the default window. Missing/warm-up values are unscorable, not
evidence of normal behavior.

This detects local level departures, not all forms of anomalous dynamics. Seasonality, pumping,
recharge, drift, and sensor problems can produce similar scores. The scale floor and threshold
are configurable engineering choices, not validated hydrological limits. Anomalies remain in
model training; removing values after inspecting future behavior can bias an experiment.
No precision/recall or ground-truth alert performance is claimed.

## Forecast experiments

Given H=14 and K=4, the final H calendar days form an untouched holdout. In the earlier development
data, K adjacent H-day validation windows use expanding preceding histories. All models have
identical training cutoffs and evaluation dates. Each fold needs at least 365 training days;
lag models need at least 100 eligible training targets. The overall default minimum is
365 + (K+1)H = 435 calendar days. Longer records are preferable.

Models:

1. Persistence repeats the most recent usable value.
2. Seasonal naive uses a fixed 365-day lag. Leap-year alignment is approximate; it is not an
   exact same-calendar-date-last-year lookup.
3. Ridge regression fits lag/calendar features with a training-fold StandardScaler and fixed
   regularization alpha=10.
4. Random forest uses the same predictors, 80 trees, maximum depth 10, minimum leaf size 5,
   one worker, and random seed 42.

For a future day, predicted values are appended to the history and used for subsequent lag
features. Observed values inside the forecast window are never used to update predictions.
Calendar features are known in advance; depth-derived features use only preceding values.
Hyperparameters are fixed to avoid suggesting that a larger search was performed.

The leaderboard pools absolute/squared errors over observed validation targets. Missing targets
are excluded consistently. The selected model minimizes pooled validation MAE; alphabetical model
name resolves exact ties. RMSE emphasizes larger misses, and bias is prediction minus observed
depth (positive means predicting deeper water). The selected model alone is evaluated on the
final holdout. Afterward it is refit on all observations for a next-horizon forecast.

The holdout is separate from model selection but only covers a short interval. Four nearby
validation windows do not demonstrate performance across seasons, climates, droughts, or wells.
Do not report their errors as generalized model performance. Expand to multiple seasons and
sites, preserve a predeclared holdout, and compare errors by lead day and regime.

## What “no leakage” means here

The implementation prevents temporal leakage through feature construction, imputation, scaling,
recursive prediction, and model selection. Tests perturb future observations to check this.
However, this is a **retrospective experiment on the current USGS record**. Approved historical
values may include revisions made after their observation dates. Reconstructing an operational
backtest requires historical as-of source snapshots and original availability timestamps,
which these fixtures do not have. The ingestion ledger starts first-seen history only from its
actual acquisition events. Do not claim point-in-time operational performance.

## Reproducibility and uncertainty

Source hashes, normalized-data hash, code hash, config, seed, Python version, and dependency
versions are recorded. Broad dependency bounds permit installation on common machines; no
cross-platform bitwise-identical output is promised. Capture an exact dependency lock for a
published benchmark. All tests are offline; live requests can change because source data can
be revised or unavailable.

No calibrated intervals are implemented. A single RMSE is not a prediction interval. Add a
separate chronological residual-calibration period, horizon-specific uncertainty estimates,
and observed coverage/width diagnostics before presenting intervals as calibrated. Avoid
reusing final holdout errors to tune uncertainty or model choices.


## Spaced validation and isolated epochs (v0.2)

With stride S (default H), fold i has origin `len(development) - H - (K-i-1)S - 1`,
for zero-based i. Minimum total history is `min_train_days + 2H + (K-1)S` calendar days.
S must be at least H. Nonadjacent windows sample periods rather than scoring every date; counts
and observed targets are exported. Diagnostic season/year fields use the target date. Their
metrics use validation only. No final holdout values are used to pick an origin or a hyperparameter.

Oregon epochs were chosen from source coverage before forecast scoring. The full 2018–2025
candidate had long missing stretches in 2021–2022, so only 2018–2020 and 2023–2025 are modeled
independently. No state is carried across the gap, and no claim covers the excluded years.
All seasonal summaries use northern-hemisphere meteorological seasons. The provided study is
retrospective, not a revision-free operational backtest.

`requirements-py312.lock` now pins and hashes the tested numerical stack. Use `--require-hashes`
for that runtime install, then `--no-deps -e .` for project code. Build/dev tools are outside this
runtime lock. Package versions, source snapshots, and platform differences still limit bitwise
reproducibility. The clean locked Python 3.12 install was exercised on Linux.
