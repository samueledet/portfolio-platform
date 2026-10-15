'use strict';
/* Portfolio Construction Platform - workspace logic. Charts and tables come from core.js; numbers from the API. */

const freshAssume = () => ({ source: 'historical', method: 'arithmetic', covariance: 'sample', return_multiplier: 1, vol_multiplier: 1,
  mu: null, vol: null, corrCustom: false, corr: null, sets: [], bl: { market: null, risk_aversion: 2.5, tau: 0.05, views: [] } });
const freshMandate = () => ({ kind: 'long_only', inv_min: 0, inv_max: 1, net: 1, gross: 1.6, short_cost: 0.005, leverage: 1.5, spread: 0.01,
  bench: 'equal', benchCustom: null, benchW: null, active: null, budget: 0.03, liability: 0, funding: 1, current: '', curW: null, turnover: 0.2 });
const MANDATE_HELP = {
  long_only: 'Weights are positive and add up to 100%. This is the classic institutional allocation.',
  long_cash: 'Weights are positive; whatever is not invested sits in cash earning the risk-free rate.',
  long_short: 'Short positions are allowed. Net exposure stays fixed (100% for a 130/30 fund) and gross exposure (longs plus shorts) is capped. Shorts pay a borrow fee. Set a negative minimum weight for any asset that may be shorted.',
  market_neutral: 'Longs and shorts offset, so net exposure is 0% and the capital sits in cash earning the risk-free rate. Return comes from the spread between longs and shorts.',
  leveraged: 'Invests more than 100% by borrowing at the risk-free rate plus a spread. Used for levered risk-parity and return-seeking mandates.',
  benchmark_relative: 'Risk is tracking error against the benchmark and return is active return over it. The usual mandate for an external manager with a tracking-error budget.',
  surplus: 'Risk and return are measured against the liabilities (assets minus liabilities). Pick the column that best tracks the liabilities, such as a long-bond index, and the funding ratio. The usual mandate for a pension fund or insurer.',
};
const S = {
  ds: null, step: 'data', rf: 0, start: null, end: null, assume: freshAssume(), mandate: freshMandate(), cons: { lower: [], upper: [], groups: [] },
  stats: null, frontier: null, portfolios: [], bt: null, attr: null, stress: null, sens: null, mc: null, spend: null, fx: null,
};
const STEPS = ['data', 'assume', 'optimise', 'backtest', 'stress', 'simulate', 'spending', 'currency'];
const num = (id, fallback = 0) => { const v = parseFloat($(id).value); return Number.isFinite(v) ? v : fallback; };
const yearOf = d => d.slice(0, 4);
const HR = '<hr style="border:0;border-top:1px solid var(--grid);margin:6px 0">';

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
async function download(path, body, filename) {
  const res = await api(path, body);
  const url = URL.createObjectURL(await res.blob()), a = Object.assign(document.createElement('a'), { href: url, download: filename });
  document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
}
const common = () => ({ assumptions: assumeSpec(), mandate: mandateSpec(), rf: S.rf, start: S.start, end: S.end });
const SHORTING = ['long_short', 'market_neutral'];
const fullInv = () => ['long_only', 'benchmark_relative', 'surplus'].includes(S.mandate.kind) || (S.mandate.kind === 'long_short' && S.mandate.net === 1);
function mandateInfo() {
  const M = S.mandate, n = S.ds.assets.length;
  const ref = M.kind === 'benchmark_relative' ? (M.benchW || new Array(n).fill(1 / n)) : M.kind === 'surplus' ? S.ds.assets.map((_, i) => (i === M.liability ? 1 / M.funding : 0)) : null;
  return { full: fullInv(), ref, relative: M.kind === 'benchmark_relative' ? 'benchmark' : M.kind === 'surplus' ? 'surplus' : null,
    spread: M.kind === 'leveraged' ? M.spread : 0, shortCost: SHORTING.includes(M.kind) ? M.short_cost : 0 };
}
function mandateSpec() {
  const M = S.mandate, o = { kind: M.kind };
  if (M.kind === 'long_cash') Object.assign(o, { invested_min: M.inv_min, invested_max: M.inv_max });
  if (M.kind === 'long_short') Object.assign(o, { net: M.net, gross: M.gross, short_cost: M.short_cost });
  if (M.kind === 'market_neutral') Object.assign(o, { gross: M.gross, short_cost: M.short_cost });
  if (M.kind === 'leveraged') Object.assign(o, { leverage: M.leverage, borrow_spread: M.spread });
  if (M.kind === 'benchmark_relative') Object.assign(o, { benchmark: M.benchW, active_limit: M.active, risk_budget: M.budget });
  if (M.kind === 'surplus') Object.assign(o, { liability: M.liability, funding_ratio: M.funding, risk_budget: M.budget });
  if (M.curW) Object.assign(o, { current: M.curW, turnover_max: M.turnover });
  return o;
}
function assumeSpec() {
  const a = S.assume, o = { source: a.source, method: a.method, covariance: a.covariance, return_multiplier: a.return_multiplier, vol_multiplier: a.vol_multiplier };
  const corr = a.corrCustom && a.corr ? a.corr : null;
  if (a.source === 'custom') { o.mu = a.mu; o.vol = a.vol; }
  if (a.source === 'blend') o.sources = a.sets.map(s => ({ name: s.name, weight: s.weight, mu: s.mu, vol: s.vol, ...(corr ? { corr } : {}) }));
  else if (corr) o.corr = corr;
  if (a.source === 'black_litterman') Object.assign(o, { market_weights: a.bl.market, views: a.bl.views, risk_aversion: a.bl.risk_aversion, tau: a.bl.tau });
  if (a.source === 'historical' && corr) o.source = 'custom';       // historical returns and risk, custom correlations
  return o;
}
const consSpec = () => ({ lower: S.cons.lower, upper: S.cons.upper,
  groups: S.cons.groups.filter(g => g.assets.length && (g.min != null || g.max != null)).map(g => ({ name: g.name, assets: g.assets, min: g.min, max: g.max })) });
const normalise = w => { const s = w.reduce((a, b) => a + b, 0) || 1; return w.map(x => x / s); };
const shown = w => (fullInv() ? normalise(w) : w).map(v => (Math.abs(v) < 5e-5 ? 0 : v));   // no "-0.0%"
/* Expected return and risk of a weight vector under the current assumptions and mandate (mirrors the engine). */
function describe(w) {
  const a = S.stats.assumptions, I = mandateInfo(), x = I.full ? normalise(w) : [...w];
  const net = x.reduce((s, v) => s + v, 0), cash = 1 - net, short = x.reduce((s, v) => s + Math.max(-v, 0), 0);
  const ret = dot(x, a.mu) + cash * (cash >= 0 ? S.rf : S.rf + I.spread) - I.shortCost * short, vol = Math.sqrt(Math.max(0, quad(x, a.cov)));
  const out = { ret, vol, sharpe: vol > 1e-12 ? (ret - S.rf) / vol : null, net, gross: x.reduce((s, v) => s + Math.abs(v), 0), cash, short, risk: vol, reward: ret };
  if (I.ref) {
    const d = x.map((v, i) => v - I.ref[i]), te = Math.sqrt(Math.max(0, quad(d, a.cov))), active = ret - dot(I.ref, a.mu);
    Object.assign(out, { te, active, ir: te > 1e-12 ? active / te : null, risk: te, reward: active });
  }
  return out;
}
const plain = ps => ps.map(p => ({ name: p.name, weights: p.weights }));

// ------------------------------------------------------------------ navigation
function go(step) {
  S.step = step;
  STEPS.forEach(s => { $('p-' + s).hidden = s !== step; });
  $('steps').querySelectorAll('button').forEach(b => b.classList.toggle('on', b.dataset.step === step));
  draw();
}
function draw() {
  if (!S.ds || !S.stats) return;
  ({ data: drawData, assume: renderAssume, optimise: drawOptimise, backtest: drawBacktest, stress: drawStress, simulate: drawSim, spending: drawSpend, currency: drawFx })[S.step]();
}
function invalidate() { Object.assign(S, { frontier: null, bt: null, attr: null, stress: null, sens: null, mc: null, spend: null }); }

