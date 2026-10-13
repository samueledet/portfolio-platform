'use strict';
/* Portfolio Construction Platform - workspace logic. Charts and tables come from core.js; numbers from the API. */

const S = {
  ds: null, step: 'data', rf: 0, start: null, end: null,
  assume: { source: 'historical', method: 'arithmetic', covariance: 'sample', return_multiplier: 1, vol_multiplier: 1, mu: null, vol: null },
  cons: { lower: [], upper: [], groups: [] },
  stats: null, frontier: null, portfolios: [], bt: null, stress: null, sens: null, mc: null,
};
const STEPS = ['data', 'assume', 'optimise', 'backtest', 'stress', 'simulate'];
const num = (id, fallback = 0) => { const v = parseFloat($(id).value); return Number.isFinite(v) ? v : fallback; };
const yearOf = d => d.slice(0, 4);

// ------------------------------------------------------------------ API
async function api(path, body, method = 'POST') {
  const res = await fetch(path, body instanceof FormData ? { method, body }
    : { method, headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined });
  if (!res.ok) {
    let detail = `Request failed (${res.status})`;
    try { const j = await res.json(); detail = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail); } catch (e) { /* keep default */ }
    throw new Error(detail);
  }
  return res;
}
const call = async (path, body) => (await api(`/api/datasets/${S.ds.id}/${path}`, body)).json();
function fail(err) { $('banner').innerHTML = `<b>Could not complete that.</b> ${esc(err.message || err)}`; $('banner').hidden = false; $('banner').scrollIntoView({ block: 'nearest' }); }
async function run(btn, fn) {
  $('banner').hidden = true;
  if (btn) btn.classList.add('busy');
  try { await fn(); } catch (err) { fail(err); } finally { if (btn) btn.classList.remove('busy'); }
}
const common = () => ({ assumptions: assumeSpec(), rf: S.rf, start: S.start, end: S.end });
function assumeSpec() {
  const a = S.assume, o = { source: a.source, method: a.method, covariance: a.covariance, return_multiplier: a.return_multiplier, vol_multiplier: a.vol_multiplier };
  if (a.source === 'custom') { o.mu = a.mu; o.vol = a.vol; }
  return o;
}
const consSpec = () => ({ lower: S.cons.lower, upper: S.cons.upper,
  groups: S.cons.groups.filter(g => g.assets.length && (g.min != null || g.max != null)).map(g => ({ assets: g.assets, min: g.min, max: g.max })) });
const normalise = w => { const s = w.reduce((a, b) => a + b, 0) || 1; return w.map(x => x / s); };
function describe(w) {           // expected return / risk of a weight vector under the current assumptions
  const a = S.stats.assumptions, x = normalise(w), ret = dot(x, a.mu), vol = Math.sqrt(Math.max(0, quad(x, a.cov)));
  return { ret, vol, sharpe: vol > 0 ? (ret - S.rf) / vol : null };
}

// ------------------------------------------------------------------ navigation
function go(step) {
  S.step = step;
  STEPS.forEach(s => { $('p-' + s).hidden = s !== step; });
  $('steps').querySelectorAll('button').forEach(b => b.classList.toggle('on', b.dataset.step === step));
  draw();
}
function draw() {
  if (!S.ds || !S.stats) return;
  ({ data: drawData, assume: renderAssume, optimise: drawOptimise, backtest: drawBacktest, stress: drawStress, simulate: drawSim })[S.step]();
}
function invalidate() { S.frontier = null; S.bt = null; S.stress = null; S.sens = null; S.mc = null; }

// ------------------------------------------------------------------ 1 data
async function onDataset(ds) {
  const n = ds.assets.length;
  Object.assign(S, { ds, rf: num('gRf') / 100, start: ds.start, end: ds.end, portfolios: [], stats: null });
  S.assume = { source: 'historical', method: 'arithmetic', covariance: 'sample', return_multiplier: 1, vol_multiplier: 1, mu: null, vol: null };
  S.cons = { lower: new Array(n).fill(0), upper: new Array(n).fill(1), groups: [] };
  invalidate();
  $('gStart').value = ds.start; $('gEnd').value = ds.end;
  $('gStart').min = $('gEnd').min = ds.start; $('gStart').max = $('gEnd').max = ds.end;
  $('settings').hidden = false; $('dataOut').hidden = false; $('exportBtn').hidden = false;
  $('dsChip').hidden = false; $('dsChip').textContent = `${ds.filename} · ${n} assets · ${ds.frequency}`;
  $('steps').querySelectorAll('button').forEach(b => { b.disabled = false; });
  ['aSource', 'aMethod', 'aCov'].forEach(id => { $(id).selectedIndex = 0; });
  $('aScenario').value = '1,1'; $('aRetMult').value = 1; $('aVolMult').value = 1;
  await loadStats();
  go('data');
}
async function loadStats() { S.stats = await call('statistics', common()); }

