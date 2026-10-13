'use strict';
/* Shared helpers for every page: formatting, SVG charts, tooltip, tables, theme, site navigation. */

const SVGNS = 'http://www.w3.org/2000/svg';
// eight categorical slots; any further series fall back to a muted grey (identified by label, not hue)
const SERIES = Array.from({ length: 64 }, (_, i) => (i < 8 ? `var(--series-${i + 1})` : 'var(--text-muted)'));
const $ = id => document.getElementById(id);
const pct = (v, d = 2) => (v == null || isNaN(v) ? '–' : (v * 100).toFixed(d) + '%');
const fmt = (v, d = 2) => (v == null || isNaN(v) ? '–' : (+v).toFixed(d));
const signed = (v, d = 2) => (v > 0 ? '+' : '') + pct(v, d);
const cssVar = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

const UNITS = [
  { n: 2, href: 'unit2.html', short: 'Unit 2', title: 'Efficient frontier' },
  { n: 3, href: 'unit3.html', short: 'Unit 3', title: 'Solver optimisation' },
  { n: 4, href: 'unit4.html', short: 'Unit 4', title: 'CMEs & SAA backtest' },
  { n: 5, href: 'unit5.html', short: 'Unit 5', title: 'Stress tests & Monte Carlo' },
  { n: 6, href: 'unit6.html', short: 'Unit 6', title: 'Performance & IC report' },
];

// ------------------------------------------------------------------ maths
function mulberry32(a) {
  return function () {
    a |= 0; a = (a + 0x6D2B79F5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
function normalRng(seed) {           // Box-Muller on a seeded uniform
  const u = mulberry32(seed); let spare = null;
  return () => {
    if (spare !== null) { const s = spare; spare = null; return s; }
    let a, b; do { a = u(); } while (a <= 1e-12); b = u();
    const r = Math.sqrt(-2 * Math.log(a)); spare = r * Math.sin(2 * Math.PI * b);
    return r * Math.cos(2 * Math.PI * b);
  };
}
const mean = a => a.reduce((s, x) => s + x, 0) / a.length;
const stdev = a => { const m = mean(a); return Math.sqrt(a.reduce((s, x) => s + (x - m) ** 2, 0) / (a.length - 1)); };
function percentile(a, p) {          // Excel PERCENTILE (inclusive)
  const s = [...a].sort((x, y) => x - y), h = (s.length - 1) * p, lo = Math.floor(h);
  return s[lo] + (h - lo) * ((s[lo + 1] ?? s[lo]) - s[lo]);
}
const dot = (a, b) => a.reduce((s, x, i) => s + x * b[i], 0);
const quad = (w, C) => w.reduce((s, wi, i) => s + wi * dot(C[i], w), 0);

// ------------------------------------------------------------------ svg primitives
function el(tag, attrs = {}, parent) {
  const e = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'text') e.textContent = v; else e.setAttribute(k, v);
  }
  if (parent) parent.appendChild(e);
  return e;
}
const lin = (d0, d1, r0, r1) => v => r0 + (v - d0) / ((d1 - d0) || 1) * (r1 - r0);
function ticks(min, max, count = 5) {
  if (max === min) { max = min + 1; }
  const step0 = (max - min) / count, mag = 10 ** Math.floor(Math.log10(step0)), e = step0 / mag;
  const step = (e >= 7.5 ? 10 : e >= 3.5 ? 5 : e >= 1.5 ? 2 : 1) * mag;
  const out = [];
  for (let v = Math.ceil(min / step - 1e-9) * step; v <= max + 1e-9; v += step) out.push(+v.toFixed(10));
  return out;
}
function frame(container, height, m) {
  container.innerHTML = '';
  const W = Math.max(280, container.clientWidth), H = height;
  const svg = el('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img' }, container);
  return { svg, W, H, x0: m.l, x1: W - m.r, y0: H - m.b, y1: m.t };
}
const TXT = (size = 12, color = 'var(--text-muted)', weight = 400) => `fill:${color};font-size:${size}px;font-weight:${weight}`;
function yAxis(f, ys, yt, yfmt) {
  for (const t of yt) {
    const y = ys(t);
    el('line', { x1: f.x0, x2: f.x1, y1: y, y2: y, style: 'stroke:var(--grid);stroke-width:1' }, f.svg);
    el('text', { x: f.x0 - 8, y: y + 4, 'text-anchor': 'end', style: TXT(), text: yfmt(t) }, f.svg);
  }
}
function axisLabels(f, xlabel, ylabel) {
  if (xlabel) el('text', { x: (f.x0 + f.x1) / 2, y: f.H - 4, 'text-anchor': 'middle', style: TXT(12, 'var(--text-secondary)'), text: xlabel }, f.svg);
  if (ylabel) el('text', { transform: `translate(14 ${(f.y0 + f.y1) / 2}) rotate(-90)`, 'text-anchor': 'middle', style: TXT(12, 'var(--text-secondary)'), text: ylabel }, f.svg);
}
function baseline(f, y) {
  el('line', { x1: f.x0, x2: f.x1, y1: y, y2: y, style: 'stroke:var(--axis);stroke-width:1' }, f.svg);
}
function roundedBar(svg, x, y0, w, y, style, r = 4) {   // rounded at the data end, square at the baseline
  const h = Math.abs(y0 - y); r = Math.min(r, w / 2, h);
  const up = y < y0;
  const d = up
    ? `M${x} ${y0}V${y + r}Q${x} ${y} ${x + r} ${y}H${x + w - r}Q${x + w} ${y} ${x + w} ${y + r}V${y0}Z`
    : `M${x} ${y0}V${y - r}Q${x} ${y} ${x + r} ${y}H${x + w - r}Q${x + w} ${y} ${x + w} ${y - r}V${y0}Z`;
  return el('path', { d, style }, svg);
}
function hBar(svg, x0, x, y, h, style, r = 4) {         // horizontal bar from x0 to x
  const w = Math.abs(x - x0); r = Math.min(r, h / 2, w);
  const right = x > x0;
  const d = right
    ? `M${x0} ${y}H${x - r}Q${x} ${y} ${x} ${y + r}V${y + h - r}Q${x} ${y + h} ${x - r} ${y + h}H${x0}Z`
    : `M${x0} ${y}H${x + r}Q${x} ${y} ${x} ${y + r}V${y + h - r}Q${x} ${y + h} ${x + r} ${y + h}H${x0}Z`;
  return el('path', { d, style }, svg);
}

