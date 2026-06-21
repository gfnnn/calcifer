// Roster section: a filterable/sortable member table (master) with a per-character
// profile drill-down (detail) that also hosts the manual-field editor. Master and
// detail are selected by the hash sub-route (#roster vs #roster/<name>).

import { api, send } from '../api.js';
import { card, panel, table, bar, esc, pill, toast } from '../components.js';

const CLASSES = [
  'Death Knight', 'Demon Hunter', 'Druid', 'Evoker', 'Hunter', 'Mage', 'Monk',
  'Paladin', 'Priest', 'Rogue', 'Shaman', 'Warlock', 'Warrior',
];
const ROLES = ['TANK', 'HEALER', 'DPS'];
const SORTS = [
  ['mythic_plus_score', 'M+ score'],
  ['item_level', 'Item level'],
  ['best_parse_avg', 'Parse'],
  ['guild_rank', 'Guild rank'],
  ['name', 'Name'],
];

// Live filter state for the table view.
const filter = { q: '', class: '', role: '', sort: 'mythic_plus_score' };

function fmt(v) { return (v == null || v === '') ? '—' : v; }
function pct(v) { return v == null ? '—' : `${Math.round(v)}%`; }

// --- master: the member table ------------------------------------------------

function optionList(items, current) {
  return items.map(o => {
    const [val, label] = Array.isArray(o) ? o : [o, o];
    const sel = val === current ? ' selected' : '';
    return `<option value="${esc(val)}"${sel}>${esc(label)}</option>`;
  }).join('');
}

async function loadRows() {
  const rows = document.getElementById('roster-rows');
  if (!rows) return;
  const params = new URLSearchParams();
  if (filter.q) params.set('q', filter.q);
  if (filter.class) params.set('class', filter.class);
  if (filter.role) params.set('role', filter.role);
  params.set('sort', filter.sort);
  const members = await api(`/api/members?${params.toString()}`);
  if (!members) return;
  if (!members.length) {
    rows.innerHTML = '<p class="muted">No characters match.</p>';
    return;
  }
  rows.innerHTML = table([
    { label: 'Name', html: m =>
      `<a href="#roster/${encodeURIComponent(m.character_name)}">${esc(m.character_name)}</a>` },
    { label: 'Class / spec', html: m => {
      const label = esc([m.spec, m.class].filter(Boolean).join(' '));
      return m.class_color ? `<span style="color:${esc(m.class_color)}">${label}</span>` : label;
    } },
    { label: 'Role', key: 'role' },
    { label: 'ilvl', num: true, html: m => fmt(m.item_level) },
    { label: 'M+', num: true, html: m => m.mythic_plus_color
      ? `<span style="color:${esc(m.mythic_plus_color)};font-weight:600">${fmt(m.mythic_plus_score)}</span>`
      : fmt(m.mythic_plus_score) },
    { label: 'Parse', num: true, html: m => fmt(m.best_parse_avg) },
    { label: 'Team', html: m => m.raid_team ? pill(m.raid_team) : '<span class="muted">—</span>' },
  ], members);
}

function renderTable(main) {
  let timer = null;
  main.innerHTML = panel('Roster', `
    <div class="row" style="margin-bottom:.8rem">
      <div class="field"><label>Search</label><input id="r-q" type="search" placeholder="name…" value="${esc(filter.q)}"></div>
      <div class="field"><label>Class</label><select id="r-class"><option value="">All</option>${optionList(CLASSES, filter.class)}</select></div>
      <div class="field"><label>Role</label><select id="r-role"><option value="">All</option>${optionList(ROLES, filter.role)}</select></div>
      <div class="field"><label>Sort</label><select id="r-sort">${optionList(SORTS, filter.sort)}</select></div>
    </div>
    <div id="roster-rows" class="muted">Loading…</div>`);

  const q = document.getElementById('r-q');
  q.addEventListener('input', () => {
    clearTimeout(timer);
    timer = setTimeout(() => { filter.q = q.value.trim(); loadRows(); }, 250);
  });
  document.getElementById('r-class').addEventListener('change', e => { filter.class = e.target.value; loadRows(); });
  document.getElementById('r-role').addEventListener('change', e => { filter.role = e.target.value; loadRows(); });
  document.getElementById('r-sort').addEventListener('change', e => { filter.sort = e.target.value; loadRows(); });
  loadRows();
}

// --- detail: one character's profile ----------------------------------------

function readinessPanel(r) {
  if (!r) return '';
  const comps = Object.entries(r.components || {}).map(([k, v]) =>
    `<tr><td>${esc(k)}</td><td>${bar(v * 100)}</td><td class="num">${Math.round(v * 100)}%</td></tr>`
  ).join('');
  const flags = (r.flags || []).map(f => pill(f, 'warn')).join(' ') || '<span class="ok">No flags</span>';
  return panel(`Readiness — ${r.score == null ? '—' : Math.round(r.score)}/100`,
    `<table>${comps}</table><div style="margin-top:.6rem">${flags}</div>`);
}

