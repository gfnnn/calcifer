// Raid section: Readiness / Sign-ups / Composition, selected by the hash
// sub-route (#raid/readiness, #raid/signups, #raid/composition).

import { api } from '../api.js';
import { card, panel, table, bar, esc, pill, tabs } from '../components.js';

const TABS = [
  ['readiness', 'Readiness'],
  ['signups', 'Sign-ups'],
  ['composition', 'Composition'],
  ['attendance', 'Attendance'],
];

const TREND = { down: '<span class="bad">▼ slipping</span>',
                up: '<span class="ok">▲ improving</span>',
                steady: '<span class="muted">steady</span>' };

function fmt(v) { return (v == null || v === '') ? '—' : v; }
function pct(v) { return v == null ? '—' : `${Math.round(v)}%`; }

// Team option list is cached from the first unfiltered Readiness load, so
// filtering by team doesn't lose the set of teams.
let teamOptions = null;
let currentTeam = '';

function readinessTable(rows) {
  return table([
    { label: 'Name', html: r =>
      `<a href="#roster/${encodeURIComponent(r.name)}">${esc(r.name)}</a>` },
    { label: 'Class / spec', html: r => esc([r.spec, r.class].filter(Boolean).join(' ')) },
    { label: 'Role', key: 'role' },
    { label: 'ilvl', num: true, html: r => fmt(r.item_level) },
    { label: 'M+', num: true, html: r => fmt(r.mythic_plus_score) },
    { label: 'Parse', num: true, html: r => fmt(r.best_parse_avg) },
    { label: 'Ready', num: true, html: r => r.readiness == null ? '—' : Math.round(r.readiness) },
    { label: 'Flags', html: r => (r.flags || []).map(f => pill(f, 'warn')).join(' ') },
  ], rows);
}

async function loadReadinessRows() {
  const host = document.getElementById('raid-rows');
  if (!host) return;
  const qs = currentTeam ? `?team=${encodeURIComponent(currentTeam)}` : '';
  const d = await api(`/api/raid${qs}`);
  if (!d) return;
  host.innerHTML = d.rows.length ? readinessTable(d.rows) : '<p class="muted">No raiders.</p>';
}

async function renderReadiness(main) {
  // Seed the team list from an unfiltered fetch the first time.
  const d = await api('/api/raid');
  if (!d) return;
  if (teamOptions === null) {
    teamOptions = [...new Set(d.rows.map(r => r.raid_team).filter(Boolean))].sort();
  }
  const opts = ['<option value="">All teams</option>']
    .concat(teamOptions.map(t =>
      `<option value="${esc(t)}"${t === currentTeam ? ' selected' : ''}>${esc(t)}</option>`))
    .join('');
  main.innerHTML = panel('Readiness', `
    <div class="row" style="margin-bottom:.8rem">
      <div class="field"><label>Team</label><select id="raid-team">${opts}</select></div>
    </div>
    <div id="raid-rows" class="muted">Loading…</div>`);
  document.getElementById('raid-team').addEventListener('change', e => {
    currentTeam = e.target.value;
    loadReadinessRows();
  });
  loadReadinessRows();
}

async function renderSignups(main) {
  const d = await api('/api/roster');
  if (!d) return;
  const groups = d.groups || {};
  const keys = Object.keys(groups);
  if (!keys.length) {
    main.innerHTML = panel('Sign-ups', '<p class="muted">No sign-ups for the next raid yet.</p>');
    return;
  }
  const title = d.raid && d.raid.title ? ` — ${esc(d.raid.title)}` : '';
  const blocks = keys.map(bucket => {
    const rows = groups[bucket].map(r =>
      `<tr><td>${esc(r.name)}</td><td>${esc(fmt(r.class))}</td><td>${esc(fmt(r.role))}</td></tr>`
    ).join('');
    return panel(`${bucket} (${groups[bucket].length})`, `<table>${rows}</table>`);
  }).join('');
  main.innerHTML = `<h2 style="margin-top:0">Next raid${title}</h2>${blocks}`;
}

async function renderComposition(main) {
  const d = await api('/api/raid');
  if (!d) return;
  const roleCards = Object.entries(d.role_counts || {})
    .map(([role, n]) => card(role, n)).join('');
  const cov = d.buff_coverage || {};
  const rows = Object.entries(cov).map(([buff, info]) => {
    const ok = info.covered;
    const who = (info.providers || []).join(', ');
    return `<tr><td>${esc(buff)}</td>`
      + `<td>${ok ? '<span class="ok">covered</span>' : '<span class="bad">missing</span>'}</td>`
      + `<td class="muted">${esc(who)}</td></tr>`;
  }).join('');
  const missing = (d.missing_buffs || []).map(b => pill(b, 'warn')).join(' ')
    || '<span class="ok">All raid buffs covered</span>';
  main.innerHTML = `<div class="cards">${roleCards}</div>`
    + panel('Buff coverage',
        `<table><thead><tr><th>Buff</th><th>Status</th><th>Providers</th></tr></thead>`
        + `<tbody>${rows}</tbody></table><div style="margin-top:.6rem">${missing}</div>`);
}

async function renderAttendance(main) {
  const d = await api('/api/analytics/attendance');
  if (!d) return;
  if (!d.event_count) {
    main.innerHTML = panel('Attendance',
      '<p class="muted">No raid events recorded yet (attendance comes from WoW Audit).</p>');
    return;
  }
  const raids = panel('Recent raids', table([
    { label: 'Date', html: e => esc((e.started_at || '').slice(0, 10)) },
    { label: 'Instance', html: e => esc([e.instance, e.difficulty].filter(Boolean).join(' · ')) },
    { label: 'Present', num: true, html: e => `${e.present}/${e.roster}` },
  ], d.raids));
  const people = panel('Reliability (worst first)', table([
    { label: 'Raider', html: c => `<a href="#roster/${encodeURIComponent(c.name)}">${esc(c.name)}</a>` },
    { label: 'Attendance', num: true, html: c => pct(c.pct) },
    { label: 'Present', num: true, html: c => `${c.present}/${c.scheduled}` },
    { label: 'Recent', num: true, html: c => pct(c.recent_pct) },
    { label: 'Trend', html: c => c.trend ? TREND[c.trend] : '<span class="muted">—</span>' },
  ], d.characters));
  main.innerHTML = raids + people;
}

export async function render(main, sub) {
  const active = TABS.some(([k]) => k === sub) ? sub : 'readiness';
  main.innerHTML = tabs('raid', TABS, active) + '<div id="raid-body" class="muted">Loading…</div>';
  const body = document.getElementById('raid-body');
  if (active === 'signups') await renderSignups(body);
  else if (active === 'composition') await renderComposition(body);
  else if (active === 'attendance') await renderAttendance(body);
  else await renderReadiness(body);
}