// ------------------------------------------------------------------ tooltip
let tipEl;
function showTip(html, cx, cy) {
  if (!tipEl) { tipEl = document.createElement('div'); tipEl.className = 'tooltip'; tipEl.setAttribute('role', 'status'); document.body.appendChild(tipEl); }
  tipEl.innerHTML = html; tipEl.hidden = false;
  const r = tipEl.getBoundingClientRect();
  let x = cx + 14, y = cy + 14;
  if (x + r.width > innerWidth - 8) x = cx - r.width - 14;
  if (y + r.height > innerHeight - 8) y = cy - r.height - 14;
  tipEl.style.left = Math.max(8, x) + 'px'; tipEl.style.top = Math.max(8, y) + 'px';
}
const hideTip = () => { if (tipEl) tipEl.hidden = true; };
const trow = (k, v, sw) => `<div class="r"><span>${sw ? `<i class="sw" style="background:${sw}"></i>` : ''}${k}</span><b>${v}</b></div>`;
function hoverable(node, htmlFn) {
  node.addEventListener('mousemove', ev => showTip(htmlFn(), ev.clientX, ev.clientY));
  node.addEventListener('mouseleave', hideTip);
}
function svgPoint(f, ev) {
  const b = f.svg.getBoundingClientRect(), s = f.W / b.width;
  return [(ev.clientX - b.left) * s, (ev.clientY - b.top) * s];
}

// ------------------------------------------------------------------ legend
function legend(target, items) {
  const t = typeof target === 'string' ? $(target) : target;
  t.innerHTML = items.map(([label, color, cls = '']) =>
    `<span class="item"><i class="sw ${cls}" style="background:${color};color:${color}"></i>${label}</span>`).join('');
}

