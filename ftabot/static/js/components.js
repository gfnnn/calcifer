// Vanilla, build-free render helpers. Each is a pure string-builder (or a small
// DOM helper) so sections stay declarative: fetch a dict, map it through these.

// HTML-escape any value (mirrors the old _esc).
export function esc(s) {
  const d = document.createElement('div');
  d.textContent = (s == null) ? '' : String(s);
  return d.innerHTML;
}

// Inline <code> or an em-dash for empties (mirrors the old _code).
export function code(s) {
  return s ? `<code>${esc(s)}</code>` : '<span class="muted">&mdash;</span>';
}

// A stat card. opts: { delta, deltaDir: 'up'|'down', spark, sub }.
export function card(label, value, opts = {}) {
  const { delta, deltaDir, spark, sub } = opts;
  const d = (delta == null || delta === '')
    ? '' : `<span class="delta ${deltaDir || ''}">${esc(delta)}</span>`;
  const s = spark ? `<div class="spark">${spark}</div>` : '';
  const subline = sub ? `<div class="muted" style="font-size:.78rem">${esc(sub)}</div>` : '';
  return `<div class="card"><div class="big">${esc(value)}${d}</div>`
    + `<div class="lbl">${esc(label)}</div>${subline}${s}</div>`;
}

// A bordered section with a heading.
export function panel(title, innerHTML) {
  return `<section class="panel"><h2>${esc(title)}</h2>${innerHTML}</section>`;
}

// A progress bar; pct is 0..100 (clamped).
export function bar(pct) {
  const w = pct == null ? 0 : Math.max(0, Math.min(100, pct));
  return `<div class="bar"><span style="width:${w.toFixed(0)}%"></span></div>`;
}

export function pill(text, cls = '') {
  return `<span class="pill ${cls}">${esc(text)}</span>`;
}

export function tag(text, cls = '') {
  return `<span class="tag ${cls}">${esc(text)}</span>`;
}

// Sub-tab bar for a section. `section` is the route key (e.g. 'raid'); `items`
// are [key, label] pairs; `active` is the live sub-route. Each tab is a hash
// link so the router re-renders the section with the new sub.
export function tabs(section, items, active) {
  const links = items.map(([key, label]) => {
    const cls = key === active ? ' active' : '';
    return `<button class="${cls.trim()}" onclick="location.hash='#${section}/${key}'">${esc(label)}</button>`;
  }).join('');
  return `<div class="tabs">${links}</div>`;
}

// A table. cols: [{label, key, num?, html?}], rows: array of objects.
// A column's html(row) wins over key lookup for custom cells.
export function table(cols, rows) {
  const head = cols.map(c => `<th class="${c.num ? 'num' : ''}">${esc(c.label)}</th>`).join('');
  const body = rows.map(row => {
    const cells = cols.map(c => {
      const v = c.html ? c.html(row) : esc(row[c.key]);
      return `<td class="${c.num ? 'num' : ''}">${v}</td>`;
    }).join('');
    return `<tr>${cells}</tr>`;
  }).join('');
  return `<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

// A dependency-free inline-SVG sparkline (mirrors the Python _sparkline). If the
// server already returned a `points` polyline string, prefer sparklinePoints.
export function sparkline(values, { width = 200, height = 36, stroke = '#ff7043' } = {}) {
  const known = values.filter(v => v != null);
  if (known.length < 2) return '<span class="muted" style="font-size:.78rem">no trend yet</span>';
  const lo = Math.min(...known);
  const hi = Math.max(...known);
  const span = (hi - lo) || 1;
  const step = width / Math.max(1, values.length - 1);
  const pts = values
    .map((v, i) => (v == null ? null : `${(i * step).toFixed(1)},${(height - ((v - lo) / span) * (height - 4) - 2).toFixed(1)}`))
    .filter(Boolean)
    .join(' ');
  return sparklinePoints(pts, { width, height, stroke });
}

// Render a precomputed polyline point string (lets the geometry live in Python).
export function sparklinePoints(points, { width = 200, height = 36, stroke = '#ff7043' } = {}) {
  if (!points) return '<span class="muted" style="font-size:.78rem">no trend yet</span>';
  return `<svg width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" `
    + `preserveAspectRatio="none"><polyline fill="none" stroke="${stroke}" `
    + `stroke-width="2" points="${points}"/></svg>`;
}

// Transient toast in #toast-host. kind: 'ok' | 'bad' | ''.
export function toast(msg, kind = '') {
  const host = document.getElementById('toast-host');
  if (!host) return;
  const el = document.createElement('div');
  el.className = `toast ${kind}`;
  el.textContent = msg;
  host.appendChild(el);
  setTimeout(() => el.remove(), 3200);
}
