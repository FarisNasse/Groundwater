# Run the rush update

1. Extract the ZIP and open the `Groundwater-main` folder.
2. To inspect results immediately, open **sample_study/index.html** in a browser.
3. To reproduce them, open PowerShell in this folder and run:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements-py312.lock
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
.\.venv\Scripts\python.exe -m groundwater study --file configs/study.json
```

Open the new `index.html` path printed by the command. Installation needs internet access;
the study itself uses bundled real data and runs offline. Environment activation is optional.
Use the virtual environment's Python directly as shown. If an existing `.venv` has a broken pip,
try `.\.venv\Scripts\python.exe -m ensurepip --upgrade` before the installation commands.

For all tests:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

The roadmap review and measured case study are in **docs/RUSH_REVIEW.md**. Incremental ingestion
commands are in **docs/INGESTION.md**. This update retains the original `run`, `replay`, `demo`,
and `discover` commands and adds `study` and `ingest`.

Use your own normal Git workflow to copy/commit the updated source. No remote repository was
modified, and no service was deployed. The sample data and its forecasts are historical.