// ------------------------------------------------------------------ line chart
/* opts: { labels:[...], series:[{name,values,color,dash}], yFmt, xTickIdx:[...], xFmt(i), height, zeroLine, endLabels, ylabel, tipTitle(i), valFmt } */
function lineChart(container, o) {
  const narrow = container.clientWidth < 560;
  const f = frame(container, o.height || (narrow ? 280 : 340), { l: 56, r: o.endLabels === false ? 16 : (narrow ? 64 : 92), t: 12, b: o.xlabel ? 44 : 30 });
  const n = o.labels.length;
  const all = o.series.flatMap(s => s.values.filter(v => v != null));
  let yMin = Math.min(...all), yMax = Math.max(...all);
  if (o.zeroLine) { yMin = Math.min(yMin, 0); yMax = Math.max(yMax, 0); }
  const pad = (yMax - yMin) * 0.06 || 0.01; yMin -= pad; yMax += pad;
  const xs = lin(0, n - 1, f.x0, f.x1), ys = lin(yMin, yMax, f.y0, f.y1);
  yAxis(f, ys, ticks(yMin, yMax, 5), o.yFmt);
  baseline(f, f.y0);
  if (o.zeroLine && yMin < 0) el('line', { x1: f.x0, x2: f.x1, y1: ys(0), y2: ys(0), style: 'stroke:var(--axis);stroke-width:1' }, f.svg);
  let xt = o.xTickIdx || ticks(0, n - 1, narrow ? 4 : 7).filter(i => Number.isInteger(i));
  const maxT = Math.max(2, Math.floor((f.x1 - f.x0) / 56));      // thin labels so they never collide
  if (xt.length > maxT) { const k = Math.ceil(xt.length / maxT); xt = xt.filter((_, i) => i % k === 0); }
  for (const i of xt) el('text', { x: xs(i), y: f.y0 + 18, 'text-anchor': 'middle', style: TXT(), text: o.xFmt ? o.xFmt(i) : o.labels[i] }, f.svg);
  axisLabels(f, o.xlabel, o.ylabel);
  o.series.forEach(s => {
    let d = '', pen = false;
    s.values.forEach((v, t) => {
      if (v == null) { pen = false; return; }
      d += `${pen ? 'L' : 'M'}${xs(t).toFixed(1)} ${ys(v).toFixed(1)}`; pen = true;
    });
    el('path', { d, style: `fill:none;stroke:${s.color};stroke-width:${s.width || 2};stroke-linejoin:round;stroke-linecap:round${s.dash ? ';stroke-dasharray:' + s.dash : ''}` }, f.svg);
  });
  if (o.endLabels !== false) {
    const ends = o.series.map((s, i) => {
      let k = s.values.length - 1; while (k > 0 && s.values[k] == null) k--;
      return { i, y: ys(s.values[k]) };
    }).sort((a, b) => a.y - b.y);
    for (let k = 1; k < ends.length; k++) if (ends[k].y - ends[k - 1].y < 14) ends[k].y = ends[k - 1].y + 14;
    ends.forEach(e => el('text', { x: f.x1 + 6, y: e.y + 4, style: TXT(12, 'var(--text-secondary)', 600), text: o.series[e.i].short || o.series[e.i].name }, f.svg));
  }
  const cross = el('line', { y1: f.y1, y2: f.y0, style: 'stroke:var(--text-muted);stroke-width:1;pointer-events:none', visibility: 'hidden' }, f.svg);
  const dots = o.series.map(s => el('circle', { r: 4, style: `fill:${s.color};stroke:var(--surface-1);stroke-width:2;pointer-events:none`, visibility: 'hidden' }, f.svg));
  const hit = el('rect', { x: f.x0, y: f.y1, width: f.x1 - f.x0, height: f.y0 - f.y1, style: 'fill:transparent' }, f.svg);
  const vf = o.valFmt || o.yFmt;
  hit.addEventListener('mousemove', ev => {
    const [mx] = svgPoint(f, ev);
    const t = Math.max(0, Math.min(n - 1, Math.round((mx - f.x0) / (f.x1 - f.x0) * (n - 1))));
    cross.setAttribute('x1', xs(t)); cross.setAttribute('x2', xs(t)); cross.setAttribute('visibility', 'visible');
    o.series.forEach((s, i) => {
      const v = s.values[t];
      if (v == null) { dots[i].setAttribute('visibility', 'hidden'); return; }
      dots[i].setAttribute('cx', xs(t)); dots[i].setAttribute('cy', ys(v)); dots[i].setAttribute('visibility', 'visible');
    });
    const rows = o.series.map((s, i) => [s, s.values[t]]).filter(([, v]) => v != null).sort((a, b) => b[1] - a[1]);
    showTip(`<div class="t">${o.tipTitle ? o.tipTitle(t) : o.labels[t]}</div>` + rows.map(([s, v]) => trow(s.name, vf(v), s.color)).join(''), ev.clientX, ev.clientY);
  });
  hit.addEventListener('mouseleave', () => { cross.setAttribute('visibility', 'hidden'); dots.forEach(d => d.setAttribute('visibility', 'hidden')); hideTip(); });
  if (o.legend) legend(o.legend, o.series.map(s => [s.name, s.color, s.dash ? 'dash-c' : '']));
  return { f, xs, ys };
}

