# v0.2 verification

Verified October 15, 2026 (Pacific time), using Linux Python 3.12.14, NumPy 2.3.5,
pandas 2.2.3, and scikit-learn 1.8.0. Numerical versions match `requirements-tested.txt`.
`requirements-py312.lock` now provides pinned versions and package hashes for the Python 3.12
runtime stack. Build tools and developer tools are outside that runtime lock.

## Local checks completed

- All **30 offline unittest tests passed**, including the original 14 tests.
- Ruff 0.14.0 lint passed, and Python source compilation passed.
- A clean isolated Python 3.12 environment installed all 10 numerical runtime dependencies from
  the lock with hash checking, installed this project, and passed all 30 tests.
- The original reference replay reproduced the supplied metrics and selected persistence.
- The declared study completed all 3 experiments on 2 distinct wells and 2,920 observations.
- The two Oregon real-data replays completed with 24 validation origins apiece, covering four
  seasons and two target years per epoch. No holdout dates were included in diagnostic metrics.
- Source fixture SHA-256 hashes, measured counts, and output references were verified.
- A live full ingestion fetched 1,096 Oregon observations and monitoring-location metadata.
  An overlapping 30-day incremental ingestion then fetched 30 unchanged observations with no
  additional measurement/revision rows. Active snapshot count remained 1,096.
- The synthetic demo completed and retained its explicit synthetic label and separate storage.
- Headless Chromium rendered the saved catalog and all three reports. Each report has three SVG
  charts. No page errors occurred; all links resolved locally. Desktop (1,400 pixels) and mobile
  (390 pixels) pages fit the viewport; wide tables/charts scroll inside their own containers.
  Desktop and mobile report images were visually inspected during development.

Tests cover measurement rejection, negative depth, approval policy, revision conflicts,
series separation, idempotence, causal anomalies/features, recursive forecasts, holdout isolation,
spaced origins, seasonal/missing-target diagnostics, pagination trust, failure provenance,
watermark planning, periodic rebuilds, backfill, withdrawals/restoration, stale revisions,
quarantine/network/export rollback, concurrent update detection, study failure visibility, and
CLI failure status. A failure-path test deliberately logs an expected CLI error.

Hosted GitHub Actions was configured for Python 3.11/3.12/3.13 with lint/tests/replays/study, plus
a separate Python 3.12 locked-install job. Hosted jobs have **not** been run here. Windows and
macOS execution were not performed. Browser tooling was used only for development verification;
it is not a runtime dependency. The runtime lock was installed on Linux, not every platform.

## Reference results

Site USGS-444302070252401, 731 observations from January 1, 2023 through December 31, 2024.
Four 14-day validation windows ending before the final 14-day holdout.

| Model | Validation MAE (ft) | Validation RMSE (ft) |
|---|---:|---:|
| Persistence | 0.1936 | 0.2758 |
| Random forest lags | 0.3075 | 0.3593 |
| Ridge lags | 1.7347 | 2.0217 |
| Seasonal 365 | 3.9482 | 4.0433 |

Persistence's December 18–31, 2024 holdout MAE is 1.2629 feet; RMSE is 1.3928 feet.
The causal detector flags 45 observations; these are unreviewed flags, not confirmed events.
The forecast is January 1–14, 2025, not current conditions.

The complete Oregon results, seasonal error analysis, model failures, ingestion evidence, and
remaining roadmap work are in RUSH_REVIEW.md. Measured sample outputs are under `sample_study/`
and `sample_ingestion/`. These results do not establish calibrated uncertainty, generalization
to other wells, anomaly detection accuracy, regional aquifer conditions, or operational readiness.