function drawData() {
  const ds = S.ds, st = S.stats, A = st.assets;
  $('gHint').textContent = `${st.observations.toLocaleString()} ${ds.frequency} returns from ${st.start} to ${st.end} are in use.`;
  const best = A.reduce((a, b, i) => (b.exp_return > A[a].exp_return ? i : a), 0), calm = A.reduce((a, b, i) => (b.volatility < A[a].volatility ? i : a), 0);
  $('dataKpis').innerHTML = tile('Assets', ds.assets.length, `${ds.kind} uploaded, ${ds.frequency}`) +
    tile('Observations', st.observations.toLocaleString(), `${st.start} to ${st.end}`) +
    tile('Highest expected return', pct(A[best].exp_return), ds.assets[best]) + tile('Lowest risk', pct(A[calm].volatility), ds.assets[calm]);
  if (ds.warnings.length) $('dataKpis').insertAdjacentHTML('beforeend', `<div class="kpi" style="grid-column:1/-1"><div class="label">Data notes</div><div class="detail">${ds.warnings.map(esc).join(' ')}</div></div>`);

  const g = st.growth, yearIdx = g.dates.map((d, i) => i).filter(i => i > 0 && yearOf(g.dates[i]) !== yearOf(g.dates[i - 1]));
  lineChart($('growthChart'), { labels: g.dates, series: ds.assets.map((a, i) => ({ name: a, color: SERIES[i], values: g.values[i] })),
    yFmt: v => v.toFixed(0), valFmt: v => v.toFixed(1), xTickIdx: yearIdx, xFmt: i => yearOf(g.dates[i]), legend: 'growthLegend' });

  const method = S.assume.method === 'compound' ? '(1 + mean)^periods − 1' : 'mean × periods per year';
  $('statsSub').textContent = `Expected return = ${method}. Risk = standard deviation × √${ds.periods_per_year}. Sharpe uses the risk-free rate above.`;
  table('statsTable', ['Asset', 'Expected return', 'Risk σ', 'Sharpe', 'Cumulative', 'Max drawdown', 'Worst period', 'Best period', 'Skew', 'Excess kurtosis'],
    A.map((a, i) => [`<b>${esc(ds.assets[i])}</b>${ds.names[i] !== ds.assets[i] ? `<span class="cls">${esc(ds.names[i])}</span>` : ''}`,
      `<b>${pct(a.exp_return)}</b>`, `<b>${pct(a.volatility)}</b>`, fmt(a.sharpe), pct(a.cumulative, 1), pct(a.max_drawdown, 1), pct(a.min), pct(a.max), fmt(a.skew), fmt(a.kurtosis)]));
  heatTable('corrTable', ds.assets.map(esc), st.assumptions.corr, { corner: 'ρ' }); scaleKey('corrScale');

  const grid = $('histGrid'); grid.innerHTML = '';
  const lo = Math.min(...st.histograms.map(h => h.edges[0])), hi = Math.max(...st.histograms.map(h => h.edges.at(-1)));
  const top = Math.max(...st.histograms.flatMap(h => h.counts)) * 1.05;
  st.histograms.forEach((h, i) => {
    const box = document.createElement('div'); box.className = 'hist';
    box.innerHTML = `<h4>${esc(ds.assets[i])}</h4><div class="meta">σ per period ${pct(A[i].sd)} · skew ${fmt(A[i].skew)}</div><div class="chart"></div>`;
    grid.appendChild(box);
    histogram(box.querySelector('.chart'), h, { lo, hi, top, height: 180, unit: 'Periods', split: false });
  });
}

function histogram(container, h, { lo, hi, top, height = 240, unit = 'Trials', split = true, marks = [] } = {}) {
  lo = lo ?? h.edges[0]; hi = hi ?? h.edges.at(-1); top = top ?? Math.max(...h.counts) * 1.1;
  const f = frame(container, height, { l: 46, r: 10, t: marks.length ? 24 : 8, b: 26 });
  const xs = lin(lo, hi, f.x0, f.x1), ys = lin(0, top, f.y0, f.y1), total = h.counts.reduce((a, b) => a + b, 0);
  yAxis(f, ys, ticks(0, top, 4), v => v);
  h.counts.forEach((c, k) => {
    if (!c) return;
    const a = h.edges[k], b = h.edges[k + 1], x = xs(a) + 0.5, w = Math.max(1, xs(b) - xs(a) - 1);
    const bar = roundedBar(f.svg, x, f.y0, w, ys(c), `fill:${split && b <= 0 ? 'var(--div-neg)' : 'var(--accent)'}`, 3);
    hoverable(bar, () => `<div class="t">${pct(a, 1)} to ${pct(b, 1)}</div>` + trow(unit, c.toLocaleString()) + trow('Share', pct(c / total, 1)));
  });
  baseline(f, f.y0);
  ticks(lo, hi, container.clientWidth < 420 ? 3 : 6).forEach(t => el('text', { x: xs(t), y: f.y0 + 17, 'text-anchor': 'middle', style: TXT(11), text: pct(t, 0) }, f.svg));
  marks.forEach(([v, lab]) => {
    if (v < lo || v > hi) return;
    el('line', { x1: xs(v), x2: xs(v), y1: f.y1 - 4, y2: f.y0, style: 'stroke:var(--text-primary);stroke-width:1.5;stroke-dasharray:4 3;pointer-events:none' }, f.svg);
    el('text', { x: xs(v), y: f.y1 - 9, 'text-anchor': 'middle', style: TXT(11, 'var(--text-primary)', 600), text: `${lab} ${pct(v, 1)}` }, f.svg);
  });
}