// ------------------------------------------------------------------ column chart (grouped, supports negatives)
/* opts: { categories:[...], series:[{name,values,color}], yFmt, height, catLabel(i), valFmt, refLine:{value,label} } */
function columnChart(container, o) {
  const narrow = container.clientWidth < 560;
  const f = frame(container, o.height || (narrow ? 260 : 300), { l: 56, r: 12, t: 14, b: o.rotate ? 64 : 40 });
  const all = o.series.flatMap(s => s.values);
  let yMin = Math.min(0, ...all), yMax = Math.max(0, ...all);
  if (o.refLine) { yMin = Math.min(yMin, o.refLine.value); yMax = Math.max(yMax, o.refLine.value); }
  const pad = (yMax - yMin) * 0.08; if (yMax > 0) yMax += pad; if (yMin < 0) yMin -= pad;
  const ys = lin(yMin, yMax, f.y0, f.y1), y0 = ys(0);
  yAxis(f, ys, ticks(yMin, yMax, 5), o.yFmt);
  const nc = o.categories.length, ns = o.series.length, band = (f.x1 - f.x0) / nc;
  const groupW = band * (ns === 1 ? 0.62 : 0.8), bw = Math.max(2, (groupW - 2 * (ns - 1)) / ns);
  o.categories.forEach((c, i) => {
    const gx = f.x0 + i * band + (band - groupW) / 2;
    o.series.forEach((s, k) => {
      const v = s.values[i]; if (v == null) return;
      const x = gx + k * (bw + 2);
      const bar = roundedBar(f.svg, x, y0, bw, ys(v), `fill:${s.color}`);
      hoverable(bar, () => `<div class="t">${o.catLabel ? o.catLabel(i) : c}</div>` + o.series.map(q => trow(q.name, (o.valFmt || o.yFmt)(q.values[i]), q.color)).join(''));
    });
    const lab = o.catLabel ? o.catLabel(i) : c;
    if (o.rotate) el('text', { transform: `translate(${f.x0 + (i + .5) * band} ${f.y0 + 14}) rotate(-35)`, 'text-anchor': 'end', style: TXT(11), text: lab }, f.svg);
    else el('text', { x: f.x0 + (i + .5) * band, y: f.y0 + 18, 'text-anchor': 'middle', style: TXT(narrow ? 10 : 12), text: lab }, f.svg);
  });
  baseline(f, y0);
  if (o.refLine) {
    const y = ys(o.refLine.value);
    el('line', { x1: f.x0, x2: f.x1, y1: y, y2: y, style: 'stroke:var(--text-secondary);stroke-width:1.5;stroke-dasharray:5 4' }, f.svg);
    el('text', { x: f.x1, y: y - 6, 'text-anchor': 'end', style: TXT(12, 'var(--text-secondary)', 600), text: o.refLine.label }, f.svg);
  }
  if (o.legend) legend(o.legend, o.series.map(s => [s.name, s.color]));
  return f;
}

