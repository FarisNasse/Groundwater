# Incremental ingestion

The `ingest` command maintains a separate, audited dataset ledger. Forecasting commands continue
to analyze complete run snapshots; a 30-day incremental response is never mistaken for a full
training history. No scheduler or external database is needed to exercise the ledger locally.

```bash
# First request builds all configured history and captures monitoring-location metadata.
python -m groundwater ingest --config configs/oregon_2023_2025.toml --root artifacts/monitor

# Repeat: re-fetch the last 30 requested days, including possible source revisions.
python -m groundwater ingest --config configs/oregon_2023_2025.toml --root artifacts/monitor --lookback-days 30

# Explicit rebuild: catch revisions older than the lookback.
python -m groundwater ingest --config configs/oregon_2023_2025.toml --root artifacts/monitor --full-refresh

# Targeted backfill: replace only this complete request window; leave the watermark alone.
python -m groundwater ingest --config configs/oregon_2023_2025.toml --root artifacts/monitor --backfill-start 2023-01-01 --backfill-end 2023-01-31
```

The sample config is deliberately historical. For monitoring, change `end` to the latest completed
source calendar day. Keep `start` fixed to retain the same dataset. Changing `start`, site,
variable, statistic, series selection, or approval policy creates a different dataset identity.
Dates and identifiers must be strings. `--rebuild-days 30` triggers a full rebuild on the next
invocation after 30 days have elapsed since the last successful full refresh. It is not a timer:
an external scheduler must invoke the CLI. Advancing `end` more than a lookback still fetches
every intervening day. Moving `end` backward requires an explicit backfill.

An offline complete GeoJSON can replace the request with `--file`. Its observations must fall
within the planned query window: use `--full-refresh` to replay an entire historical fixture.
Live and replay ledgers are separate. There is no automatic synthetic fallback.

## Ledger tables

SQLite files live at `silver/live_ingestion.sqlite` or `silver/replay_ingestion.sqlite`.
These are new ledgers, separate from the original forecasting cache `silver/live.sqlite`.
No automatic migration imports untracked cache history.

| Table | Purpose |
|---|---|
| `measurements` | Latest accepted payload and active/withdrawn state per natural key and dataset |
| `source_revisions` | Distinct accepted per-request revisions, hashes, first seen time, and event ID |
| `ingestion_events` | Request window, start/finish, success/failure, counts, and error |
| `withdrawals` | Immutable records of inferred absence from a complete validated request |
| `watermarks` | Last fully queried calendar date and last successful full refresh time |
| `well_metadata` | Versioned raw monitoring-location metadata with hash and first seen event |

The natural key preserves site and series IDs as strings plus calendar date, parameter, and
statistic. Metadata preserves source coordinates, land-surface altitude/datum, aquifer fields,
construction information, and nulls; it never converts depth below land surface into elevation.
Sensor identity is retained as `series_id`; full sensor hardware/deployment history is not
provided by this endpoint. A changed series is not merged into an existing unselected dataset.
Set `series_id` explicitly for a separate sensor series; other-series filtering is counted.

The watermark records the **requested end**, not the last measured day. This avoids repeatedly
backfilling the entire dataset when arrivals stop; the overlap and periodic rebuild can still
recover late values. Freshness and missing requested days remain separate measurements.

## Commit and failure semantics

The client must finish every page before normalization and commit. Any quarantined record,
ambiguous series, equal-revision conflict, metadata failure, or network failure prevents ledger
changes and watermark advancement, while retaining a failed event and failure manifest. An older
revision is archived as seen but cannot overwrite a newer accepted revision. Equal source revision
timestamps with different values/status/qualifiers fail rather than choosing by arrival order.
The original cache now rejects equal-revision conflicts across runs too.

Within the fully validated query window, an active key absent from the response is marked withdrawn.
This means **inferred source absence**, not an official USGS deletion reason. Empty complete
responses can withdraw that window. Missing keys outside the query window are untouched. The
same/newer accepted revision can restore a withdrawn key. Approved-only responses containing
non-approved values fail quarantine checks rather than misclassifying policy filtering as source
deletion. Review that policy before running an approved-only monitoring job.

SQLite serializes commits and detects a concurrent successful update after fetching; that attempt
fails and must be retried. A snapshot export failure rolls back the transaction. Network retrieval
happens outside the write transaction. Ingestion counters distinguish inserted, revised,
unchanged, stale, withdrawn, restored, and new revision rows. Repeating identical input adds an
ingestion event, not duplicate measurements or revision rows.

Each `ingestions/<event>/manifest.json` records the query, dataset, source-page hashes, code hash,
counts, snapshot hash, and run times. `observations.csv` contains the complete active dataset at
that event; `quality.json` describes the fetched window and `quarantine.json` its rejected inputs.
Freshness is measured at ingestion, and first-seen time records availability **to this application**.
It does not establish original USGS publication time or recover historical as-of data.

## Limits

This is a local ledger for small datasets, not a warehouse or production orchestration service.
SQLite state and raw archives need backups. A terminated process can leave an event marked started;
there is no automatic crash recovery service. Source revisions predating the first archived fetch
cannot be reconstructed. Raw pages retain duplicates that normalization reduces to the latest
revision within that request. No operational backtest, sensor-change interpretation, reviewed
alert workflow, or regional spatial aggregation is claimed. Keep full rebuilds enabled: a bounded
lookback alone cannot discover arbitrarily old corrections.