// ------------------------------------------------------------------ 2 assumptions & constraints
function renderAssume() {
  const ds = S.ds, h = S.stats.historical, used = S.stats.assumptions, custom = S.assume.source === 'custom';
  const mu = custom && S.assume.mu ? S.assume.mu : h.mu, vol = custom && S.assume.vol ? S.assume.vol : h.vol;
  const inp = (cls, i, v, dis, step = 0.1) => `<input type="number" class="${cls}" data-i="${i}" step="${step}" value="${(v * 100).toFixed(2)}" ${dis ? 'disabled' : ''} aria-label="${cls} ${esc(ds.assets[i])}">`;
  table('assumeTable', ['Asset', 'Historical E(R)', 'Historical σ', 'Expected return %', 'Risk σ %', 'Min weight %', 'Max weight %', 'Used E(R)', 'Used σ'],
    ds.assets.map((a, i) => [`<b>${esc(a)}</b>`, pct(h.mu[i]), pct(h.vol[i]), inp('in-mu', i, mu[i], !custom), inp('in-vol', i, vol[i], !custom),
      inp('in-lo', i, S.cons.lower[i], false, 1), inp('in-hi', i, S.cons.upper[i], false, 1), pct(used.mu[i]), pct(used.vol[i])]));
  const tiltCustom = $('aScenario').value === 'custom';
  $('aRetMult').disabled = $('aVolMult').disabled = !tiltCustom;
  $('aNote').textContent = used.shrinkage != null ? `Shrinkage intensity ${pct(used.shrinkage, 0)}.` : '';
  renderGroups();
}
function renderGroups() {
  const ds = S.ds;
  $('groups').innerHTML = S.cons.groups.map((g, k) => `<div class="group-row" data-k="${k}">
    <input type="text" class="g-name" value="${esc(g.name)}" aria-label="Group name">
    <div class="checks">${ds.assets.map((a, i) => `<label><input type="checkbox" class="g-asset" value="${i}" ${g.assets.includes(i) ? 'checked' : ''}>${esc(a)}</label>`).join('')}</div>
    <label>Min % <input type="number" class="g-min" step="1" value="${g.min == null ? '' : (g.min * 100).toFixed(0)}"></label>
    <label>Max % <input type="number" class="g-max" step="1" value="${g.max == null ? '' : (g.max * 100).toFixed(0)}"></label>
    <button type="button" class="x g-del" aria-label="Remove group">×</button></div>`).join('') || '<p class="empty">No group limits yet.</p>';
}
function readGroups() {
  $('groups').querySelectorAll('.group-row').forEach(row => {
    const g = S.cons.groups[+row.dataset.k], v = sel => { const x = parseFloat(row.querySelector(sel).value); return Number.isFinite(x) ? x / 100 : null; };
    g.name = row.querySelector('.g-name').value; g.min = v('.g-min'); g.max = v('.g-max');
    g.assets = [...row.querySelectorAll('.g-asset:checked')].map(c => +c.value);
  });
}
function readAssume() {
  const vals = cls => [...document.querySelectorAll('#assumeTable .' + cls)].map(i => parseFloat(i.value) / 100);
  const lo = vals('in-lo'), hi = vals('in-hi');
  if ([...lo, ...hi].some(v => !Number.isFinite(v))) throw new Error('Every minimum and maximum weight needs a number.');
  S.cons.lower = lo; S.cons.upper = hi;
  S.assume.source = $('aSource').value; S.assume.method = $('aMethod').value; S.assume.covariance = $('aCov').value;
  if (S.assume.source === 'custom') {
    const mu = vals('in-mu'), vol = vals('in-vol');
    if ([...mu, ...vol].some(v => !Number.isFinite(v))) throw new Error('Every expected return and risk needs a number.');
    S.assume.mu = mu; S.assume.vol = vol;
  }
  const sc = $('aScenario').value;
  [S.assume.return_multiplier, S.assume.vol_multiplier] = sc === 'custom' ? [num('aRetMult', 1), num('aVolMult', 1)] : sc.split(',').map(Number);
  $('aRetMult').value = S.assume.return_multiplier; $('aVolMult').value = S.assume.vol_multiplier;
  readGroups();
}
async function applyAssume(thenStep) {
  readAssume();
  invalidate();
  await loadStats();
  S.portfolios = S.portfolios.filter(p => !p.auto);
  if (thenStep) { go(thenStep); await runFrontier(); } else draw();
}