// ------------------------------------------------------------------ horizontal diverging bars (one series)
function divergingBars(container, o) {   // o: { labels, values, fmt, colorPos, colorNeg }
  const labW = container.clientWidth < 560 ? 100 : 140, bh = 22, gap = 10;
  const f = frame(container, o.labels.length * (bh + gap) + 28, { l: labW, r: 56, t: 4, b: 24 });
  const m = Math.max(...o.values.map(Math.abs)) * 1.35 || 0.01;
  const xs = lin(-m, m, f.x0, f.x1), x0 = xs(0);
  ticks(-m, m, 4).forEach(t => {
    el('line', { x1: xs(t), x2: xs(t), y1: f.y1, y2: f.y0, style: 'stroke:var(--grid);stroke-width:1' }, f.svg);
    el('text', { x: xs(t), y: f.H - 6, 'text-anchor': 'middle', style: TXT(11), text: o.fmt(t) }, f.svg);
  });
  o.labels.forEach((lab, i) => {
    const y = f.y1 + i * (bh + gap), v = o.values[i];
    el('text', { x: 0, y: y + bh / 2 + 4, style: TXT(13, 'var(--text-primary)', 600), text: lab }, f.svg);
    if (Math.abs(v) > 1e-9) {
      const b = hBar(f.svg, x0, xs(v), y, bh, `fill:${v >= 0 ? o.colorPos : o.colorNeg}`);
      hoverable(b, () => `<div class="t">${lab}</div>` + trow(o.tipLabel || 'Value', o.fmt(v)));
    }
    el('text', { x: v >= 0 ? xs(v) + 6 : xs(v) - 6, y: y + bh / 2 + 4, 'text-anchor': v >= 0 ? 'start' : 'end', style: TXT(12, 'var(--text-secondary)', 600), text: Math.abs(v) > 1e-9 ? o.fmt(v) : '0' }, f.svg);
  });
  el('line', { x1: x0, x2: x0, y1: f.y1 - 2, y2: f.y0, style: 'stroke:var(--axis);stroke-width:1.5' }, f.svg);
  return f;
}

// ------------------------------------------------------------------ 100% stacked weight bars
function weightBars(container, rows, names, legendTarget) {   // rows: [[label, weights[]]]
  const narrow = container.clientWidth < 560;
  const longest = Math.max(...rows.map(([lab, , short]) => (narrow && short ? short : lab).length));
  const labW = Math.min(narrow ? 110 : 300, longest * 7.4 + 14), bh = 26, gap = 14;
  const f = frame(container, rows.length * (bh + gap) + 4, { l: labW, r: 8, t: 4, b: 0 });
  const xs = lin(0, 1, f.x0, f.x1);
  rows.forEach(([lab, w, short], r) => {
    const y = 4 + r * (bh + gap);
    el('text', { x: 0, y: y + bh / 2 + 4, style: TXT(13, 'var(--text-primary)', 600), text: narrow && short ? short : lab }, f.svg);
    let acc = 0;
    w.forEach((wi, i) => {
      const x = xs(acc), wd = Math.max(0, xs(acc + wi) - x - 2);
      acc += wi;
      if (wd <= 0.5) return;
      const seg = el('rect', { x, y, width: wd, height: bh, rx: 4, style: `fill:${SERIES[i]}` }, f.svg);
      const ink = inkFor(cssVar(`--series-${i + 1}`));
      if (wd > 36) el('text', { x: x + wd / 2, y: y + bh / 2 + 4, 'text-anchor': 'middle', style: `fill:${ink};font-size:12px;font-weight:600;pointer-events:none`, text: pct(wi, 0) }, f.svg);
      hoverable(seg, () => `<div class="t">${lab}</div>` + trow(names[i], pct(wi, 1), SERIES[i]));
    });
  });
  if (legendTarget) legend(legendTarget, names.map((nm, i) => [nm, SERIES[i]]));
}

