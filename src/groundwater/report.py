"""Portable self-contained report with inline SVG; no dashboard server required."""

from html import escape
import json
import numpy as np


def error_chart(frame, dimension, title):
    """Small native SVG chart. Only validation predictions feed these summaries."""
    categories = sorted(frame[dimension].unique())
    if dimension == "season":
        categories = [s for s in ("DJF", "MAM", "JJA", "SON") if s in categories]
    maximum = max(float(frame.mae_ft.max()) * 1.1, 0.01)
    colors = ("#176e8b", "#b03a2e", "#6c3483", "#a36c00")
    elements = []
    for i in range(5):
        y = 30 + i * 45
        elements.append(f'<path d="M60 {y}H930" stroke="#dbe3e8"/>'
                        f'<text x="2" y="{y+4}" font-size="12">{maximum*(4-i)/4:.2f}</text>')
    for i, category in enumerate(categories):
        x = 60 + i * 870 / max(len(categories) - 1, 1)
        elements.append(f'<text x="{x:.1f}" y="234" text-anchor="middle" '
                        f'font-size="12">{escape(str(category))}</text>')
    for i, (model, group) in enumerate(frame.groupby("model", sort=True)):
        points = []
        for _, row in group.iterrows():
            x = 60 + categories.index(row[dimension]) * 870 / max(len(categories) - 1, 1)
            y = 210 - float(row.mae_ft) / maximum * 180
            points.append(f"{x:.2f},{y:.2f}")
            elements.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="3" fill="{colors[i]}"/>')
        # Category order (especially seasons) controls line order too.
        points.sort(key=lambda p: float(p.split(",")[0]))
        elements.append(f'<polyline points="{" ".join(points)}" fill="none" '
                        f'stroke="{colors[i]}" stroke-width="2"/>')
        elements.append(f'<text x="{60+i*225}" y="267" font-size="12" '
                        f'fill="{colors[i]}">{escape(model)}</text>')
    return (f'<h3>{escape(title)}</h3><div class="scroll plot"><svg role="img" '
            f'aria-label="{escape(title)}; MAE in feet" '
            f'viewBox="0 0 960 285">{"".join(elements)}</svg></div>')


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
    diagnostics = "".join(error_chart(result[key], dimension, title) for key, dimension, title in (
        ("errors_by_lead_day", "lead_day", "Validation MAE by forecast lead day"),
        ("errors_by_season", "season", "Validation MAE by target season")))
    yearly = result["errors_by_year"].to_html(index=False, float_format=lambda x: f"{x:.4f}", border=0)
    html = f'''<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{title}</title>
<style>body{{font:16px system-ui,sans-serif;max-width:1050px;margin:40px auto;padding:0 24px;
color:#16374b;background:#fff}}h1{{font-size:34px}}.tag{{color:#805000;background:#fff1cf;
padding:12px;border-radius:8px}}.cards{{display:flex;gap:16px;flex-wrap:wrap;margin:24px 0}}
.card{{background:#eff5f8;padding:18px;border-radius:10px;min-width:200px}}strong{{font-size:22px}}
svg{{width:100%;height:auto}}table{{border-collapse:collapse;width:100%;margin:16px 0}}
td,th{{padding:12px;text-align:left;border-bottom:1px solid #dbe3e8}}pre{{white-space:pre-wrap;
overflow-wrap:anywhere;background:#f5f9fc;padding:18px}}p{{line-height:1.6}}
.scroll{{overflow-x:auto;max-width:100%}}.plot svg{{min-width:660px}}
@media(max-width:600px){{body{{margin:24px auto}}h1{{font-size:28px}}
.card{{min-width:0;flex:1 1 180px}}}}</style>
<h1>{title}</h1><p class="tag">{label} · {escape(config.site)}</p>
<p>Daily mean depth below land surface, in feet. A larger value means deeper groundwater.
This is one well's time series; it does not measure total aquifer storage.</p>
<div class="cards"><div class="card">Selected model<br><strong>{escape(holdout['selected_model'])}</strong></div>
<div class="card">Final holdout MAE<br><strong>{holdout['mae_ft']:.3f} ft</strong></div>
<div class="card">Flagged observations<br><strong>{int(anomalies.is_anomaly.sum())}</strong></div></div>
<h2>Recent observations and forecast</h2><div class="scroll plot">{chart(series=result['series'], anomalies=anomalies, forecast=result['forecast'])}</div>
<p>Blue: measured depth. Red: anomaly flag. Gold: forecast. Depth increases downward.
Forecast begins after the latest observation ({quality['last_observed']}), which is
{quality['age_days_at_run']} days before this run; historical replay forecasts are historical.</p>
<h2>Validation model comparison</h2><div class="scroll">{table}</div>
<p>{config.folds} expanding training windows; {config.horizon}-day recursive forecasts.
All models share the same origins and observed scoring dates. Lower MAE/RMSE is better.
Origins are spaced {config.validation_stride_days or config.horizon} calendar days apart.
Bias is prediction minus observation. Hyperparameters are fixed.</p>
{diagnostics}<h3>Validation errors by target year</h3><div class="scroll">{yearly}</div>
<p>These slices exclude the final holdout. DJF: December–February; MAM: March–May;
JJA: June–August; SON: September–November. Missing seasons have no evaluated targets.
Counts and signed bias are in the accompanying CSV files; sparse slices are uncertain.</p>
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