// ------------------------------------------------------------------ 3 optimise
async function runFrontier() {
  S.frontier = await call('frontier', { ...common(), constraints: consSpec() });
  const keep = S.portfolios.filter(p => !p.auto);
  S.portfolios = [...Object.entries(S.frontier.portfolios).map(([name, p]) => ({ name, weights: p.weights, auto: true })), ...keep];
  S.bt = null;
  drawOptimise();
}
function drawOptimise() {
  refreshSelectors();
  const c = $('frontChart');
  if (!S.frontier) { c.innerHTML = '<p class="empty">Press “Run optimiser” to compute the frontier under the current assumptions and constraints.</p>'; $('frontLegend').innerHTML = ''; renderPortfolios(); return; }
  const F = S.frontier, ds = S.ds, narrow = c.clientWidth < 560;
  const f = frame(c, narrow ? 340 : 440, { l: 56, r: 28, t: 16, b: 46 });
  const P = S.portfolios.map(p => ({ ...p, ...describe(p.weights) }));
  const xMax = Math.max(...F.assets.map(a => a.volatility), ...P.map(p => p.vol)) * 1.1;
  const ys0 = [...F.assets.map(a => a.exp_return), ...P.map(p => p.ret)], yMax = Math.max(...ys0, 0) * 1.12, yMin = Math.min(0, ...ys0) * 1.1;
  const xs = lin(0, xMax, f.x0, f.x1), ys = lin(yMin, yMax || 0.01, f.y0, f.y1);
  yAxis(f, ys, ticks(yMin, yMax || 0.01, 5), t => pct(t, 0)); baseline(f, f.y0);
  ticks(0, xMax, narrow ? 4 : 7).forEach(t => el('text', { x: xs(t), y: f.y0 + 18, 'text-anchor': 'middle', style: TXT(), text: pct(t, 0) }, f.svg));
  axisLabels(f, 'Risk: annualised volatility (σ)', 'Expected annual return');
  el('path', { d: F.points.map((p, i) => `${i ? 'L' : 'M'}${xs(p.volatility).toFixed(1)} ${ys(p.exp_return).toFixed(1)}`).join(''), style: 'fill:none;stroke:var(--accent);stroke-width:3;stroke-linejoin:round' }, f.svg);
  const hit = el('path', { d: F.points.map((p, i) => `${i ? 'L' : 'M'}${xs(p.volatility).toFixed(1)} ${ys(p.exp_return).toFixed(1)}`).join(''), style: 'fill:none;stroke:transparent;stroke-width:16' }, f.svg);
  hit.addEventListener('mousemove', ev => {
    const [mx] = svgPoint(f, ev), p = F.points.reduce((a, b) => (Math.abs(xs(b.volatility) - mx) < Math.abs(xs(a.volatility) - mx) ? b : a));
    showTip(`<div class="t">Efficient portfolio</div>` + trow('Expected return', pct(p.exp_return)) + trow('Risk σ', pct(p.volatility)) + trow('Sharpe', fmt(p.sharpe)) +
      '<hr style="border:0;border-top:1px solid var(--grid);margin:6px 0">' + p.weights.map((w, i) => (w > 0.0005 ? trow(esc(ds.assets[i]), pct(w, 1), SERIES[i]) : '')).join(''), ev.clientX, ev.clientY);
  });
  hit.addEventListener('mouseleave', hideTip);
  F.assets.forEach((a, i) => {
    const x = xs(a.volatility), y = ys(a.exp_return), s = 5;
    const d = el('path', { d: `M${x} ${y - s}L${x + s} ${y}L${x} ${y + s}L${x - s} ${y}Z`, style: 'fill:var(--surface-1);stroke:var(--text-primary);stroke-width:1.75' }, f.svg);
    if (ds.assets.length <= 12) el('text', { x: x + 8, y: y + 4, style: TXT(11.5, 'var(--text-secondary)', 600), text: ds.assets[i] }, f.svg);
    hoverable(d, () => `<div class="t">${esc(ds.assets[i])}: 100% in one asset</div>` + trow('Expected return', pct(a.exp_return)) + trow('Risk σ', pct(a.volatility)));
  });
  P.forEach((p, k) => {
    const m = el('circle', { cx: xs(p.vol), cy: ys(p.ret), r: 7, style: `fill:${SERIES[k]};stroke:var(--surface-1);stroke-width:2` }, f.svg);
    hoverable(m, () => `<div class="t">${esc(p.name)}</div>` + trow('Expected return', pct(p.ret)) + trow('Risk σ', pct(p.vol)) + trow('Sharpe', fmt(p.sharpe)) +
      '<hr style="border:0;border-top:1px solid var(--grid);margin:6px 0">' + normalise(p.weights).map((w, i) => (w > 0.0005 ? trow(esc(ds.assets[i]), pct(w, 1)) : '')).join(''));
  });
  legend('frontLegend', [['Efficient frontier', 'var(--accent)'], ['Single asset (100%)', '', 'ring'], ...P.map((p, k) => [esc(p.name), SERIES[k], 'dot'])]);
  $('frontNotes').innerHTML = F.notes.map(n => `<div class="callout">${esc(n)}</div>`).join('');
  renderPortfolios();
}
function renderPortfolios() {
  const ds = S.ds, P = S.portfolios;
  if (!P.length) { $('portBars').innerHTML = '<p class="empty">No portfolios yet. Run the optimiser or add one.</p>'; $('portTable').innerHTML = ''; $('portLegend').innerHTML = ''; return; }
  weightBars($('portBars'), P.map(p => [p.name, normalise(p.weights)]), ds.assets, 'portLegend');
  table('portTable', ['Portfolio', ...ds.assets.map(esc), 'Sum', 'E(R)', 'Risk σ', 'Sharpe', ''],
    P.map((p, k) => {
      const d = describe(p.weights), sum = p.weights.reduce((a, b) => a + b, 0);
      const cells = p.editable
        ? p.weights.map((w, i) => `<input type="number" class="w-in" data-k="${k}" data-i="${i}" step="1" value="${(w * 100).toFixed(1)}" style="width:66px;text-align:right;min-height:28px;padding:2px 6px" aria-label="${esc(ds.assets[i])} weight">`)
        : normalise(p.weights).map(w => pct(w, 1));
      return [p.editable ? `<input type="text" class="p-name" data-k="${k}" value="${esc(p.name)}" style="width:150px;min-height:28px;padding:2px 8px" aria-label="Portfolio name">` : `<b>${esc(p.name)}</b>`,
        ...cells, Math.abs(sum - 1) > 0.0005 ? `<span class="neg">${pct(sum, 0)}</span>` : '100%', `<b>${pct(d.ret)}</b>`, `<b>${pct(d.vol)}</b>`, fmt(d.sharpe),
        `<button type="button" class="x p-del" data-k="${k}" aria-label="Remove ${esc(p.name)}">×</button>`];
    }));
}
function uniqueName(base) { let name = base, k = 2; while (S.portfolios.some(p => p.name === name)) name = `${base} (${k++})`; return name; }
async function addOptimised() {
  const obj = $('oObjective').value, needs = obj === 'target_volatility' || obj === 'target_return';
  const p = await call('optimise', { ...common(), constraints: consSpec(), objective: obj, target: needs ? num('oTarget') / 100 : null });
  const label = { target_volatility: `Max return at ${num('oTarget')}% risk`, target_return: `Min risk for ${num('oTarget')}% return`, max_sharpe: 'Maximum Sharpe',
    min_variance: 'Minimum variance', max_return: 'Maximum return', risk_parity: 'Risk parity' }[obj];
  S.portfolios.push({ name: uniqueName($('oName').value.trim() || label), weights: p.weights });
  $('oName').value = ''; S.bt = null; drawOptimise();
}
function refreshSelectors() {
  const opts = S.portfolios.map((p, k) => `<option value="${k}">${esc(p.name)}</option>`).join('');
  ['sPort', 'mPort'].forEach(id => { const cur = $(id).value; $(id).innerHTML = opts; if (cur && +cur < S.portfolios.length) $(id).value = cur; });
  const cur = $('bBench').value;
  $('bBench').innerHTML = '<option value="">None</option>' + S.portfolios.map(p => `<option>${esc(p.name)}</option>`).join('');
  if (S.portfolios.some(p => p.name === cur)) $('bBench').value = cur;
}
const needPortfolios = () => { if (!S.portfolios.length) throw new Error('Add at least one portfolio in step 3 first.'); };
const pickPortfolio = id => { needPortfolios(); return S.portfolios[+$(id).value || 0]; };

