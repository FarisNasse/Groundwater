"""Reproduce isolated experiments from a declaration; never pool different wells' depths."""

from html import escape
import hashlib
import json
from pathlib import Path
import re
import time
import uuid

import pandas as pd

from groundwater.config import Config
from groundwater.pipeline import run, write_json
from groundwater.usgs import utc_now


def run_study(declaration, root):
    declaration = Path(declaration).resolve()
    spec = json.loads(declaration.read_text(encoding='utf-8'))
    experiments = spec.get('experiments', []) if isinstance(spec, dict) else None
    if not isinstance(experiments, list) or not experiments:
        raise ValueError('Study requires at least one experiment')
    if any(not isinstance(e, dict) or any(not isinstance(e.get(k), str) or not e[k]
               for k in ('id', 'config', 'snapshot')) for e in experiments):
        raise ValueError('Every experiment requires string id, config, and snapshot fields')
    ids = [e.get('id', '') for e in experiments]
    if len(set(ids)) != len(ids) or any(not re.fullmatch(r'[A-Za-z0-9_-]+', x) for x in ids):
        raise ValueError('Study IDs must be unique simple directory names')
    directory = Path(root).resolve() / 'studies' / (utc_now().replace(':', '-') + '-' + uuid.uuid4().hex[:8])
    directory.mkdir(parents=True)
    manifest = {'started_at': utc_now(), 'declaration': spec, 'experiments': [], 'status': 'started'}
    write_json(directory / 'declaration.json', spec)
    comparisons = []
    for experiment in experiments:
        entry = {'id': experiment['id'], 'status': 'started'}
        began = time.perf_counter()
        try:
            config = Config.load(declaration.parent / experiment['config'])
            entry['config'] = config.as_dict()
            metadata = None
            if experiment.get('metadata'):
                raw_metadata = (declaration.parent / experiment['metadata']).read_bytes()
                metadata = json.loads(raw_metadata)
                if metadata.get('id') != config.site:
                    raise ValueError('Study metadata site differs from analysis site')
                entry['metadata_sha256'] = hashlib.sha256(raw_metadata).hexdigest()
            output = run(config, directory / experiment['id'], 'replay',
                         declaration.parent / experiment['snapshot'])
            quality = json.loads((output / 'quality.json').read_text())
            provenance = json.loads((output / 'provenance.json').read_text())
            if metadata:
                write_json(output / 'well_metadata.json', metadata)
            entry.update(status='complete', report=(output / 'report.html').relative_to(directory).as_posix(),
                         quality=quality, holdout=json.loads((output / 'holdout.json').read_text()),
                         selected_model=provenance['selected_model'],
                         observations_sha256=provenance['observations_sha256'],
                         code_sha256=provenance['code_sha256'], metadata=metadata)
            for row in pd.read_csv(output / 'leaderboard.csv').to_dict('records'):
                comparisons.append({'experiment': experiment['id'], 'site': config.site,
                                    'selected': row['model'] == entry['selected_model'], **row})
        except (ValueError, RuntimeError, OSError) as exc:
            entry.update(status='failed', error=str(exc))
        entry['elapsed_seconds'] = round(time.perf_counter() - began, 3)
        manifest['experiments'].append(entry)
        write_json(directory / 'study.json', manifest)
    manifest.update(status=('complete' if all(x['status'] == 'complete' for x in manifest['experiments'])
                            else 'failed'), finished_at=utc_now())
    manifest['unique_wells'] = len({x['config']['site'] for x in manifest['experiments']
                                    if x['status'] == 'complete'})
    if comparisons:
        pd.DataFrame(comparisons).to_csv(directory / 'comparison.csv', index=False)
    write_json(directory / 'study.json', manifest)
    write_catalog(directory / 'index.html', manifest)
    return directory, manifest['status'] == 'complete'


def write_catalog(path, manifest):
    rows = []
    for entry in manifest['experiments']:
        if entry['status'] != 'complete':
            rows.append(f'<tr><td>{escape(entry["id"])}</td><td colspan="6">Failed: '
                        f'{escape(entry["error"])}</td></tr>')
            continue
        config, quality, holdout = entry['config'], entry['quality'], entry['holdout']
        properties = (entry.get('metadata') or {}).get('properties', {})
        location = ' · '.join(str(properties[x]) for x in ('state_name', 'county_name')
                              if properties.get(x))
        rows.append(f'<tr><td><a href="{escape(entry["report"], quote=True)}">'
                    f'{escape(entry["id"])}</a><br>{escape(config["site"])}<br>{escape(location)}</td>'
                    f'<td>{config["start"]}<br>{config["end"]}</td>'
                    f'<td>{quality["observed_days"]} observations<br>{quality["missing_days"]} missing days'
                    f'<br>{quality["age_days_at_run"]} days old at run</td>'
                    f'<td>{escape(entry["selected_model"])}</td>'
                    f'<td>{holdout["mae_ft"]:.4f} ft</td>'
                    f'<td>{config["folds"]} origins<br>{entry["elapsed_seconds"]:.2f} s</td></tr>')
    html = '''<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Groundwater study</title>
<style>body{font:16px system-ui;color:#16374b;max-width:1200px;margin:40px auto;padding:0 24px}
table{border-collapse:collapse;width:100%}td,th{padding:16px;text-align:left;border-bottom:1px solid #ddd}
a{color:#176e8b}p{line-height:1.6}.scroll{overflow:auto}</style>
<h1>Groundwater experiment catalog</h1><p>Frozen historical data. Select an experiment to inspect
its observations, missingness, anomalies, forecasts, and validation diagnostics. Depth below land
surface is measured in feet; greater depth means lower water. Each series is analyzed separately.
No regional average or live conditions are implied. Forecast origin and units appear in each report.</p>
<div class="scroll"><table><thead><tr><th>Experiment / location</th><th>Period</th><th>Data quality</th>
<th>Validation choice</th><th>Final holdout MAE</th><th>Evaluation / runtime</th></tr></thead><tbody>'''
    html += ''.join(rows) + '</tbody></table></div><p>See comparison.csv and study.json for measured results, settings, source hashes, and status. Final holdout results were excluded from selection. These historical experiments do not establish operational performance.</p></html>'
    path.write_text(html, encoding='utf-8')