function identityHeader(d) {
  const m = d.member;
  const id = d.identity || {};
  const bits = [
    m.spec && m.class ? `${esc(m.spec)} ${esc(m.class)}` : esc(m.class),
    m.role, id.rank_name ? `rank ${esc(id.rank_name)}` : null,
    id.join_date ? `joined ${esc(id.join_date)}` : null,
    d.active ? '<span class="ok">active</span>' : '<span class="muted">inactive</span>',
  ].filter(Boolean).join(' · ');
  const alts = (d.alts || []).length ? `<div class="muted">Alts: ${d.alts.map(esc).join(', ')}</div>` : '';
  const armory = m.profile_url
    ? ` <a href="${esc(m.profile_url)}" target="_blank" rel="noopener">armory ↗</a>` : '';
  return panel(`${esc(m.character_name)}${armory}`, `<div>${bits}</div>${alts}`);
}

function vitals(m) {
  return '<div class="cards">'
    + card('Item level', fmt(m.item_level))
    + card('M+ score', m.mythic_plus_color
      ? `<span style="color:${esc(m.mythic_plus_color)}">${fmt(m.mythic_plus_score)}</span>`
      : fmt(m.mythic_plus_score))
    + card('Parse', fmt(m.best_parse_avg))
    + card('Vault', m.vault_slots_filled == null ? '—' : `${m.vault_slots_filled}/9`)
    + '</div>';
}

function wishlistPanel(w) {
  if (!w || !w.total) return '';
  const items = (w.items || []).slice(0, 12).map(it =>
    `<tr><td>${esc(it.item_name || it.item_id)}</td><td class="num">${fmt(it.upgrade_pct)}%</td>`
    + `<td>${it.obtained ? '<span class="ok">✓</span>' : ''}</td></tr>`).join('');
  return panel(`Wishlist — ${w.obtained}/${w.total} (${pct(w.pct)})`,
    bar(w.pct) + (items ? `<table style="margin-top:.6rem">${items}</table>` : ''));
}

function lootPanel(loot) {
  if (!loot || !loot.length) return '';
  return panel('Recent loot', table([
    { label: 'Item', key: 'item_name' },
    { label: 'Boss', key: 'boss' },
    { label: 'Difficulty', key: 'difficulty' },
    { label: 'Response', key: 'response' },
    { label: 'When', key: 'awarded_at' },
  ], loot.slice(0, 20)));
}

function editorPanel(m) {
  return panel('Manual fields', `
    <div class="row">
      <div class="field"><label>Discord ID</label><input id="e-discord" value="${esc(fmt2(m.discord_id))}"></div>
      <div class="field"><label>Raid team</label><input id="e-team" value="${esc(fmt2(m.raid_team))}"></div>
      <div class="field"><label>Officer role</label><input id="e-officer" value="${esc(fmt2(m.officer_role))}"></div>
      <div class="field"><label>Main character (alt link)</label><input id="e-main" placeholder="leave blank if this is a main"></div>
    </div>
    <div class="field"><label>Notes</label><input id="e-notes" value="${esc(fmt2(m.notes))}"></div>
    <button id="e-save" data-id="${m.id}">Save</button>
    <span class="muted" style="margin-left:.6rem">${m.is_main === false ? 'currently an alt' : 'currently a main'}</span>`);
}
function fmt2(v) { return v == null ? '' : v; }

async function saveManual(id, name) {
  const body = {
    discord_id: document.getElementById('e-discord').value,
    raid_team: document.getElementById('e-team').value,
    officer_role: document.getElementById('e-officer').value,
    notes: document.getElementById('e-notes').value,
  };
  const main = document.getElementById('e-main').value.trim();
  if (main) body.main_character = main;  // only relink when explicitly given
  try {
    const r = await send('PUT', `/api/admin/members/${id}`, body);
    if (r) { toast('Saved', 'ok'); renderProfile(document.getElementById('app'), name); }
  } catch (e) { toast(String(e.message || e), 'bad'); }
}

async function renderProfile(main, name) {
  main.innerHTML = '<div class="muted">Loading…</div>';
  const d = await api(`/api/members/${encodeURIComponent(name)}/detail`);
  if (!d) return;
  const m = d.member;
  main.innerHTML = `<p><a href="#roster">← Roster</a></p>`
    + identityHeader(d)
    + vitals(m)
    + readinessPanel(d.readiness)
    + (d.attendance && d.attendance.scheduled
        ? panel('Attendance', `${d.attendance.present}/${d.attendance.scheduled} raids · ${pct(d.attendance.pct)}`)
        : '')
    + wishlistPanel(d.wishlist)
    + lootPanel(d.loot)
    + editorPanel(m);
  document.getElementById('e-save').addEventListener('click', e =>
    saveManual(e.target.dataset.id, m.character_name));
}

// --- entry -------------------------------------------------------------------

export function render(main, sub) {
  if (sub) renderProfile(main, decodeURIComponent(sub));
  else renderTable(main);
}