// ------------------------------------------------------------------ 4 backtest
async function runBacktest() {
  needPortfolios();
  S.bt = await call('backtest', { ...common(), portfolios: S.portfolios.map(p => ({ name: p.name, weights: p.weights })),
    benchmark: $('bBench').value || null, rebalance: $('bRebalance').value, cost_bps: num('bCost') });
  drawBacktest();
}
function drawBacktest() {
  refreshSelectors();
  $('btOut').hidden = !S.bt;
  if (!S.bt) return;
  const B = S.bt, yearIdx = B.dates.map((d, i) => i).filter(i => i > 0 && yearOf(B.dates[i]) !== yearOf(B.dates[i - 1]));
  const series = key => B.portfolios.map((p, k) => ({ name: p.name, color: SERIES[k], values: p[key] }));
  lineChart($('btWealth'), { labels: B.dates, series: series('wealth'), yFmt: v => v.toFixed(2), xTickIdx: yearIdx, xFmt: i => yearOf(B.dates[i]), legend: 'btLegend' });
  lineChart($('btDd'), { labels: B.dates, series: series('drawdown'), yFmt: v => pct(v, 0), valFmt: v => pct(v, 1), xTickIdx: yearIdx, xFmt: i => yearOf(B.dates[i]), zeroLine: true, height: 220 });
  const bench = B.portfolios.some(p => p.metrics.tracking_error != null);
  const head = ['Portfolio', 'Annual return', 'Risk σ', 'Sharpe', 'Sortino', 'Max drawdown', 'Calmar', 'VaR 95% (period)', 'CVaR 95% (period)', 'Worst period', 'Turnover / yr'];
  if (bench) head.push('Active return', 'Tracking error', 'Information ratio', 'Beta');
  table('btTable', head, B.portfolios.map(p => {
    const m = p.metrics, row = [`<b>${esc(p.name)}</b>`, `<b>${pct(m.ann_return)}</b>`, pct(m.volatility), fmt(m.sharpe), fmt(m.sortino), pct(m.max_drawdown, 1), fmt(m.calmar), pct(m.var_95), pct(m.cvar_95), pct(m.worst), pct(m.turnover, 0)];
    if (bench) row.push(m.tracking_error == null ? 'benchmark' : signed(m.active_return), pct(m.tracking_error), fmt(m.information_ratio), fmt(m.beta));
    return row;
  }));
}

