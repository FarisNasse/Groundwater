# Data and metrics dictionary

## Observation snapshot

| Column | Meaning |
|---|---|
| `site` | Full USGS monitoring-location string; preserve leading zeroes |
| `series_id` | Source daily sensor/time-series identifier; never average different series |
| `date` | Source calendar date, without timezone truncation |
| `depth_ft` | Daily mean depth below land surface in feet; increasing is deeper |
| `parameter_code`, `statistic_id` | Supported codes `72019` and `00003` |
| `unit` | `ft`; other units are quarantined |
| `approval_status`, `qualifier` | Source values preserved as JSON strings |
| `last_modified` | Source revision timestamp normalized to UTC; not assumed publication time |

## Quality fields

| Field | Interpretation |
|---|---|
| `input_records` | Features in the complete fetched/replayed response |
| `valid_records` | Accepted unique rows after latest-revision selection |
| `quarantined_records` | Rejected inputs; see the original feature and reason in quarantine.json |
| `filtered_other_series` | Rows deliberately excluded by explicit series selection |
| `duplicate_revisions_removed` | Accepted extra revisions/duplicates reduced to one natural key |
| `first_observed`, `last_observed` | Actual accepted observation bounds |
| `calendar_days`, `observed_days`, `missing_days` | Days and observed/missing targets within those bounds |
| `longest_gap_days` | Longest consecutive missing stretch inside the observed span |
| `coverage_within_observed_span` | Observed days divided by calendar days, not requested days |
| `requested_start`, `requested_end` | Configured request bounds |
| `missing_requested_leading_days`, `missing_requested_trailing_days` | Requested edge gaps, reported separately |
| `age_days_at_run` | UTC run date minus latest observation date; historical replay stays historical |
| `provisional_or_other_records` | Accepted rows whose status is not exactly Approved |
| `qualified_records` | Accepted rows with a non-null source qualifier |
| `forecast_blockers` | Explicit coverage/gap reasons the forecast could not proceed |

`input_records = valid_records + quarantined_records + filtered_other_series +
duplicate_revisions_removed` for a successful normalization. A fatal ambiguity prevents returning
a normalized dataset. Ingest quality describes the request window; analysis quality describes the
full run snapshot. See INGESTION.md for separate ledger counters and dataset freshness.

## Evaluation and diagnostics

| Field | Interpretation |
|---|---|
| `forecast_origin` / `train_end` | Last calendar day available to that forecast |
| `lead_day` | Calendar days after origin; predictions are recursive |
| `mae_ft` | Mean absolute prediction error; the validation selection metric |
| `rmse_ft` | Square root of mean squared error, emphasizing large misses |
| `bias_ft` | Mean prediction minus observation; positive predicts deeper water |
| `n_observed` | Finite actual/prediction pairs; missing targets are never filled for scoring |
| `season` | Target-date meteorological season: DJF, MAM, JJA, SON |
| `year` | Target-date calendar year, not the origin year or hydrologic water year |

All `errors_by_*.csv` files contain **validation-only** metrics pooled over individual observed
targets. Counts differ by slice. They exclude holdout and future forecasts. Season plots connect
categories for comparison; they do not estimate an interpolated seasonal error curve. A pooled
leaderboard winner need not win the greatest number of individual folds. Model-name order breaks
exact validation MAE ties. Error estimates from a small, correlated set of forecasts are uncertain;
no confidence or calibrated prediction interval is implied.