// ------------------------------------------------------------------ 1 data
async function onDataset(ds) {
  const n = ds.assets.length;
  Object.assign(S, { ds, rf: num('gRf') / 100, start: ds.start, end: ds.end, portfolios: [], stats: null, fx: null, assume: freshAssume(), mandate: freshMandate() });
  S.cons = { lower: new Array(n).fill(0), upper: new Array(n).fill(1), groups: [] };
  invalidate();
  $('gStart').value = ds.start; $('gEnd').value = ds.end;
  $('gStart').min = $('gEnd').min = ds.start; $('gStart').max = $('gEnd').max = ds.end;
  ['settings', 'dataOut', 'exportBtn', 'reportBtn', 'dsChip'].forEach(id => { $(id).hidden = false; });
  $('dsChip').textContent = `${ds.filename} · ${n} assets · ${ds.frequency}`;
  $('steps').querySelectorAll('button').forEach(b => { b.disabled = false; });
  ['aSource', 'aMethod', 'aCov'].forEach(id => { $(id).selectedIndex = 0; });
  $('aScenario').value = '1,1'; $('aRetMult').value = 1; $('aVolMult').value = 1; $('aCorrCustom').checked = false;
  const opts = ds.assets.map((a, i) => `<option value="${i}">${esc(a)}</option>`).join('');
  $('fAsset').innerHTML = opts; $('fCcy').innerHTML = opts; $('fAsset').value = 0; $('fCcy').value = n - 1;
  $('mLiab').innerHTML = opts;
  await loadStats();
  const vols = S.stats.historical.vol; S.mandate.liability = vols.indexOf(Math.min(...vols));   // least volatile column as a first guess
  go('data');
}
async function loadStats() { S.stats = await call('statistics', common()); }