// ------------------------------------------------------------------ 5 stress
async function runStress() {
  const p = pickPortfolio('sPort'), body = { ...common(), weights: p.weights };
  let res = await call('stress', body);
  let windows = res.windows.map(w => ({ name: w.name, start: w.start, end: w.end }));
  if ($('sStart').value && $('sEnd').value) {
    windows = [...windows, { name: 'Custom window', start: $('sStart').value, end: $('sEnd').value }];
    res = await call('stress', { ...body, windows });
  }
  S.stress = { ...res, portfolio: p.name, weights: p.weights, list: windows };
  S.sens = null;
  const ok = res.windows.filter(w => !w.error);
  $('shWindow').innerHTML = ok.map(w => `<option value="${esc(w.name)}">${esc(w.name)}</option>`).join('');
  if (ok.length) await runShock(); else drawStress();
}
async function runShock() {
  const w = S.stress.list.find(x => x.name === $('shWindow').value);
  const res = await call('stress', { ...common(), weights: S.stress.weights, windows: [w], shock_window: w });
  S.sens = res.sensitivity;
  drawStress();
}
function drawStress() {
  refreshSelectors();
  $('stressOut').hidden = !S.stress; $('shockCard').hidden = !S.sens;
  if (!S.stress) return;
  const W = S.stress.windows, ok = W.filter(w => !w.error);
  table('stressTable', ['Window', 'Period', 'Observations', 'Annualised return', 'Risk σ', 'Cumulative return', 'Max drawdown'],
    W.map((w, k) => (w.error ? [`<b>${esc(w.name)}</b>`, `${w.start} → ${w.end}`, w.periods, `<span class="muted">${esc(w.error)}</span>`, '', '', '']
      : [`<span class="sw-inline" style="background:${SERIES[ok.indexOf(w)]}"></span><b>${esc(w.name)}</b>`, `${w.start} → ${w.end}`, w.periods,
        `<span class="${w.mean_ann_return < 0 ? 'neg' : ''}">${pct(w.mean_ann_return, 1)}</span>`, pct(w.volatility, 1), `<span class="${w.cumulative < 0 ? 'neg' : ''}">${pct(w.cumulative, 1)}</span>`, pct(w.max_drawdown, 1)])));
  if (ok.length) {
    const n = Math.max(...ok.map(w => w.path.length));
    lineChart($('stressChart'), { labels: Array.from({ length: n }, (_, i) => i), legend: 'stressLegend',
      series: ok.map((w, k) => ({ name: w.name, short: w.name.split(' ').slice(0, 2).join(' '), color: SERIES[k], values: Array.from({ length: n }, (_, i) => w.path[i] ?? null) })),
      yFmt: v => v.toFixed(2), xFmt: i => String(i), endLabels: false, xlabel: `${S.ds.frequency} periods since the start of each window · ${S.stress.portfolio}`, tipTitle: i => `Period ${i}` });
  } else $('stressChart').innerHTML = '<p class="empty">No stress window falls inside this data. Add a custom window above.</p>';
  if (S.sens) drawShock();
}
function drawShock() {
  const X = S.sens, s = +$('shSev').value, w = normalise(S.stress.weights);
  const at = v => X.base_mu.map((b, i) => b + v * (X.shock_mu[i] - b)), port = v => dot(w, at(v));
  $('shOut').textContent = s.toFixed(2) + '×';
  $('shKpis').innerHTML = tile('Portfolio expected return', pct(port(s)), `at ${s.toFixed(2)}× severity`) + tile('Change vs base case', signed(port(s) - port(0)), `base ${pct(port(0))}`) +
    tile('Excess over risk-free', signed(port(s) - S.rf), `rf ${pct(S.rf)}`) + tile('Break-even severity', X.break_even == null ? 'n/a' : X.break_even.toFixed(2) + '×', X.break_even == null ? 'the window did not lower the return' : 'where the return reaches 0%');
  columnChart($('shBars'), { categories: S.ds.assets, series: [{ name: 'Base assumption', values: X.base_mu, color: 'var(--series-1)' }, { name: `Stressed (${s.toFixed(2)}×)`, values: at(s), color: 'var(--series-2)' }],
    yFmt: v => pct(v, 0), valFmt: v => pct(v, 1), legend: 'shLegend', rotate: true, height: 280 });
  const grid = Array.from({ length: 61 }, (_, k) => +(k * 0.05).toFixed(2));
  const g = lineChart($('shLine'), { labels: grid, series: [{ name: 'Portfolio expected return', color: 'var(--accent)', values: grid.map(port) }], yFmt: v => pct(v, 0), valFmt: v => pct(v, 2), zeroLine: true,
    xTickIdx: [0, 10, 20, 30, 40, 50, 60], xFmt: i => grid[i] + '×', tipTitle: i => `Severity ${grid[i]}×`, height: 280, endLabels: false, xlabel: 'Shock severity (1× = the window as it happened)' });
  el('circle', { cx: g.xs(s / 0.05), cy: g.ys(port(s)), r: 7, style: 'fill:var(--highlight);stroke:var(--surface-1);stroke-width:2;pointer-events:none' }, g.f.svg);
}

