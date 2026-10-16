# Starter verification

Verified on October 15, 2026 with Python 3.12.14, NumPy 2.3.5, pandas 2.2.3,
and scikit-learn 1.8.0. Exact installed numerical dependencies are listed in
`requirements-tested.txt`; that file is an environment snapshot, not a hash-locked build.

## Completed checks

- Editable package installation succeeded.
- The installed CLI and `python -m groundwater` entry points ran.
- All 14 offline unittest tests passed. They cover contract rejection, revision selection,
  conflicting series, negative depth, approval filtering, idempotent upserts, calendar gaps,
  pagination, untrusted next links, failure provenance, causal anomaly references, lag features,
  recursive prediction inputs, insufficient history, and final-holdout isolation.
- A full replay of the bundled real USGS snapshot completed and produced all documented outputs.
- A full live fetch through the implemented USGS client completed for the same site/date range:
  731 records, zero quarantined records, 100% internal daily coverage.
- The synthetic pipeline completed with the explicit synthetic label and isolated storage.
- Live geographic discovery completed for the Portland-area bounding box in README. Results
  included historical/inactive records, so latest dates must be reviewed before choosing a well.
- The report's HTML/SVG structure and exported metrics were checked. No browser-based visual
  QA was performed; the report is a portable starter presentation, not a polished application UI.
- Python source compilation passed.

The repository includes a GitHub Actions matrix for Python 3.11/3.12/3.13. Those hosted jobs
have not been run here; only the local Python 3.12 environment above was exercised.

## Example results: real USGS snapshot

Site: USGS-444302070252401. Observations: January 1, 2023 through December 31, 2024.
Four validation windows, 14 calendar days each, ending before the final 14-day holdout.

| Model | Validation MAE (ft) | Validation RMSE (ft) |
|---|---:|---:|
| Persistence | 0.1936 | 0.2758 |
| Random forest lags | 0.3075 | 0.3593 |
| Ridge lags | 1.7347 | 2.0217 |
| Seasonal 365-day naive | 3.9482 | 4.0433 |

Persistence was selected using validation MAE. On the separate December 18–31, 2024 holdout,
its MAE was **1.2629 ft** and its RMSE was **1.3928 ft**. This larger error is a reason to
investigate behavior across periods; the validation result alone is not evidence of reliable
year-round performance. The forecast after the frozen snapshot covers January 1–14, 2025.

The causal detector flagged 45 of 731 observations under the starter thresholds. These are
unreviewed flags, not confirmed hydrological or sensor events. No detection accuracy is claimed.

These numbers describe this record and this configuration only. They are not an accuracy claim
for other wells, future operational conditions, or a regional aquifer model. See METHODOLOGY.md
for revision/availability limitations and the absence of calibrated uncertainty intervals.
