"""Portable self-contained report with inline SVG; no dashboard server required."""

from html import escape
import json
import numpy as np


def chart(series, anomalies, forecast):
    observed = series.iloc[-365:]
    combined = observed.to_list() + forecast["predicted_depth_ft"].to_list()
    finite = [x for x in combined if np.isfinite(x)]
    low, high = min(finite) - 0.2, max(finite) + 0.2
    n, width, height = len(combined), 960, 320

    def point(i, value):
        return f"{55 + i / max(n - 1, 1) * 875:.2f},{30 + (value-low)/(high-low)*240:.2f}"

    # Draw separate segments for missing days instead of visually joining gaps.
    segments, current = [], []
    for i, value in enumerate(observed):
        if np.isfinite(value):
            current.append(point(i, value))
        elif current:
            segments.append(current)
            current = []
    if current:
        segments.append(current)
    lines = ''.join(f'<polyline points="{" ".join(s)}" fill="none" stroke="#176e8b" '
                    'stroke-width="2"/>' for s in segments)
    future_points = " ".join(point(len(observed) + i, val)
                             for i, val in enumerate(forecast.predicted_depth_ft))
    dots = []
    flags = anomalies.reindex(observed.index)
    for i, (_, row) in enumerate(flags.iterrows()):
        if row.is_anomaly:
            x, y = point(i, row.depth_ft).split(",")
            dots.append(f'<circle cx="{x}" cy="{y}" r="4" fill="#ce453b"/>')
    return (f'<svg role="img" aria-label="Observed depth and forecast, increasing depth downward" '
            f'viewBox="0 0 {width} {height}"><rect width="960" height="320" fill="#f5f9fc"/>'
            f'<text x="4" y="25" font-size="13">{low:.1f} ft</text>'
            f'<text x="4" y="275" font-size="13">{high:.1f} ft</text>{lines}'
            f'<polyline points="{future_points}" fill="none" stroke="#a36c00" '
            f'stroke-width="3"/>{"".join(dots)}'
            f'<text x="55" y="308" font-size="13">{observed.index[0].date()}</text>'
            f'<text x="800" y="308" font-size="13">{forecast.index[-1].date()}</text></svg>')


def write_report(path, config, source, quality, anomalies, result):
    title = "Groundwater Intelligence & Forecasting"
    label = {"synthetic": "SYNTHETIC DEMO — generated values", "live": "USGS public measurements",
             "replay": "Replayed GeoJSON — see source provenance"}[source]
    table = result["leaderboard"].to_html(index=False, float_format=lambda x: f"{x:.4f}", border=0)
    holdout = result["holdout"]
    html = f'''<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{title}</title>
<style>body{{font:16px system-ui,sans-serif;max-width:1050px;margin:40px auto;padding:0 24px;
color:#16374b;background:#fff}}h1{{font-size:34px}}.tag{{color:#805000;background:#fff1cf;
padding:12px;border-radius:8px}}.cards{{display:flex;gap:16px;flex-wrap:wrap;margin:24px 0}}
.card{{background:#eff5f8;padding:18px;border-radius:10px;min-width:200px}}strong{{font-size:22px}}
svg{{width:100%;height:auto}}table{{border-collapse:collapse;width:100%;margin:16px 0}}
td,th{{padding:12px;text-align:left;border-bottom:1px solid #dbe3e8}}pre{{white-space:pre-wrap;
background:#f5f9fc;padding:18px}}p{{line-height:1.6}}</style>
<h1>{title}</h1><p class="tag">{label} · {escape(config.site)}</p>
<p>Daily mean depth below land surface, in feet. A larger value means deeper groundwater.
This is one well's time series; it does not measure total aquifer storage.</p>
<div class="cards"><div class="card">Selected model<br><strong>{escape(holdout['selected_model'])}</strong></div>
<div class="card">Final holdout MAE<br><strong>{holdout['mae_ft']:.3f} ft</strong></div>
<div class="card">Flagged observations<br><strong>{int(anomalies.is_anomaly.sum())}</strong></div></div>
<h2>Recent observations and forecast</h2>{chart(series=result['series'], anomalies=anomalies, forecast=result['forecast'])}
<p>Blue: measured depth. Red: anomaly flag. Gold: forecast. Depth increases downward.
Forecast begins after the latest observation ({quality['last_observed']}), which is
{quality['age_days_at_run']} days before this run; historical replay forecasts are historical.</p>
<h2>Validation model comparison</h2>{table}
<p>{config.folds} expanding training windows; {config.horizon}-day recursive forecasts.
All models share the same origins and observed scoring dates. Lower MAE/RMSE is better.
Bias is prediction minus observation. Hyperparameters are fixed.</p>
<h2>Final holdout</h2><pre>{escape(json.dumps(holdout, indent=2))}</pre>
<p>The final period was excluded from model selection. The selected model is then refit on all
available observations for the next forecast. No calibrated uncertainty intervals are implemented.</p>
<h2>Data quality</h2><pre>{escape(json.dumps(quality, indent=2))}</pre>
<p>Anomalies compare each value with preceding observations only. Flags can reflect pumping,
recharge, seasonal transitions, or sensor issues; investigate before excluding them. Qualified
and provisional values are retained unless approved_only is configured. Raw source pages and
provenance are preserved beside the exports. See README and docs/METHODOLOGY.md for assumptions.</p>
</html>'''
    path.write_text(html, encoding="utf-8")
