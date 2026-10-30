'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

// Exercise the actual renderer without a server, browser install, or network.
const source = fs.readFileSync(path.join(__dirname, '../src/groundwater/web/app.js'), 'utf8');
function renderer(data) {
  const nodes = new Map();
  const document = {
    querySelector(selector) {
      if (!nodes.has(selector)) nodes.set(selector, {});
      return nodes.get(selector);
    },
    querySelectorAll() { return []; },
  };
  const context = vm.createContext({document,
    sessionStorage: {getItem() { return null; }},
    fetch: async () => ({ok: true, json: async () => data}),
  });
  // Omit startup navigation; each test invokes the relevant production function.
  vm.runInContext(source.slice(0, source.indexOf('window.addEventListener')), context);
  return {context, nodes};
}

function savedResult() {
  return {
    id: 'fixture', config: {site: 'USGS-444302070252401', horizon: 2, folds: 4},
    quality: {first_observed: '2024-01-01', last_observed: '2024-12-31',
      age_days_today: 0, observed_days: 366, coverage_within_observed_span: 1, longest_gap_days: 0},
    provenance: {status: 'complete', source_mode: 'replay', finished_at: '2025-01-01'},
    holdout: {selected_model: 'persistence', mae_ft: 1, n_observed: 2,
      start: '2024-12-30', end: '2024-12-31'},
    models: {persistence: 'Last observed level'},
    leaderboard: [{model: 'persistence', mae_ft: 1, rmse_ft: 1, fold_wins: 4}],
    forecast: [{date: '2025-01-01', predicted_depth_ft: 10},
      {date: '2025-01-02', predicted_depth_ft: 10}],
    observations: [{date: '2024-12-30', value: 9},
      {date: '2024-12-31', value: 10, anomaly: true}], files: [],
  };
}

test('older results render without claiming bands or changing forecast values', async () => {
  const data = savedResult();
  data.uncertainty = {available: false, warning: 'This saved run has no empirical bands.'};
  const {context, nodes} = renderer(data);
  await vm.runInContext("result('fixture', 0)", context);
  const html = nodes.get('#main').innerHTML;
  assert.match(html, /This saved run has no empirical bands/);
  assert.doesNotMatch(html, /class="band-swatch"|<polygon|conformal interval/);
  assert.match(html, /class="anomaly-dot"/);
  assert.equal(data.forecast[0].predicted_depth_ft, 10);
});

test('new results show experimental bands and measured coverage with sample counts', async () => {
  const data = savedResult();
  for (const row of data.forecast) Object.assign(row, {
    lower_80_ft: 9, upper_80_ft: 11, lower_95_ft: 8, upper_95_ft: 12,
  });
  data.uncertainty = {available: true, warning: 'Nominal coverage is not guaranteed.',
    holdout_coverage_80: 0.5, holdout_coverage_95: 1,
    holdout_n_scored_80: 2, holdout_n_scored_95: 2};
  const {context, nodes} = renderer(data);
  await vm.runInContext("result('fixture', 0)", context);
  const html = nodes.get('#main').innerHTML;
  assert.match(html, /Experimental 80% \/ 95% bands/);
  assert.match(html, /Nominal coverage is not guaranteed/);
  assert.match(html, /nominal 80% band 50\.0% \(2 days\)/);
  assert.match(html, /nominal 95% band 100\.0% \(2 days\)/);
  assert.equal((html.match(/<polygon/g) || []).length, 2);
});

test('null bands render cleanly and do not invent coverage', async () => {
  const data = savedResult();
  data.forecast.forEach(row => Object.assign(row, {
    lower_80_ft: null, upper_80_ft: null, lower_95_ft: null, upper_95_ft: null,
  }));
  data.uncertainty = {available: false, warning: 'Too few errors: <20 usable targets.',
    holdout_coverage_95: null, holdout_n_scored_95: 0};
  const {context, nodes} = renderer(data);
  await vm.runInContext("result('fixture', 0)", context);
  const html = nodes.get('#main').innerHTML;
  assert.match(html, /Too few errors: &lt;20 usable targets/);
  assert.doesNotMatch(html, /Observed final-test coverage|<polygon|NaN|Infinity/);
});

test('chart bands break at missing bounds instead of bridging gaps', () => {
  const {context} = renderer(savedResult());
  context.historyRows = [{date: '2024-12-31', value: 10}];
  context.futureRows = Array.from({length: 5}, (_, i) => ({
    date: `2025-01-0${i + 1}`, predicted_depth_ft: 10,
    lower_80_ft: i === 2 ? null : 9, upper_80_ft: i === 2 ? null : 11,
    lower_95_ft: i === 2 ? null : 8, upper_95_ft: i === 2 ? null : 12,
  }));
  const html = vm.runInContext('chart(historyRows, futureRows)', context);
  assert.equal((html.match(/<polygon/g) || []).length, 4);
  assert.doesNotMatch(html, /NaN|Infinity/);
});