// ------------------------------------------------------------------ 6 simulate
async function runSim() {
  const p = pickPortfolio('mPort');
  S.mc = { ...(await call('simulate', { ...common(), weights: p.weights, years: num('mYears', 5), trials: +$('mTrials').value, method: $('mMethod').value, seed: Math.max(0, Math.floor(num('mSeed', 42))) })), portfolio: p.name };
  drawSim();
}
function drawSim() {
  refreshSelectors();
  $('simOut').hidden = !S.mc;
  if (!S.mc) return;
  const M = S.mc, ppy = S.ds.periods_per_year;
  $('simKpis').innerHTML = tile('Median annual return', pct(M.median_ann_return), `${M.years.toFixed(1)}-year horizon · ${esc(M.portfolio)}`) +
    tile('5th percentile (95% VaR)', pct(M.p05_ann_return), `CVaR ${pct(M.cvar_95_ann_return)} a year`) +
    tile('Probability of a loss', pct(M.prob_loss, 1), 'ending below the starting value') + tile('Median worst drawdown', pct(M.median_max_drawdown, 1), `1 in 20 paths: ${pct(M.p05_max_drawdown, 1)} or worse`);
  const fan = M.fan.wealth, n = fan[0].length, labels = Array.from({ length: n }, (_, i) => i);
  const mk = (i, name, color, width, dash) => ({ name, short: name.replace(' percentile', ''), color, width, dash, values: fan[i] });
  const years = Math.max(1, Math.round(M.years)), tickIdx = Array.from({ length: years + 1 }, (_, y) => Math.min(n - 1, y * ppy));
  lineChart($('simFan'), { labels, legend: 'simLegend', xTickIdx: [...new Set(tickIdx)], xFmt: i => `${Math.round(i / ppy)}y`, tipTitle: i => `Year ${(i / ppy).toFixed(2)}`, yFmt: v => v.toFixed(2),
    series: [mk(4, '95th percentile', 'var(--series-3)', 1.5, '5 4'), mk(3, '75th percentile', 'var(--series-1)', 1.5), mk(2, 'Median', 'var(--text-primary)', 2.5), mk(1, '25th percentile', 'var(--series-1)', 1.5), mk(0, '5th percentile', 'var(--series-2)', 1.5, '5 4')] });
  histogram($('simHist'), M.histogram, { marks: [[M.p05_ann_return, '5th pct'], [M.median_ann_return, 'median']] });
  table('simTable', ['Statistic', 'Value'], [['Method', { normal: 'Normal', student_t: 'Fat tails (Student-t)', bootstrap: 'Bootstrap history' }[M.method]], ['Trials', M.trials.toLocaleString()],
    ['Mean annual return', pct(M.mean_ann_return)], ['Median annual return', pct(M.median_ann_return)], ['5th to 95th percentile', `${pct(M.p05_ann_return)} to ${pct(M.p95_ann_return)}`],
    ['Average annualised volatility', pct(M.mean_volatility)], ['Worst simulated annual return', pct(M.worst_ann_return)],
    ['Ending value of 1 (5th / median / 95th)', `${M.p05_terminal.toFixed(2)} / ${M.median_terminal.toFixed(2)} / ${M.p95_terminal.toFixed(2)}`]]);
}

// ------------------------------------------------------------------ export
async function exportExcel() {
  needPortfolios();
  const res = await api(`/api/datasets/${S.ds.id}/report.xlsx`, { ...common(), constraints: consSpec(), portfolios: S.portfolios.map(p => ({ name: p.name, weights: p.weights })),
    benchmark: $('bBench').value || null, rebalance: $('bRebalance').value, cost_bps: num('bCost') });
  const url = URL.createObjectURL(await res.blob()), a = Object.assign(document.createElement('a'), { href: url, download: 'portfolio_analysis.xlsx' });
  document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
}

