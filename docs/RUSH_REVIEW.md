# Roadmap review and rush delivery — v0.2

Implemented and measured October 15, 2026 (Pacific time). This is a substantial local build-out,
not a declaration that the entire roadmap is finished. The order was baseline evidence, safe
ingestion, then a small interface over saved results. More model complexity was not justified by
the measured comparisons.

## Roadmap assessment

| Area | Delivered in this rush | Remaining work |
|---|---|---|
| 1. Own the baseline | Reproduced the reference; verified an Oregon well and source metadata; evaluated across years and seasons; added lead/season/year metrics and charts; documented cases where simple models win | Additional wells/regimes and independent expert interpretation |
| 2. Strengthen pipeline | Incremental watermark, revision overlap, periodic rebuild, explicit backfill, separate metadata/revision/event/withdrawal tables, manifests/counters, isolated multi-well study, lint CI, runtime hash lock | Full sensor deployment history, true source publication latency, scheduling/monitoring, backups/recovery; larger storage only if scale warrants it |
| 3. Improve science | Added broader evidence and tests protecting holdout/diagnostic isolation | Weather/recharge features with availability audits, ETS/boosting, calibration split and intervals, reviewed anomaly labels, historical as-of evaluation |
| 4. Operational interface | Portable read-only catalog of saved experiments with locations, age, missingness, report links, anomaly/forecast/model charts | Stable read APIs, live dashboard, reviewed alerts/cooldowns, deployment logs and health checks |

The original evaluation used four adjacent late-year windows. That is a valid narrow experiment,
but weak evidence for year-round behavior. `validation_stride_days` now allows wider spacing while
preserving nonoverlapping scoring windows and an excluded final holdout. The default remains
unchanged, so the original result is still reproducible. The second priority was making repeated
acquisition auditable without silently filling deleted or rejected observations from a cache.

## Reproduce the evidence

Python 3.12 is recommended. From the project folder on Windows PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements-py312.lock
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m groundwater study --file configs/study.json --root artifacts/study
```

The last command prints `index.html`. Open that file to choose an experiment. After extraction,
`sample_study/index.html` already contains the measured reports; no installation is needed to
read them. On Linux/macOS, use `python3 -m venv .venv`, then `.venv/bin/python` for the subsequent
Python commands. The source distribution, fixtures, example outputs, and lock are included.

## Case study: two wells, three experiments

Reference: `USGS-444302070252401`, 731 observations in 2023–2024. Oregon:
`USGS-420318121312201`, Klamath County, source well name `40.00S/11.00E-28DDD01`.
The monitoring-location snapshot confirms Oregon, construction depth 335 feet, and source
land-surface datum NAVD88. Forecast targets remain depth **below land surface**, not elevation
above that datum. Coordinates, aquifer fields, native metadata nulls, and source URLs are retained.

The first Oregon acquisition covered 2018–2025: 2,645 observations. Coverage inspection found
92-day and 132-day maximum internal gaps in 2021 and 2022 respectively. Before model evaluation,
two dense epochs were chosen: 2018–2020 (1,093 observed days, three missing days) and 2023–2025
(1,096 observed days, no missing days). No data was generated to bridge the excluded years.
The epochs are separate analyses, not a single imputed series.

Each Oregon experiment uses 24 expanding origins spaced 30 days apart, 14-day recursive forecasts,
a minimum of 365 training days, and the same fixed four-model comparison. Final December 18–31
holdouts remain excluded from selection. Validation targets cover calendar 2019–2020 or
2024–2025 and all four meteorological seasons. The reference retains its original four adjacent
14-day origins. Missing targets are not scored. No hyperparameters were tuned in this rush.

| Experiment | Observations | Validation origins | Observed validation targets/model | Selected model | Validation MAE (ft) | Final holdout MAE (ft) |
|---|---:|---:|---:|---|---:|---:|
| Reference 2023–2024 | 731 | 4 | 56 | Persistence | 0.1936 | 1.2629 |
| Oregon 2018–2020 | 1,093 | 24 | 333 | Persistence | 0.4852 | 0.4379 |
| Oregon 2023–2025 | 1,096 | 24 | 336 | Persistence | 0.1183 | 0.1050 |

Total: **2 distinct wells, 2,920 unique observations, 3 experiments, 52 validation origins,
2,900 model/validation-target prediction pairs**, and 42 selected-model holdout targets. These
counts describe the declared study, not an operational deployment. Holdout performance is
reported only for the model selected within each experiment. Do not average depths or treat
these different sites/periods as independent regional performance samples.

| Model | Reference validation MAE (ft) | Oregon 2018–2020 MAE (ft) | Oregon 2023–2025 MAE (ft) |
|---|---:|---:|---:|
| Persistence | 0.1936 | 0.4852 | 0.1183 |
| Random forest lags | 0.3075 | 0.6358 | 0.1464 |
| Ridge lags | 1.7347 | 0.7860 | 0.2050 |
| Seasonal 365 | 3.9482 | 6.3497 | 4.1088 |

This is a documented failed-model experiment: all three alternatives lost to persistence in
pooled validation MAE for every declared experiment. That does not mean the alternatives always
lose. In Oregon 2018–2020, persistence and ridge each won 10 individual folds, with random forest
winning 4. In 2023–2025 the counts were persistence 11, forest 7, ridge 6. A few large errors can
outweigh a model's number of fold wins. Seasonal repetition performed poorly on this record;
this result does not establish its performance at other wells.

The seasonal diagnostic reveals why the pooled number is incomplete. For persistence in Oregon
2018–2020, DJF MAE was 0.1460 feet versus JJA 0.7436 feet. In 2023–2025, DJF was 0.0874 versus
SON 0.1669 feet. These slices describe sampled validation dates, not a causal explanation for
seasonal groundwater behavior. The original reference's much larger final error is retained
and visible instead of being used to redesign the holdout.

## Ingestion evidence

A live full refresh of the Oregon 2023–2025 range inserted 1,096 rows and 1,096 distinct accepted
revision rows. A subsequent overlapping 30-day request covered December 2–31, 2025: 30 unchanged,
zero inserts, zero new revisions, zero withdrawals, and still 1,096 active rows. Monitoring-location
metadata was archived and versioned. Both complete responses had zero quarantine records.
A compact copy of the measured manifests is in `sample_ingestion/`.

Offline tests exercise revisions, stale revisions, inferred withdrawal/restoration, out-of-window
preservation, idempotence, quarantine rollback, export failure rollback, network failures,
backfills, periodic rebuild planning, sensor separation, concurrent updates, and equal-timestamp
conflicts across runs. See INGESTION.md for exact guarantees and limits.

## Verification and next work

The release verification details are in VALIDATION.md. Tests, lint, locked installation, real
replays, live full/incremental acquisition, and report rendering are checked locally. Hosted
GitHub Actions and Windows execution are not claimed.

Next, keep the evidence fixed and add an independent dataset before another model search.
Then implement weather availability contracts and chronological calibration. A stable read API
can serve versioned outputs after the data contract is settled. Reviewed alerts and deployment
belong later. No calibrated uncertainty, confirmed anomaly accuracy, regional aquifer inference,
revision-free operational backtest, or production readiness is claimed by this rush.