function drawData() {
  const ds = S.ds, st = S.stats, A = st.assets;
  $('gHint').textContent = `${st.observations.toLocaleString()} ${ds.frequency} returns from ${st.start} to ${st.end} are in use.`;
  const best = A.reduce((a, b, i) => (b.exp_return > A[a].exp_return ? i : a), 0), calm = A.reduce((a, b, i) => (b.volatility < A[a].volatility ? i : a), 0);
  $('dataKpis').innerHTML = tile('Assets', ds.assets.length, `${ds.kind} uploaded, ${ds.frequency}`) +
    tile('Observations', st.observations.toLocaleString(), `${st.start} to ${st.end}`) +
    tile('Highest historical return', pct(A[best].exp_return), esc(ds.assets[best])) + tile('Lowest risk', pct(A[calm].volatility), esc(ds.assets[calm]));
  if (ds.warnings.length) $('dataKpis').insertAdjacentHTML('beforeend', `<div class="kpi" style="grid-column:1/-1"><div class="label">Data notes</div><div class="detail">${ds.warnings.map(esc).join(' ')}</div></div>`);
  const g = st.growth, yearIdx = g.dates.map((d, i) => i).filter(i => i > 0 && yearOf(g.dates[i]) !== yearOf(g.dates[i - 1]));
  lineChart($('growthChart'), { labels: g.dates, series: ds.assets.map((a, i) => ({ name: a, color: SERIES[i], values: g.values[i] })),
    yFmt: v => v.toFixed(0), valFmt: v => v.toFixed(1), xTickIdx: yearIdx, xFmt: i => yearOf(g.dates[i]), legend: 'growthLegend' });
  const method = S.assume.method === 'compound' ? '(1 + mean)^periods − 1' : 'mean × periods per year';
  $('statsSub').textContent = `Historical figures. Return = ${method}. Risk = standard deviation × √${ds.periods_per_year}. Sharpe uses the risk-free rate above.`;
  table('statsTable', ['Asset', 'Return', 'Risk σ', 'Sharpe', 'Cumulative', 'Max drawdown', 'Worst period', 'Best period', 'Skew', 'Excess kurtosis'],
    A.map((a, i) => [`<b>${esc(ds.assets[i])}</b>${ds.names[i] !== ds.assets[i] ? `<span class="cls">${esc(ds.names[i])}</span>` : ''}`,
      `<b>${pct(a.exp_return)}</b>`, `<b>${pct(a.volatility)}</b>`, fmt(a.sharpe), pct(a.cumulative, 1), pct(a.max_drawdown, 1), pct(a.min), pct(a.max), fmt(a.skew), fmt(a.kurtosis)]));
  heatTable('corrTable', ds.assets.map(esc), st.historical.corr, { corner: 'ρ' }); scaleKey('corrScale');
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
function fanChart(container, values, { legendId, xFmt, tipTitle, yFmt, tickIdx }) {
  const n = values[0].length, mk = (i, name, color, width, dash) => ({ name, short: name.replace(' percentile', ''), color, width, dash, values: values[i] });
  lineChart(container, { labels: Array.from({ length: n }, (_, i) => i), legend: legendId, xTickIdx: tickIdx, xFmt, tipTitle, yFmt,
    series: [mk(4, '95th percentile', 'var(--series-3)', 1.5, '5 4'), mk(3, '75th percentile', 'var(--series-1)', 1.5), mk(2, 'Median', 'var(--text-primary)', 2.5),
      mk(1, '25th percentile', 'var(--series-1)', 1.5), mk(0, '5th percentile', 'var(--series-2)', 1.5, '5 4')] });
}

// ------------------------------------------------------------------ 2 assumptions & constraints
const HELP = {
  historical: 'Expected returns and risk are estimated from your uploaded data.',
  custom: 'Type your own expected return and risk for each asset. Cells start from the historical values.',
  blend: 'Enter forecasts from several sources, such as two asset managers or a house view. They are averaged using the weights you give each set.',
  black_litterman: 'Starts from the returns implied by market weights (the equilibrium), then adjusts them for your views. A view held with high confidence moves the estimate further.',
};
const cell = (kind, i, v, attrs = '') => `<input type="number" class="${kind}" data-i="${i}" step="0.1" value="${Number.isFinite(v) ? (v * 100).toFixed(2) : ''}" ${attrs} aria-label="${kind} ${esc(S.ds.assets[i])}">`;

function renderAssume() {
  const ds = S.ds, a = S.assume, h = S.stats.historical, used = S.stats.assumptions, n = ds.assets.length;
  $('aSource').value = a.source; $('aCorrCustom').checked = a.corrCustom;
  $('aHelp').textContent = HELP[a.source];
  const head = ['Asset', 'Historical E(R)', 'Historical σ'], extra = [];
  if (a.source === 'custom') { head.push('Expected return %', 'Risk σ %'); extra.push(i => [cell('in-mu', i, a.mu[i]), cell('in-vol', i, a.vol[i])]); }
  if (a.source === 'blend') a.sets.forEach((s, k) => { head.push(`${esc(s.name)} E(R) %`, `${esc(s.name)} σ %`); extra.push(i => [cell('set-mu', i, s.mu[i], `data-k="${k}"`), cell('set-vol', i, s.vol[i], `data-k="${k}"`)]); });
  if (a.source === 'black_litterman') { head.push('Market weight %', 'Equilibrium E(R)'); extra.push(i => [cell('bl-mkt', i, a.bl.market[i]), used.prior_mu ? pct(used.prior_mu[i]) : '–']); }
  if (S.mandate.kind === 'benchmark_relative' && S.mandate.bench === 'custom') {
    const bw = S.mandate.benchCustom || new Array(n).fill(1 / n);
    head.push('Benchmark %'); extra.push(i => [cell('bm-w', i, bw[i])]);
  }
  head.push('Min weight %', 'Max weight %', 'Used E(R)', 'Used σ');
  table('assumeTable', head, ds.assets.map((name, i) => [`<b>${esc(name)}</b>`, pct(h.mu[i]), pct(h.vol[i]), ...extra.flatMap(f => f(i)),
    cell('in-lo', i, S.cons.lower[i], 'step="1"'), cell('in-hi', i, S.cons.upper[i], 'step="1"'), `<b>${pct(used.mu[i])}</b>`, pct(used.vol[i])]));

  $('setsBar').innerHTML = a.source !== 'blend' ? '' : `<div class="sets">${a.sets.map((s, k) => `<span class="set" data-k="${k}">
      <input type="text" class="set-name" value="${esc(s.name)}" aria-label="Forecast set name"> weight <input type="number" class="set-w" value="${s.weight}" min="0" step="0.5" aria-label="Weight">
      ${a.sets.length > 1 ? '<button type="button" class="x set-del" aria-label="Remove forecast set">×</button>' : ''}</span>`).join('')}
      <button type="button" class="btn ghost" id="addSet">Add forecast set</button></div>`;

  const assetOpts = sel => ds.assets.map((x, i) => `<option value="${i}" ${i === sel ? 'selected' : ''}>${esc(x)}</option>`).join('');
  $('blBox').innerHTML = a.source !== 'black_litterman' ? '' : `<div class="controls inner" style="margin-top:14px">
      <label>Risk aversion <input id="blRA" type="number" step="0.5" min="0.1" value="${a.bl.risk_aversion}"></label>
      <label>Uncertainty of the equilibrium (τ) <input id="blTau" type="number" step="0.01" min="0.001" value="${a.bl.tau}"></label></div>
    <h3>Views</h3>${a.bl.views.map((v, k) => `<div class="group-row view-row" data-k="${k}">
      <select class="v-type" aria-label="View type"><option value="absolute" ${v.type === 'absolute' ? 'selected' : ''}>returns</option><option value="relative" ${v.type === 'relative' ? 'selected' : ''}>beats</option></select>
      <select class="v-a" aria-label="Asset">${assetOpts(v.type === 'relative' ? v.long : v.asset)}</select>
      <span class="v-word">${v.type === 'relative' ? 'beats' : 'returns'}</span>
      ${v.type === 'relative' ? `<select class="v-b" aria-label="Other asset">${assetOpts(v.short)}</select><span>by</span>` : ''}
      <label><input type="number" class="v-val" step="0.25" value="${(v.value * 100).toFixed(2)}">% a year</label>
      <label>confidence <input type="number" class="v-conf" step="5" min="1" max="100" value="${(v.confidence * 100).toFixed(0)}">%</label>
      <button type="button" class="x v-del" aria-label="Remove view">×</button></div>`).join('') || '<p class="empty">No views yet: the equilibrium returns are used as they are.</p>'}
    <button type="button" class="btn ghost" id="addView">Add a view</button>`;

  const C = a.corrCustom && a.corr ? a.corr : h.corr;
  $('corrEdit').innerHTML = `<thead><tr><th>ρ</th>${ds.assets.map(x => `<th>${esc(x)}</th>`).join('')}</tr></thead><tbody>` + ds.assets.map((x, i) =>
    `<tr><td class="tkr">${esc(x)}</td>${ds.assets.map((_, j) => (i === j ? '<td class="diag">1.00</td>' : i > j
      ? `<td><input type="number" class="c-in" data-i="${i}" data-j="${j}" min="-1" max="1" step="0.05" value="${C[i][j].toFixed(2)}" ${a.corrCustom ? '' : 'disabled'} aria-label="Correlation ${esc(x)} and ${esc(ds.assets[j])}"></td>`
      : `<td class="muted">${fmt(C[i][j])}</td>`)).join('')}</tr>`).join('') + '</tbody>';

  const tiltCustom = $('aScenario').value === 'custom';
  $('aRetMult').disabled = $('aVolMult').disabled = !tiltCustom;
  const notes = [...(used.notes || [])];
  if (used.shrinkage != null) notes.push(`Covariance shrinkage intensity: ${pct(used.shrinkage, 0)}.`);
  $('aNotes').innerHTML = notes.map(t => `<div class="callout">${esc(t)}</div>`).join('');
  renderMandate();
  renderGroups();
}
function renderMandate() {
  const M = S.mandate, pct0 = v => +(v * 100).toFixed(2);
  $('mKind').value = M.kind;
  document.querySelectorAll('#mandateBox [data-for]').forEach(l => { l.hidden = !l.dataset.for.split(' ').includes(M.kind); });
  [['mInvMin', M.inv_min], ['mInvMax', M.inv_max], ['mNet', M.net], ['mGross', M.gross], ['mShortCost', M.short_cost], ['mLev', M.leverage],
    ['mSpread', M.spread], ['mFund', M.funding], ['mTurn', M.turnover]].forEach(([id, v]) => { $(id).value = pct0(v); });
  $('mActive').value = M.active == null ? '' : pct0(M.active);
  $('mBudget').value = M.budget == null ? '' : pct0(M.budget);
  $('mBudgetText').textContent = M.kind === 'surplus' ? 'Surplus-risk budget (%, blank = none)' : 'Tracking-error budget (%, blank = none)';
  const ports = S.portfolios.map(p => `<option value="p:${esc(p.name)}">${esc(p.name)}</option>`).join('');
  $('mBench').innerHTML = '<option value="equal">Equal weight</option><option value="custom">Custom (type in the table above)</option>' + ports;
  $('mBench').value = [...$('mBench').options].some(o => o.value === M.bench) ? M.bench : 'equal';
  $('mLiab').value = M.liability;
  $('mCur').innerHTML = '<option value="">No turnover limit</option>' + S.portfolios.map(p => `<option>${esc(p.name)}</option>`).join('');
  $('mCur').value = S.portfolios.some(p => p.name === M.current) ? M.current : '';
  $('mHelp').textContent = MANDATE_HELP[M.kind];
}
/* Switching mandate: reset per-asset limits to sensible defaults for shorting or long-only. */
function setMandateKind(kind) {
  const M = S.mandate, n = S.ds.assets.length;
  M.kind = kind;
  M.budget = kind === 'benchmark_relative' ? (M.budget ?? 0.03) : kind === 'surplus' ? null : M.budget;
  if (kind === 'market_neutral') { M.gross = Math.max(M.gross, 2); S.cons.lower = new Array(n).fill(-0.5); S.cons.upper = new Array(n).fill(0.5); }
  else if (kind === 'long_short') { if (S.cons.lower.every(v => v >= 0)) S.cons.lower = new Array(n).fill(-0.3); S.cons.upper = S.cons.upper.map(v => Math.max(v, 0.5)); }
  else { S.cons.lower = S.cons.lower.map(v => Math.max(0, v)); if (S.cons.upper.every(v => v <= 0.5)) S.cons.upper = new Array(n).fill(1); }
}
function renderGroups() {
  const ds = S.ds;
  $('groups').innerHTML = S.cons.groups.map((g, k) => `<div class="group-row grp" data-k="${k}">
    <input type="text" class="g-name" value="${esc(g.name)}" aria-label="Group name">
    <div class="checks">${ds.assets.map((a, i) => `<label><input type="checkbox" class="g-asset" value="${i}" ${g.assets.includes(i) ? 'checked' : ''}>${esc(a)}</label>`).join('')}</div>
    <label>Min % <input type="number" class="g-min" step="1" value="${g.min == null ? '' : (g.min * 100).toFixed(0)}"></label>
    <label>Max % <input type="number" class="g-max" step="1" value="${g.max == null ? '' : (g.max * 100).toFixed(0)}"></label>
    <button type="button" class="x g-del" aria-label="Remove group">×</button></div>`).join('') || '<p class="empty">No group limits yet.</p>';
}
/* Read every input on the assumptions step into state. strict = reject blanks (used on Apply). */
function capture(strict) {
  const a = S.assume, n = S.ds.assets.length;
  const col = (cls, k) => [...document.querySelectorAll(`#assumeTable .${cls}${k == null ? '' : `[data-k="${k}"]`}`)].map(i => parseFloat(i.value) / 100);
  const need = (arr, what) => { if (strict && arr.some(v => !Number.isFinite(v))) throw new Error(`Every ${what} needs a number.`); return arr.map(v => (Number.isFinite(v) ? v : 0)); };
  const lo = col('in-lo'), hi = col('in-hi');
  if (lo.length === n) { S.cons.lower = need(lo, 'minimum weight'); S.cons.upper = need(hi, 'maximum weight'); }
  if (a.source === 'custom' && col('in-mu').length === n) { a.mu = need(col('in-mu'), 'expected return'); a.vol = need(col('in-vol'), 'risk figure'); }
  if (a.source === 'blend') document.querySelectorAll('#setsBar .set').forEach(elm => {
    const k = +elm.dataset.k, s = a.sets[k];
    s.name = elm.querySelector('.set-name').value.trim() || s.name; s.weight = Math.max(0, parseFloat(elm.querySelector('.set-w').value) || 0);
    if (col('set-mu', k).length === n) { s.mu = need(col('set-mu', k), 'expected return'); s.vol = need(col('set-vol', k), 'risk figure'); }
  });
  if (a.source === 'black_litterman' && $('blRA')) {
    if (col('bl-mkt').length === n) a.bl.market = need(col('bl-mkt'), 'market weight');
    a.bl.risk_aversion = num('blRA', 2.5); a.bl.tau = num('blTau', 0.05);
    a.bl.views = [...document.querySelectorAll('#blBox .view-row')].map(r => {
      const type = r.querySelector('.v-type').value, A = +r.querySelector('.v-a').value, value = parseFloat(r.querySelector('.v-val').value) / 100;
      const confidence = Math.min(1, Math.max(0.01, (parseFloat(r.querySelector('.v-conf').value) || 50) / 100));
      if (strict && !Number.isFinite(value)) throw new Error('Every view needs a return figure.');
      const B = r.querySelector('.v-b') ? +r.querySelector('.v-b').value : (A + 1) % n;
      return type === 'relative' ? { type, long: A, short: B, value: value || 0, confidence } : { type, asset: A, value: value || 0, confidence };
    });
  }
  if (a.corrCustom && a.corr) document.querySelectorAll('#corrEdit .c-in').forEach(inp => {
    const v = parseFloat(inp.value), i = +inp.dataset.i, j = +inp.dataset.j;
    if (strict && !(v >= -1 && v <= 1)) throw new Error('Correlations must be numbers between −1 and 1.');
    if (Number.isFinite(v)) a.corr[i][j] = a.corr[j][i] = Math.max(-1, Math.min(1, v));
  });
  const M = S.mandate, pv = (id, d) => { const v = parseFloat($(id).value); if (strict && !Number.isFinite(v) && d === undefined) throw new Error('Every mandate setting needs a number.'); return Number.isFinite(v) ? v / 100 : d; };
  Object.assign(M, { inv_min: pv('mInvMin', M.inv_min), inv_max: pv('mInvMax', M.inv_max), net: pv('mNet', M.net), gross: pv('mGross', M.gross),
    short_cost: pv('mShortCost', M.short_cost), leverage: pv('mLev', M.leverage), spread: pv('mSpread', M.spread), funding: pv('mFund', M.funding),
    turnover: pv('mTurn', M.turnover), active: $('mActive').value === '' ? null : pv('mActive', null), budget: $('mBudget').value === '' ? null : pv('mBudget', null), bench: $('mBench').value || 'equal',
    liability: +$('mLiab').value || 0, current: $('mCur').value });
  if (col('bm-w').length === n) M.benchCustom = need(col('bm-w'), 'benchmark weight');
  if (strict) {                     // freeze the benchmark and the current portfolio so later re-runs do not move them
    const named = name => S.portfolios.find(p => p.name === name);
    M.benchW = M.bench === 'custom' ? normalise(M.benchCustom || new Array(n).fill(1 / n)) : M.bench.startsWith('p:') && named(M.bench.slice(2))
      ? normalise(named(M.bench.slice(2)).weights) : new Array(n).fill(1 / n);
    if (M.benchW.some(v => v < 0)) throw new Error('Benchmark weights cannot be negative.');
    M.curW = M.current && named(M.current) ? shown(named(M.current).weights) : null;
  }
  a.method = $('aMethod').value; a.covariance = $('aCov').value;
  const sc = $('aScenario').value;
  [a.return_multiplier, a.vol_multiplier] = sc === 'custom' ? [num('aRetMult', 1), num('aVolMult', 1)] : sc.split(',').map(Number);
  document.querySelectorAll('#groups .grp').forEach(row => {
    const g = S.cons.groups[+row.dataset.k], v = sel => { const x = parseFloat(row.querySelector(sel).value); return Number.isFinite(x) ? x / 100 : null; };
    g.name = row.querySelector('.g-name').value; g.min = v('.g-min'); g.max = v('.g-max');
    g.assets = [...row.querySelectorAll('.g-asset:checked')].map(c => +c.value);
  });
}
function prepareSource(source) {            // give a newly chosen source sensible starting values
  const a = S.assume, h = S.stats.historical, n = S.ds.assets.length;
  a.source = source;
  if (source === 'custom' && !a.mu) { a.mu = [...h.mu]; a.vol = [...h.vol]; }
  if (source === 'blend' && !a.sets.length) a.sets = ['Forecast set 1', 'Forecast set 2'].map(name => ({ name, weight: 1, mu: [...h.mu], vol: [...h.vol] }));
  if (source === 'black_litterman' && !a.bl.market) a.bl.market = new Array(n).fill(1 / n);
}
async function applyAssume(thenStep) {
  capture(true);
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
  S.bt = null; S.attr = null;
  drawOptimise();
}
function drawOptimise() {
  refreshSelectors();
  objectiveChanged();
  const c = $('frontChart');
  if (!S.frontier) { c.innerHTML = '<p class="empty">Press “Run optimiser” to compute the frontier under the current assumptions and constraints.</p>'; $('frontLegend').innerHTML = ''; $('frontNotes').innerHTML = ''; renderPortfolios(); return; }
  const F = S.frontier, ds = S.ds, narrow = c.clientWidth < 560;
  const f = frame(c, narrow ? 340 : 440, { l: 56, r: 28, t: 16, b: 46 });
  const P = S.portfolios.map(p => ({ ...p, ...describe(p.weights) }));
  const xMax = Math.max(...F.points.map(p => p.risk), ...F.assets.map(a => a.risk), ...P.map(p => p.risk), 0.01) * 1.1;
  const ys0 = [...F.points.map(p => p.reward), ...F.assets.map(a => a.reward), ...P.map(p => p.reward)];
  const yMax = Math.max(...ys0, 0) * 1.12 || 0.01, yMin = Math.min(0, ...ys0) * 1.12;
  const xs = lin(0, xMax, f.x0, f.x1), ys = lin(yMin, yMax, f.y0, f.y1);
  yAxis(f, ys, ticks(yMin, yMax, 5), t => pct(t, 0)); baseline(f, f.y0);
  ticks(0, xMax, narrow ? 4 : 7).forEach(t => el('text', { x: xs(t), y: f.y0 + 18, 'text-anchor': 'middle', style: TXT(), text: pct(t, 0) }, f.svg));
  axisLabels(f, F.axes.x, F.axes.y);
  const d = F.points.map((p, i) => `${i ? 'L' : 'M'}${xs(p.risk).toFixed(1)} ${ys(p.reward).toFixed(1)}`).join('');
  el('path', { d, style: 'fill:none;stroke:var(--accent);stroke-width:3;stroke-linejoin:round' }, f.svg);
  const hit = el('path', { d, style: 'fill:none;stroke:transparent;stroke-width:16' }, f.svg);
  const weightsTip = w => shown(w).map((x, i) => (Math.abs(x) > 0.0005 ? trow(esc(ds.assets[i]), pct(x, 1), SERIES[i]) : '')).join('');
  const rel = F.mandate.relative, relRows = q => (rel ? trow(rel === 'benchmark' ? 'Active return' : 'Surplus return', pct(q.reward)) + trow(rel === 'benchmark' ? 'Tracking error' : 'Surplus risk', pct(q.risk)) : '');
  const expo = q => (F.mandate.full_investment && q.gross <= 1.0005 ? '' : trow('Net / gross', `${pct(q.net, 0)} / ${pct(q.gross, 0)}`) + trow('Cash', pct(q.cash, 0)));
  hit.addEventListener('mousemove', ev => {
    const [mx] = svgPoint(f, ev), p = F.points.reduce((a, b) => (Math.abs(xs(b.risk) - mx) < Math.abs(xs(a.risk) - mx) ? b : a));
    showTip('<div class="t">Efficient portfolio</div>' + trow('Expected return', pct(p.exp_return)) + trow('Volatility σ', pct(p.volatility)) + relRows(p) + expo(p) + HR + weightsTip(p.weights), ev.clientX, ev.clientY);
  });
  hit.addEventListener('mouseleave', hideTip);
  F.assets.forEach((a, i) => {
    const x = xs(a.risk), y = ys(a.reward), s = 5;
    const dm = el('path', { d: `M${x} ${y - s}L${x + s} ${y}L${x} ${y + s}L${x - s} ${y}Z`, style: 'fill:var(--surface-1);stroke:var(--text-primary);stroke-width:1.75' }, f.svg);
    if (ds.assets.length <= 12) el('text', { x: x + 8, y: y + 4, style: TXT(11.5, 'var(--text-secondary)', 600), text: ds.assets[i] }, f.svg);
    hoverable(dm, () => `<div class="t">${esc(ds.assets[i])}: 100% in one asset</div>` + trow('Expected return', pct(a.exp_return)) + trow('Volatility σ', pct(a.volatility)) + relRows(a));
  });
  P.forEach((p, k) => {
    const m = el('circle', { cx: xs(p.risk), cy: ys(p.reward), r: 7, style: `fill:${SERIES[k]};stroke:var(--surface-1);stroke-width:2` }, f.svg);
    hoverable(m, () => `<div class="t">${esc(p.name)}</div>` + trow('Expected return', pct(p.ret)) + trow('Volatility σ', pct(p.vol)) + trow('Sharpe', fmt(p.sharpe)) + relRows(p) + expo(p) + HR + weightsTip(p.weights));
  });
  legend('frontLegend', [['Efficient frontier', 'var(--accent)'], ['Single asset (100%)', '', 'ring'], ...P.map((p, k) => [esc(p.name), SERIES[k], 'dot'])]);
  $('frontLegend').insertAdjacentHTML('afterbegin', `<span class="item"><b>${esc(F.mandate.label)}</b></span>`);
  $('frontNotes').innerHTML = F.notes.map(n => `<div class="callout">${esc(n)}</div>`).join('');
  renderPortfolios();
}
function renderPortfolios() {
  const ds = S.ds, P = S.portfolios;
  if (!P.length) { $('portBars').innerHTML = '<p class="empty">No portfolios yet. Run the optimiser or add one.</p>'; $('portTable').innerHTML = ''; $('portLegend').innerHTML = ''; return; }
  const I = mandateInfo(), plainLong = I.full && P.every(p => p.weights.every(v => v >= -1e-9));
  if (plainLong) weightBars($('portBars'), P.map(p => [p.name, normalise(p.weights)]), ds.assets, 'portLegend');
  else columnChart($('portBars'), { categories: ds.assets, series: P.map((p, k) => ({ name: p.name, values: shown(p.weights), color: SERIES[k] })),
    yFmt: v => pct(v, 0), valFmt: v => pct(v, 1), legend: 'portLegend', rotate: ds.assets.length > 6, height: 280 });
  const relHead = I.relative ? (I.relative === 'benchmark' ? ['Active return', 'Tracking error', 'Info ratio'] : ['Surplus return', 'Surplus risk', 'Ratio']) : [];
  const expoHead = plainLong ? ['Sum'] : ['Net', 'Gross', 'Cash'];
  table('portTable', ['Portfolio', ...ds.assets.map(esc), ...expoHead, 'E(R)', 'Risk σ', 'Sharpe', ...relHead, ''],
    P.map((p, k) => {
      const d = describe(p.weights), sum = p.weights.reduce((a, b) => a + b, 0);
      const cells = p.editable
        ? p.weights.map((w, i) => `<input type="number" class="w-in" data-k="${k}" data-i="${i}" step="1" value="${(w * 100).toFixed(1)}" style="width:66px;text-align:right;min-height:28px;padding:2px 6px" aria-label="${esc(ds.assets[i])} weight">`)
        : shown(p.weights).map(w => `<span class="${w < -1e-9 ? 'neg' : ''}">${pct(w, 1)}</span>`);
      return [p.editable ? `<input type="text" class="p-name" data-k="${k}" value="${esc(p.name)}" style="width:150px;min-height:28px;padding:2px 8px" aria-label="Portfolio name">` : `<b>${esc(p.name)}</b>`,
        ...cells, ...(plainLong ? [Math.abs(sum - 1) > 0.0005 && !p.editable ? '100%' : Math.abs(sum - 1) > 0.0005 ? `<span class="neg">${pct(sum, 0)}</span>` : '100%']
          : [pct(d.net, 0), pct(d.gross, 0), pct(d.cash, 0)]),
        `<b>${pct(d.ret)}</b>`, `<b>${pct(d.vol)}</b>`, fmt(d.sharpe), ...(I.relative ? [pct(d.active), pct(d.te), fmt(d.ir)] : []),
        `<button type="button" class="x p-del" data-k="${k}" aria-label="Remove ${esc(p.name)}">×</button>`];
    }));
}
function uniqueName(base) { let name = base, k = 2; while (S.portfolios.some(p => p.name === name)) name = `${base} (${k++})`; return name; }
let TARGETS = { target_volatility: 'Risk limit (% a year)', target_return: 'Target return (% a year)', min_cvar: 'Minimum return (% a year, optional)' };
function objectiveLabels() {
  const rel = mandateInfo().relative, risk = rel === 'benchmark' ? 'tracking error' : rel === 'surplus' ? 'surplus risk' : 'risk', ret = rel === 'benchmark' ? 'active return' : rel === 'surplus' ? 'surplus return' : 'return';
  const text = { target_volatility: `Maximise ${ret} at a ${risk} limit`, target_return: `Minimise ${risk} for a target ${ret}`,
    max_sharpe: rel === 'benchmark' ? 'Maximum information ratio' : rel === 'surplus' ? 'Best surplus return per unit of risk' : 'Maximum Sharpe ratio',
    min_variance: rel ? `Minimum ${risk}` : 'Minimum variance', min_cvar: 'Minimum tail loss (CVaR)', max_return: 'Maximum return', risk_parity: 'Risk parity (long-only only)' };
  [...$('oObjective').options].forEach(o => { o.textContent = text[o.value]; o.disabled = o.value === 'risk_parity' && S.mandate.kind !== 'long_only'; });
  if ($('oObjective').selectedOptions[0].disabled) $('oObjective').value = 'target_volatility';
  if (rel && $('oTarget').value === '10') $('oTarget').value = '2';            // tracking-error targets are small
  if (!rel && $('oTarget').value === '2') $('oTarget').value = '10';
  TARGETS = { target_volatility: `${risk[0].toUpperCase() + risk.slice(1)} limit (% a year)`, target_return: `Target ${ret} (% a year)`, min_cvar: 'Minimum return (% a year, optional)' };
}
function objectiveChanged() {
  objectiveLabels();
  const o = $('oObjective').value;
  $('oTargetLab').hidden = !(o in TARGETS); $('oTargetText').textContent = TARGETS[o] || '';
  $('oBetaLab').hidden = o !== 'min_cvar';
  if (o === 'min_cvar') $('oTarget').value = '';
}
async function addOptimised() {
  const obj = $('oObjective').value, t = parseFloat($('oTarget').value), target = obj in TARGETS && Number.isFinite(t) ? t / 100 : null;
  if ((obj === 'target_volatility' || obj === 'target_return') && target == null) throw new Error('Enter a target first.');
  const p = await call('optimise', { ...common(), constraints: consSpec(), objective: obj, target, beta: num('oBeta', 95) / 100 });
  const label = { target_volatility: `Max return at ${t}% ${mandateInfo().relative ? 'TE' : 'risk'}`, target_return: `Min risk for ${t}% return`, max_sharpe: 'Maximum Sharpe', min_variance: 'Minimum variance',
    max_return: 'Maximum return', risk_parity: 'Risk parity', min_cvar: `Minimum CVaR ${num('oBeta', 95)}%` }[obj];
  S.portfolios.push({ name: uniqueName($('oName').value.trim() || label), weights: p.weights });
  $('oName').value = ''; S.bt = null; drawOptimise();
}
function refreshSelectors() {
  const opts = S.portfolios.map((p, k) => `<option value="${k}">${esc(p.name)}</option>`).join('');
  ['sPort', 'mPort', 'pPort', 'tPort'].forEach(id => { const cur = $(id).value; $(id).innerHTML = opts; if (cur && +cur < S.portfolios.length) $(id).value = cur; });
  const names = '<option value="">None</option>' + S.portfolios.map(p => `<option>${esc(p.name)}</option>`).join('');
  ['bBench', 'tBench'].forEach(id => { const cur = $(id).value; $(id).innerHTML = names; if (S.portfolios.some(p => p.name === cur)) $(id).value = cur; });
}
const needPortfolios = () => { if (!S.portfolios.length) throw new Error('Add at least one portfolio in step 3 first.'); };
const pickPortfolio = id => { needPortfolios(); return S.portfolios[+$(id).value || 0]; };

// ------------------------------------------------------------------ 4 backtest & attribution
async function runBacktest() {
  needPortfolios();
  S.bt = await call('backtest', { ...common(), portfolios: plain(S.portfolios), benchmark: $('bBench').value || null, rebalance: $('bRebalance').value, cost_bps: num('bCost') });
  drawBacktest();
}
function drawBacktest() {
  refreshSelectors();
  $('btOut').hidden = !S.bt; $('attrOut').hidden = !S.attr;
  if (S.bt) {
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
  if (S.attr) drawAttr();
}
async function runAttr() {
  const p = pickPortfolio('tPort'), b = S.portfolios.find(x => x.name === $('tBench').value);
  S.attr = { ...(await call('attribution', { ...common(), weights: p.weights, benchmark_weights: b && b !== p ? b.weights : null })), portfolio: p.name, benchmark: b && b !== p ? b.name : null };
  drawBacktest();
}
function drawAttr() {
  const A = S.attr, ds = { assets: S.attr.labels || S.ds.assets }, hasB = A.allocation_effect != null;
  $('attrKpis').innerHTML = tile('Portfolio return', pct(A.total_return, 1), `${esc(A.portfolio)}, ${A.start} to ${A.end}`) +
    (hasB ? tile('Benchmark return', pct(A.benchmark_return, 1), esc(A.benchmark)) + tile('Active return', signed(A.active_return, 1), 'portfolio minus benchmark') : '') +
    tile('Largest contributor', esc(ds.assets[A.contribution.indexOf(Math.max(...A.contribution))]), signed(Math.max(...A.contribution), 1));
  const bars = (id, values, tip) => divergingBars($(id), { labels: ds.assets, values, fmt: v => signed(v, 1), colorPos: 'var(--series-1)', colorNeg: 'var(--series-2)', tipLabel: tip });
  bars('attrContrib', A.contribution, 'Contribution');
  $('attrEffectTitle').textContent = hasB ? `Allocation effect vs ${A.benchmark}` : 'Share of portfolio risk';
  if (hasB) bars('attrEffect', A.allocation_effect, 'Allocation effect');
  else divergingBars($('attrEffect'), { labels: ds.assets, values: A.risk_contribution, fmt: v => pct(v, 0), colorPos: 'var(--series-1)', colorNeg: 'var(--series-2)', tipLabel: 'Share of risk' });
  const head = ['Asset', 'Weight', 'Asset return', 'Contribution', 'Share of risk'];
  if (hasB) head.push('Benchmark weight', 'Active weight', 'Allocation effect');
  const rows = ds.assets.map((a, i) => { const r = [`<b>${esc(a)}</b>`, pct(A.weights[i], 1), pct(A.asset_return[i], 1), signed(A.contribution[i]), pct(A.risk_contribution[i], 0)];
    if (hasB) r.push(pct(A.benchmark_weights[i], 1), signed(A.active_weights[i], 1), `<span class="${A.allocation_effect[i] < 0 ? 'neg' : 'pos'}">${signed(A.allocation_effect[i])}</span>`); return r; });
  const total = ['<b>Total</b>', '100%', '', `<b>${signed(A.total_return)}</b>`, '100%'];
  if (hasB) total.push('100%', '', `<b>${signed(A.active_return)}</b>`);
  table('attrTable', head, [...rows, total], { emRows: [rows.length] });
}

// ------------------------------------------------------------------ 5 stress
async function runStress() {
  const p = pickPortfolio('sPort'), body = { ...common(), weights: p.weights };
  let res = await call('stress', body);
  let windows = res.windows.map(w => ({ name: w.name, start: w.start, end: w.end, kind: w.kind }));
  if ($('sStart').value && $('sEnd').value) {
    windows = [...windows, { name: 'Custom window', start: $('sStart').value, end: $('sEnd').value }];
    res = await call('stress', { ...body, windows });
  }
  S.stress = { ...res, portfolio: p.name, weights: p.weights, list: windows };
  S.stress.show = defaultShown(res.windows);
  S.sens = null;
  const ok = res.windows.filter(w => !w.error);
  $('shWindow').innerHTML = ok.map(w => `<option value="${esc(w.name)}">${esc(w.name)}</option>`).join('');
  if (ok.length) await runShock(); else drawStress();
}
async function runShock() {
  const w = S.stress.list.find(x => x.name === $('shWindow').value);
  S.sens = (await call('stress', { ...common(), weights: S.stress.weights, windows: [w], shock_window: w })).sensitivity;
  drawStress();
}
/* Windows ticked for the path chart: by default the crises since 2023, the worst drawdown and any custom window. */
function defaultShown(W) {
  const ok = W.filter(w => !w.error);
  const pick = ok.filter(w => w.end >= '2023-01-01' || w.kind === 'detected' && /Worst|Current/.test(w.name) || w.name === 'Custom window');
  return new Set((pick.length ? pick : ok).slice(-8).map(w => w.name));
}
function drawStress() {
  refreshSelectors();
  $('stressOut').hidden = !S.stress; $('shockCard').hidden = !S.sens;
  if (!S.stress) return;
  const W = S.stress.windows, ok = W.filter(w => !w.error), show = S.stress.show;
  const shown = ok.filter(w => show.has(w.name)), colour = w => SERIES[shown.indexOf(w)] || 'var(--axis)';
  const badge = w => `<span class="status">${w.kind === 'detected' ? 'Detected' : w.name === 'Custom window' ? 'Custom' : 'Named crisis'}</span>`;
  table('stressTable', ['Show', 'Window', 'Type', 'Period', 'Days', 'Annualised return', 'Risk σ', 'Cumulative return', 'Max drawdown'],
    W.map(w => (w.error ? ['', `<b>${esc(w.name)}</b>`, badge(w), `${w.start} → ${w.end}`, w.periods, `<span class="muted">${esc(w.error)}</span>`, '', '', '']
      : [`<input type="checkbox" class="st-show" data-name="${esc(w.name)}" ${show.has(w.name) ? 'checked' : ''} aria-label="Show ${esc(w.name)} in the chart">`,
        `${show.has(w.name) ? `<span class="sw-inline" style="background:${colour(w)}"></span>` : ''}<b>${esc(w.name)}</b>`, badge(w), `${w.start} → ${w.end}`, w.periods,
        `<span class="${w.mean_ann_return < 0 ? 'neg' : ''}">${pct(w.mean_ann_return, 1)}</span>`, pct(w.volatility, 1), `<span class="${w.cumulative < 0 ? 'neg' : ''}">${pct(w.cumulative, 1)}</span>`, pct(w.max_drawdown, 1)])));
  if (shown.length) {
    const n = Math.max(...shown.map(w => w.path.length));
    lineChart($('stressChart'), { labels: Array.from({ length: n }, (_, i) => i), legend: 'stressLegend', endLabels: false,
      series: shown.map(w => ({ name: w.name, color: colour(w), values: Array.from({ length: n }, (_, i) => w.path[i] ?? null) })),
      yFmt: v => signed(v - 1, 1), valFmt: v => signed(v - 1, 2), xFmt: i => String(i), endLabels: false,
      xlabel: `${S.ds.frequency} periods since the start of each window · cumulative return · ${S.stress.portfolio}`, tipTitle: i => `Period ${i}` });
  } else {
    $('stressChart').innerHTML = `<p class="empty">${ok.length ? 'Tick at least one window in the table to chart it.' : 'No stress window falls inside this data. Add a custom window above.'}</p>`;
    $('stressLegend').innerHTML = '';
  }
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
const simSettings = () => ({ years: num('mYears', 5), trials: +$('mTrials').value, method: $('mMethod').value, seed: Math.max(0, Math.floor(num('mSeed', 42))) });
async function runSim() {
  const p = pickPortfolio('mPort');
  S.mc = { ...(await call('simulate', { ...common(), weights: p.weights, ...simSettings() })), portfolio: p.name };
  drawSim();
}
function drawSim() {
  refreshSelectors();
  $('simOut').hidden = !S.mc;
  if (!S.mc) return;
  const M = S.mc, ppy = S.ds.periods_per_year, n = M.fan.wealth[0].length;
  $('simKpis').innerHTML = tile('Median annual return', pct(M.median_ann_return), `${M.years.toFixed(1)}-year horizon · ${esc(M.portfolio)}`) +
    tile('5th percentile (95% VaR)', pct(M.p05_ann_return), `CVaR ${pct(M.cvar_95_ann_return)} a year`) +
    tile('Probability of a loss', pct(M.prob_loss, 1), 'ending below the starting value') + tile('Median worst drawdown', pct(M.median_max_drawdown, 1), `1 in 20 paths: ${pct(M.p05_max_drawdown, 1)} or worse`);
  const years = Math.max(1, Math.round(M.years));
  fanChart($('simFan'), M.fan.wealth, { legendId: 'simLegend', tickIdx: [...new Set(Array.from({ length: years + 1 }, (_, y) => Math.min(n - 1, y * ppy)))],
    xFmt: i => `${Math.round(i / ppy)}y`, tipTitle: i => `Year ${(i / ppy).toFixed(2)}`, yFmt: v => v.toFixed(2) });
  histogram($('simHist'), M.histogram, { marks: [[M.p05_ann_return, '5th pct'], [M.median_ann_return, 'median']] });
  table('simTable', ['Statistic', 'Value'], [['Method', { normal: 'Normal', student_t: 'Fat tails (Student-t)', bootstrap: 'Bootstrap history' }[M.method]], ['Trials', M.trials.toLocaleString()],
    ['Mean annual return', pct(M.mean_ann_return)], ['Median annual return', pct(M.median_ann_return)], ['5th to 95th percentile', `${pct(M.p05_ann_return)} to ${pct(M.p95_ann_return)}`],
    ['Average annualised volatility', pct(M.mean_volatility)], ['Worst simulated annual return', pct(M.worst_ann_return)],
    ['Ending value of 1 (5th / median / 95th)', `${M.p05_terminal.toFixed(2)} / ${M.median_terminal.toFixed(2)} / ${M.p95_terminal.toFixed(2)}`]]);
}

// ------------------------------------------------------------------ 7 spending policy
function spendSettings() {
  const rate = num('pRate', 4) / 100;
  return { rule: $('pRule').value, rate, smoothing_years: Math.round(num('pSmooth', 3)), hybrid_weight: num('pHybrid', 70) / 100, inflation: num('pInfl', 2.5) / 100,
    inflation_vol: num('pInflVol', 1) / 100, contribution: num('pContrib', 0) / 100, fee: num('pFee', 0) / 100, years: Math.round(num('pYears', 30)), trials: +$('pTrials').value,
    compare_rates: [-0.02, -0.01, 0, 0.01, 0.02].map(d => +(rate + d).toFixed(4)).filter(r => r > 0) };
}
async function runSpend() {
  const p = pickPortfolio('pPort');
  S.spend = { ...(await call('spending', { ...common(), weights: p.weights, ...spendSettings() })), portfolio: p.name };
  drawSpend();
}
function drawSpend() {
  refreshSelectors();
  $('pSmoothLab').hidden = $('pRule').value !== 'smoothed'; $('pHybridLab').hidden = $('pRule').value !== 'hybrid';
  $('spendOut').hidden = !S.spend;
  if (!S.spend) return;
  const X = S.spend, yrs = X.years, tick = Array.from({ length: Math.floor(yrs / 5) + 1 }, (_, k) => k * 5);
  $('spendKpis').innerHTML = tile('Keeps its real value', pct(X.prob_preserve, 0), `of simulations after ${yrs} years · ${esc(X.portfolio)}`) +
    tile('Median value at the end', X.median_terminal_real.toFixed(0), `today's money, start = 100 (5th pct ${X.p05_terminal_real.toFixed(0)})`) +
    tile('Median annual spending', X.median_spending_real.toFixed(2), `today's money; first year ${X.first_year_spending.toFixed(2)}`) +
    tile('Falls below 70% at some point', pct(X.prob_below_70, 0), `real spending cut over 10% in a year: ${pct(X.prob_spending_cut_10, 0)}`);
  fanChart($('spendVal'), X.value_fan.values, { legendId: 'spendValLegend', tickIdx: tick, xFmt: i => `${i}y`, tipTitle: i => `Year ${i}`, yFmt: v => v.toFixed(0) });
  fanChart($('spendPay'), X.spending_fan.values, { legendId: null, tickIdx: tick.slice(0, -1).length ? tick.filter(t => t < yrs) : [0], xFmt: i => `${i + 1}y`, tipTitle: i => `Year ${i + 1}`, yFmt: v => v.toFixed(1) });
  const C = X.comparison || [];
  columnChart($('spendCmp'), { categories: C.map(c => pct(c.rate, 1)), series: [{ name: 'Probability of keeping real value', values: C.map(c => c.prob_preserve), color: 'var(--series-1)' },
    { name: 'Probability of falling below 70%', values: C.map(c => c.prob_below_70), color: 'var(--series-2)' }], yFmt: v => pct(v, 0), valFmt: v => pct(v, 0), legend: 'spendCmpLegend', height: 240 });
  table('spendTable', ['Spending rate', 'Keeps real value', 'Falls below 70%', 'Median end value', 'Median annual spending', 'Spending cut over 10%', 'Spending volatility'],
    C.map(c => [Math.abs(c.rate - X.rate) < 1e-9 ? `<b>${pct(c.rate, 2)} (chosen)</b>` : pct(c.rate, 2), pct(c.prob_preserve, 0), pct(c.prob_below_70, 0), c.median_terminal_real.toFixed(0), c.median_spending_real.toFixed(2), pct(c.prob_spending_cut_10, 0), pct(c.spending_volatility, 1)]),
    { emRows: [C.findIndex(c => Math.abs(c.rate - X.rate) < 1e-9)] });
}

// ------------------------------------------------------------------ 8 currency
async function runFx() {
  S.fx = await call('currency', { start: S.start, end: S.end, asset: +$('fAsset').value, currency: +$('fCcy').value, hedge_ratio: num('fHedge', 50) / 100, base_rate: num('fBase') / 100, foreign_rate: num('fForeign') / 100 });
  drawFx();
}
function drawFx() {
  $('fHedgeOut').textContent = `${num('fHedge', 50)}%`;
  $('fxOut').hidden = !S.fx;
  if (!S.fx) return;
  const X = S.fx, m = X.metrics, d = X.dates, col = { 'Local currency': 'var(--series-3)', Unhedged: 'var(--series-2)', Hedged: 'var(--series-1)' };
  $('fxKpis').innerHTML = tile('Asset return in its own currency', pct(m['Local currency'].cumulative), esc(X.asset)) +
    tile('Unhedged return', pct(m.Unhedged.cumulative), `currency moved ${signed(m.Currency.cumulative)}`) +
    tile(`${pct(X.hedge_ratio, 0)} hedged return`, pct(m.Hedged.cumulative), `hedge added ${signed(X.hedge_value_added)}`) +
    tile('Lowest-risk hedge ratio', pct(X.min_variance_hedge_ratio, 0), X.min_variance_hedge_ratio > 1 ? 'above 100%: the asset tends to fall when the currency falls' : `asset and currency correlation ${fmt(X.correlation_asset_currency)}`);
  const step = Math.max(1, Math.floor(d.length / 7));
  lineChart($('fxWealth'), { labels: d, legend: 'fxLegend', xTickIdx: d.map((_, i) => i).filter(i => i % step === 0), xFmt: i => d[i].slice(2),
    series: Object.keys(col).map(k => ({ name: k === 'Hedged' ? `Hedged (${pct(X.hedge_ratio, 0)})` : k, short: k === 'Local currency' ? 'Local' : k, color: col[k], values: X.wealth[k] })), yFmt: v => v.toFixed(2), valFmt: v => v.toFixed(3) });
  const g = lineChart($('fxCurve'), { labels: X.curve.map(c => c.hedge_ratio), series: [{ name: 'Annualised volatility', color: 'var(--accent)', values: X.curve.map(c => c.volatility) }],
    yFmt: v => pct(v, 1), xTickIdx: [0, 2, 4, 6, 8, 10, 12], xFmt: i => pct(X.curve[i].hedge_ratio, 0), tipTitle: i => `Hedge ratio ${pct(X.curve[i].hedge_ratio, 0)}`, height: 260, endLabels: false, xlabel: 'Hedge ratio' });
  const at = X.hedge_ratio / 0.1, near = X.curve[Math.round(at)] || X.curve.at(-1);
  if (at <= 12) el('circle', { cx: g.xs(at), cy: g.ys(near.volatility), r: 7, style: 'fill:var(--highlight);stroke:var(--surface-1);stroke-width:2;pointer-events:none' }, g.f.svg);
  table('fxTable', ['', 'Local currency', 'Unhedged', `Hedged ${pct(X.hedge_ratio, 0)}`, 'Currency'],
    [['Cumulative return', 'cumulative'], ['Annualised volatility', 'volatility'], ['Max drawdown', 'max_drawdown'], ['Worst period', 'worst']].map(([lab, k]) =>
      [lab, ...['Local currency', 'Unhedged', 'Hedged', 'Currency'].map(s => pct(m[s][k]))]).concat([['Interest-rate carry on the hedge (a year)', '', '', signed(X.carry_per_year * X.hedge_ratio), '']]));
}

// ------------------------------------------------------------------ exports
const exportBody = () => ({ ...common(), constraints: consSpec(), portfolios: plain(S.portfolios), benchmark: $('bBench').value || null, rebalance: $('bRebalance').value, cost_bps: num('bCost') });
async function exportExcel() { needPortfolios(); await download(`/api/datasets/${S.ds.id}/report.xlsx`, exportBody(), 'portfolio_analysis.xlsx'); }
function openReport() {
  needPortfolios();
  const opts = S.portfolios.map(p => `<option>${esc(p.name)}</option>`).join('');
  $('rFocus').innerHTML = opts; $('rBench').innerHTML = '<option value="">None</option>' + opts;
  const sharpe = S.portfolios.find(p => p.name === 'Maximum Sharpe'); if (sharpe) $('rFocus').value = sharpe.name;
  if ($('bBench').value) $('rBench').value = $('bBench').value;
  $('reportDlg').showModal();
}
async function createReport() {
  const s = spendSettings();
  await download(`/api/datasets/${S.ds.id}/report.docx`, { ...exportBody(), benchmark: $('rBench').value || null, focus: $('rFocus').value, title: $('rTitle').value.trim() || 'Strategic asset allocation review',
    client: $('rClient').value.trim() || null, simulation: simSettings(), spending: $('rSpend').checked ? { ...s, trials: Math.min(s.trials, 5000) } : null, stress_windows: S.stress ? S.stress.list : null }, 'investment_committee_report.docx');
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

  // assumptions: re-render on structural changes without losing what has been typed
  const rerender = fn => { capture(false); fn(); renderAssume(); };
  $('aSource').addEventListener('change', () => rerender(() => prepareSource($('aSource').value)));
  $('aCorrCustom').addEventListener('change', () => rerender(() => { S.assume.corrCustom = $('aCorrCustom').checked; if (S.assume.corrCustom && !S.assume.corr) S.assume.corr = S.stats.historical.corr.map(r => [...r]); }));
  $('mKind').addEventListener('change', () => rerender(() => setMandateKind($('mKind').value)));
  $('mBench').addEventListener('change', () => rerender(() => {}));
  $('aScenario').addEventListener('change', () => { const v = $('aScenario').value; if (v !== 'custom') [$('aRetMult').value, $('aVolMult').value] = v.split(','); $('aRetMult').disabled = $('aVolMult').disabled = v !== 'custom'; });
  $('p-assume').addEventListener('click', ev => {
    const t = ev.target, h = S.stats.historical, a = S.assume;
    if (t.id === 'addSet') rerender(() => a.sets.push({ name: `Forecast set ${a.sets.length + 1}`, weight: 1, mu: [...h.mu], vol: [...h.vol] }));
    else if (t.classList.contains('set-del')) rerender(() => a.sets.splice(+t.closest('.set').dataset.k, 1));
    else if (t.id === 'addView') rerender(() => a.bl.views.push({ type: 'absolute', asset: 0, value: h.mu[0], confidence: 0.5 }));
    else if (t.classList.contains('v-del')) rerender(() => a.bl.views.splice(+t.closest('.view-row').dataset.k, 1));
    else if (t.id === 'addGroup') rerender(() => S.cons.groups.push({ name: `Group ${S.cons.groups.length + 1}`, assets: [], min: null, max: null }));
    else if (t.classList.contains('g-del')) rerender(() => S.cons.groups.splice(+t.closest('.grp').dataset.k, 1));
  });
  $('p-assume').addEventListener('change', ev => { if (ev.target.classList.contains('v-type') || ev.target.classList.contains('set-name')) rerender(() => {}); });
  $('aApply').addEventListener('click', ev => run(ev.target, () => applyAssume('optimise')));

  $('runFrontier').addEventListener('click', ev => run(ev.target, runFrontier));
  $('oObjective').addEventListener('change', objectiveChanged);
  $('oAdd').addEventListener('click', ev => run(ev.target, addOptimised));
  $('oCustom').addEventListener('click', () => { const n = S.ds.assets.length; S.portfolios.push({ name: uniqueName('Custom'), weights: new Array(n).fill(1 / n), editable: true }); S.bt = null; drawOptimise(); });
  $('portTable').addEventListener('click', ev => { if (ev.target.classList.contains('p-del')) { S.portfolios.splice(+ev.target.dataset.k, 1); S.bt = null; S.attr = null; drawOptimise(); } });
  $('portTable').addEventListener('change', ev => {
    const t = ev.target, p = S.portfolios[+t.dataset.k];
    if (t.classList.contains('w-in')) { p.weights[+t.dataset.i] = Math.max(0, parseFloat(t.value) || 0) / 100; S.bt = null; drawOptimise(); }
    if (t.classList.contains('p-name')) { p.name = t.value.trim() || p.name; drawOptimise(); }
  });
  $('runBacktest').addEventListener('click', ev => run(ev.target, runBacktest));
  $('runAttr').addEventListener('click', ev => run(ev.target, runAttr));
  $('runStress').addEventListener('click', ev => run(ev.target, runStress));
  $('stressTable').addEventListener('change', ev => {
    const t = ev.target; if (!t.classList.contains('st-show')) return;
    t.checked ? S.stress.show.add(t.dataset.name) : S.stress.show.delete(t.dataset.name); drawStress();
  });
  $('stressPick').addEventListener('click', ev => {
    const k = ev.target.dataset.pick; if (!k || !S.stress) return;
    const ok = S.stress.windows.filter(w => !w.error);
    S.stress.show = k === 'recent' ? defaultShown(S.stress.windows) : new Set(k === 'all' ? ok.map(w => w.name) : k === 'named' ? ok.filter(w => w.kind !== 'detected').map(w => w.name) : []);
    drawStress();
  });
  $('shWindow').addEventListener('change', () => run(null, runShock));
  $('shSev').addEventListener('input', drawShock);
  $('runSim').addEventListener('click', ev => run(ev.target, runSim));
  $('runSpend').addEventListener('click', ev => run(ev.target, runSpend));
  $('pRule').addEventListener('change', drawSpend);
  $('runFx').addEventListener('click', ev => run(ev.target, runFx));
  $('fHedge').addEventListener('input', () => { $('fHedgeOut').textContent = `${num('fHedge', 50)}%`; });
  $('fHedge').addEventListener('change', () => { if (S.fx) run(null, runFx); });
  $('exportBtn').addEventListener('click', ev => run(ev.target, exportExcel));
  $('reportBtn').addEventListener('click', () => run(null, async () => openReport()));
  $('reportDlg').addEventListener('close', () => { if ($('reportDlg').returnValue === 'ok') run($('reportBtn'), createReport); });

  // Demo links: ?demo=indices&step=backtest loads a sample and runs through to that step.
  const q = new URLSearchParams(location.search), demo = q.get('demo'), step = q.get('step');
  if (demo) await run(null, async () => {
    await onDataset(await (await api(`/api/datasets/sample/${demo}`)).json());
    if (!STEPS.includes(step) || step === 'data') return;
    if (q.get('source')) { prepareSource(q.get('source')); S.assume.corrCustom = q.get('corr') === '1'; if (S.assume.corrCustom) S.assume.corr = S.stats.historical.corr.map(r => [...r]);
      if (q.get('source') === 'black_litterman') S.assume.bl.views.push({ type: 'relative', long: 0, short: 1, value: 0.02, confidence: 0.6 }); await loadStats(); }
    if (q.get('mandate')) { setMandateKind(q.get('mandate')); if (q.get('mandate') === 'benchmark_relative') { S.mandate.benchW = null; } await loadStats(); }
    go(step);
    if (step === 'assume') return;
    if (step === 'currency') return runFx();
    await runFrontier();
    await ({ backtest: async () => { $('bBench').value = 'Equal weight'; await runBacktest(); $('tBench').value = 'Equal weight'; await runAttr(); }, stress: runStress, simulate: runSim, spending: runSpend }[step] || (async () => {}))();
  });
}
init().catch(fail);