// ------------------------------------------------------------------ wiring
async function uploadFile(file) {
  const fd = new FormData(); fd.append('file', file); fd.append('kind', $('upKind').value); fd.append('frequency', $('upFreq').value);
  await onDataset(await (await api('/api/datasets', fd)).json());
}
async function init() {
  initTheme(draw);
  const samples = await (await api('/api/samples', null, 'GET')).json();
  $('samples').innerHTML = samples.map(s => `<button type="button" class="linkish" data-key="${s.key}">${esc(s.description)}</button>`).join(' · ');
  $('samples').addEventListener('click', ev => { const k = ev.target.dataset.key; if (k) run(null, async () => onDataset(await (await api(`/api/datasets/sample/${k}`)).json())); });
  $('pick').addEventListener('click', () => $('file').click());
  $('file').addEventListener('change', ev => { const f = ev.target.files[0]; if (f) run($('pick'), () => uploadFile(f)); ev.target.value = ''; });
  const drop = $('drop');
  ['dragenter', 'dragover'].forEach(t => drop.addEventListener(t, ev => { ev.preventDefault(); drop.classList.add('over'); }));
  ['dragleave', 'drop'].forEach(t => drop.addEventListener(t, ev => { ev.preventDefault(); drop.classList.remove('over'); }));
  drop.addEventListener('drop', ev => { const f = ev.dataTransfer.files[0]; if (f) run($('pick'), () => uploadFile(f)); });

  $('steps').addEventListener('click', ev => { const s = ev.target.dataset.step; if (s && !ev.target.disabled) go(s); });
  $('gApply').addEventListener('click', ev => run(ev.target, async () => {
    S.start = $('gStart').value || S.ds.start; S.end = $('gEnd').value || S.ds.end; S.rf = num('gRf') / 100;
    invalidate(); S.portfolios = S.portfolios.filter(p => !p.auto); await loadStats(); draw();
  }));
  $('aSource').addEventListener('change', () => {
    S.assume.source = $('aSource').value;
    if (S.assume.source === 'custom' && !S.assume.mu) { S.assume.mu = [...S.stats.historical.mu]; S.assume.vol = [...S.stats.historical.vol]; }
    renderAssume();
  });
  $('aScenario').addEventListener('change', () => {
    const v = $('aScenario').value;
    if (v !== 'custom') [$('aRetMult').value, $('aVolMult').value] = v.split(',');
    $('aRetMult').disabled = $('aVolMult').disabled = v !== 'custom';
  });
  $('addGroup').addEventListener('click', () => { readGroups(); S.cons.groups.push({ name: `Group ${S.cons.groups.length + 1}`, assets: [], min: null, max: null }); renderGroups(); });
  $('groups').addEventListener('click', ev => { if (ev.target.classList.contains('g-del')) { readGroups(); S.cons.groups.splice(+ev.target.closest('.group-row').dataset.k, 1); renderGroups(); } });
  $('aApply').addEventListener('click', ev => run(ev.target, () => applyAssume('optimise')));
  $('runFrontier').addEventListener('click', ev => run(ev.target, runFrontier));
  $('oObjective').addEventListener('change', () => { const o = $('oObjective').value; $('oTargetLab').hidden = !(o === 'target_volatility' || o === 'target_return'); });
  $('oAdd').addEventListener('click', ev => run(ev.target, addOptimised));
  $('oCustom').addEventListener('click', () => { const n = S.ds.assets.length; S.portfolios.push({ name: uniqueName('Custom'), weights: new Array(n).fill(1 / n), editable: true }); S.bt = null; drawOptimise(); });
  $('portTable').addEventListener('click', ev => { if (ev.target.classList.contains('p-del')) { S.portfolios.splice(+ev.target.dataset.k, 1); S.bt = null; drawOptimise(); } });
  $('portTable').addEventListener('change', ev => {
    const t = ev.target, p = S.portfolios[+t.dataset.k];
    if (t.classList.contains('w-in')) { p.weights[+t.dataset.i] = Math.max(0, parseFloat(t.value) || 0) / 100; S.bt = null; drawOptimise(); }
    if (t.classList.contains('p-name')) { p.name = t.value.trim() || p.name; drawOptimise(); }
  });
  $('runBacktest').addEventListener('click', ev => run(ev.target, runBacktest));
  $('runStress').addEventListener('click', ev => run(ev.target, runStress));
  $('shWindow').addEventListener('change', () => run(null, runShock));
  $('shSev').addEventListener('input', drawShock);
  $('runSim').addEventListener('click', ev => run(ev.target, runSim));
  $('exportBtn').addEventListener('click', ev => run(ev.target, exportExcel));

  // Demo links: ?demo=indices&step=backtest loads a sample and runs through to that step.
  const q = new URLSearchParams(location.search), demo = q.get('demo'), step = q.get('step');
  if (demo) await run(null, async () => {
    await onDataset(await (await api(`/api/datasets/sample/${demo}`)).json());
    if (!STEPS.includes(step) || step === 'data') return;
    go(step);
    if (step === 'assume') return;
    await runFrontier();
    await ({ backtest: runBacktest, stress: runStress, simulate: runSim }[step] || (async () => {}))();
  });
}
init().catch(fail);
