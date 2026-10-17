# Open your Groundwater dashboard

1. Extract the ZIP. Open the extracted **Groundwater-main** folder.
2. On Windows, double-click **Start Dashboard.cmd**.
3. Keep the launch window open. Your browser opens the local dashboard.

Python 3.11 or newer must be installed; 3.12 is recommended. On its first launch the program
creates `.venv` and installs dependencies. This first setup needs internet. Following launches
reuse that environment. No environment activation, TOML edits, or API key is needed for examples.

On macOS or Linux, open a terminal in this folder and run:

```bash
python3 start_dashboard.py
```

If the package is already installed in your environment, use one command:

```powershell
.\.venv\Scripts\python.exe -m groundwater dashboard
```

## In the browser

- **Explore an example:** opens the bundled Oregon results immediately, without training.
- **Analyze a well:** use an offline snapshot, or choose live USGS data. Search a listed city,
  choose coordinates on the schematic map, select a saved well, or enter an identifier.
- Choose dates and a forecast length, then select **Check data readiness**.
- Review gaps, coverage, data age, and any blockers. Select **Run analysis** when ready.
- **Saved results:** reopen reports, download data, or return to an active or interrupted task.

Historical snapshots produce forecasts immediately after their last measurement, not today.
The dashboard displays the exact forecast dates. Live discovery and downloads require internet;
no network failure is replaced with synthetic data.

## If something goes wrong

- **No Python found:** install Python 3.12 from python.org and reopen the launcher.
- **First setup fails:** check the connection and retry. If an existing environment has broken
  pip, run `.\.venv\Scripts\python.exe -m ensurepip --upgrade` from this folder, then retry.
- **Address already in use:** use `python -m groundwater dashboard --port 8766` in your installed
  environment, or `python start_dashboard.py --port 8766`.
- **Browser did not open:** visit the local address printed in the launch window.
- **USGS unavailable:** retry later or use **Explore an example** offline.
- **Data not ready:** change dates or wells using the explanation beside the failed check.

Press Ctrl+C in the launch window to stop. An active analysis finishes saving before normal
shutdown. If the process is forcibly stopped, unfinished tasks are marked interrupted at the
next launch and offer a retry. Completed files remain available.

Your new results and saved wells live in `artifacts/`. Keep that folder when updating the source.
To keep working with an existing repository, copy the updated source and supporting files over
your checkout; preserve its `.git`, `.venv`, and `artifacts` folders. The ZIP also includes
`dashboard.patch`, which can be applied to this exact uploaded baseline with `git apply`.

The original CLI commands still work. Details: **docs/DASHBOARD.md**.