// ------------------------------------------------------------------ colour helpers & tables
function mix(c1, c2, t) {
  const h = s => [1, 3, 5].map(i => parseInt(s.slice(i, i + 2), 16));
  const a = h(c1), b = h(c2);
  return '#' + a.map((v, i) => Math.round(v + (b[i] - v) * t).toString(16).padStart(2, '0')).join('');
}
function inkFor(hex) {
  if (!hex || hex[0] !== '#') return '#0b0b0b';
  const [r, g, b] = [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16) / 255);
  return 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.45 ? '#0b0b0b' : '#ffffff';
}
function divColor(v, scale = 1) {     // diverging fill, v in [-scale, scale]
  const t = Math.min(1, Math.abs(v) / scale) * 0.85;
  return v >= 0 ? mix(cssVar('--div-mid'), cssVar('--div-pos'), t) : mix(cssVar('--div-mid'), cssVar('--div-neg'), t);
}
function heatTable(target, labels, M, { scale = 1, fmtFn = v => fmt(v), corner = '' } = {}) {
  $(target).innerHTML = `<thead><tr><th>${corner}</th>${labels.map(l => `<th>${l}</th>`).join('')}</tr></thead><tbody>` +
    M.map((r, i) => `<tr><td class="tkr">${labels[i]}</td>${r.map(v => { const bg = divColor(v, scale); return `<td style="background:${bg};color:${inkFor(bg)}">${fmtFn(v)}</td>`; }).join('')}</tr>`).join('') + '</tbody>';
}
function scaleKey(target, scale = 1, lo = '−1', hi = '+1') {
  $(target).innerHTML = `<span>${lo}</span><span class="bar" style="background:linear-gradient(90deg,${divColor(-scale, scale)},${divColor(0, scale)},${divColor(scale, scale)})"></span><span>${hi}</span>`;
}
/* simple table: head = [..], rows = [[..]], opts.cls per column fn */
function table(target, head, rows, { emRows = [], firstLeft = true } = {}) {
  $(target).innerHTML = `<thead><tr>${head.map(h => `<th>${h}</th>`).join('')}</tr></thead><tbody>` +
    rows.map((r, i) => `<tr class="${emRows.includes(i) ? 'em' : ''}">${r.map(c => `<td>${c}</td>`).join('')}</tr>`).join('') + '</tbody>';
}
const tile = (label, value, detail, cls = '') => `<div class="kpi ${cls}"><div class="label">${label}</div><div class="value">${value}</div><div class="detail">${detail || ''}</div></div>`;
const esc = s => String(s ?? '').replace(/[&<>]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));

// ------------------------------------------------------------------ page shell
/* segmented control: buttons with data-<attr>; marks the clicked one .on */
function wireSeg(id, attr, fn) {
  $(id).querySelectorAll('button').forEach(b => b.addEventListener('click', () => {
    $(id).querySelectorAll('button').forEach(x => x.classList.toggle('on', x === b));
    fn(b.dataset[attr]);
  }));
}

function siteNav(active) {
  const nav = document.querySelector('.site-nav');
  if (!nav) return;
  nav.innerHTML = `<a href="index.html" class="${active === 0 ? 'on' : ''}">Overview</a>` +
    UNITS.map(u => `<a href="${u.href}" class="${active === u.n ? 'on' : ''}" title="${u.short}: ${u.title}">${u.short}</a>`).join('');
  const pager = document.querySelector('.pager');
  if (pager && active) {
    const i = UNITS.findIndex(u => u.n === active), prev = UNITS[i - 1], next = UNITS[i + 1];
    pager.innerHTML = (prev ? `<a href="${prev.href}">← ${prev.short}: ${prev.title}</a>` : '<a href="index.html">← Overview</a>') +
      (next ? `<a href="${next.href}" class="next">${next.short}: ${next.title} →</a>` : '<a href="index.html" class="next">Back to overview →</a>');
  }
}
function initTheme(onChange) {
  try { const t = localStorage.getItem('ef-theme'); if (t) document.documentElement.setAttribute('data-theme', t); } catch (e) { /* storage unavailable */ }
  const btn = $('themeToggle');
  if (btn) btn.addEventListener('click', () => {
    const cur = document.documentElement.getAttribute('data-theme') || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
    const t = cur === 'dark' ? 'light' : 'dark';
    try { localStorage.setItem('ef-theme', t); } catch (e) { /* ignore */ }
    document.documentElement.setAttribute('data-theme', t);
    onChange && onChange();
  });
  matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => onChange && onChange());
  let rt;
  addEventListener('resize', () => { clearTimeout(rt); rt = setTimeout(() => onChange && onChange(), 150); });
}
async function loadJson(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
  return r.json();
}
function loadError(err) {
  document.querySelector('main').insertAdjacentHTML('afterbegin',
    `<div class="card"><b>Could not load data.</b> If you opened this file directly from disk, serve the folder over HTTP (for example <code>python -m http.server</code>) or view it on GitHub Pages.<br><small>${esc(err)}</small></div>`);
}
