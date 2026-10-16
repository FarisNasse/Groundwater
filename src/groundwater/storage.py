"""Small transactional silver layer. Analysis uses this run's validated snapshot."""

import sqlite3
from pathlib import Path
from groundwater.quality import COLUMNS


def upsert(frame, database):
    Path(database).parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS observations (
            site TEXT NOT NULL, series_id TEXT NOT NULL, date TEXT NOT NULL,
            depth_ft REAL NOT NULL, parameter_code TEXT NOT NULL, statistic_id TEXT NOT NULL,
            unit TEXT NOT NULL CHECK(unit='ft'), approval_status TEXT NOT NULL,
            qualifier TEXT NOT NULL, last_modified TEXT NOT NULL,
            PRIMARY KEY(site,series_id,date,parameter_code,statistic_id))""")
        for row in frame.to_dict("records"):
            old = conn.execute("""SELECT depth_ft,approval_status,qualifier,last_modified
                FROM observations WHERE site=? AND series_id=? AND date=?
                AND parameter_code=? AND statistic_id=?""",
                tuple(row[k] for k in ("site", "series_id", "date", "parameter_code", "statistic_id"))
                ).fetchone()
            if old and old[3] == row["last_modified"] and old[:3] != tuple(
                    row[k] for k in ("depth_ft", "approval_status", "qualifier")):
                raise ValueError("Conflicting cached values with identical source revision")
        conn.executemany("""INSERT INTO observations VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(site,series_id,date,parameter_code,statistic_id) DO UPDATE SET
            depth_ft=excluded.depth_ft, approval_status=excluded.approval_status,
            qualifier=excluded.qualifier, last_modified=excluded.last_modified
            WHERE excluded.last_modified >= observations.last_modified""",
                         frame[COLUMNS].itertuples(index=False, name=None))
    # Historical approved rows remain if a later run quarantines a value. They are never
    # silently used as replacements: models consume only the new run's snapshot CSV.
