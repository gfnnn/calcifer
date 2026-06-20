// Loot section: Fairness / Recent / Priority, selected by the hash sub-route
// (#loot/fairness, #loot/recent, #loot/priority).

import { api } from '../api.js';
import { card, panel, table, esc, pill, tabs } from '../components.js';

const TABS = [
  ['fairness', 'Fairness'],
  ['equity', 'Equity'],
  ['wishlist', 'Wishlist'],
  ['recent', 'Recent'],
  ['priority', 'Priority tool'],
];

function fmt(v) { return (v == null || v === '') ? '—' : v; }
function pct(v) { return v == null ? '—' : `${Math.round(v)}%`; }

async function renderFairness(main) {
  const d = await api('/api/loot');
  if (!d) return;
  const rows = d.fairness || [];
  if (!rows.length) { main.innerHTML = panel('Fairness', '<p class="muted">No loot recorded yet.</p>'); return; }
  main.innerHTML = panel(`Fairness — ${d.total_awards} awards`, table([
    { label: 'Name', html: r =>
      `<a href="#roster/${encodeURIComponent(r.name)}">${esc(r.name)}</a>` },
    { label: 'Won', num: true, key: 'items_won' },
    { label: 'Raids', num: true, html: r => fmt(r.raids_attended) },
    { label: 'Per raid', num: true, html: r => fmt(r.items_per_raid) },
    { label: 'Attend', num: true, html: r => pct(r.attendance_pct) },
    { label: 'Days since', num: true, html: r =>
      r.days_since_last_loot == null ? '—' : Math.round(r.days_since_last_loot) },
  ], rows));
}

async function renderRecent(main) {
  const d = await api('/api/loot');
  if (!d) return;
  const rows = d.recent || [];
  if (!rows.length) { main.innerHTML = panel('Recent', '<p class="muted">No loot recorded yet.</p>'); return; }
  main.innerHTML = panel('Recent awards', table([
    { label: 'Item', key: 'item_name' },
    { label: 'Character', key: 'character_name' },
    { label: 'Boss', html: r => fmt(r.boss) },
    { label: 'Difficulty', html: r => fmt(r.difficulty) },
    { label: 'Response', html: r => fmt(r.response) },
    { label: 'When', html: r => fmt(r.awarded_at) },
  ], rows));
}

async function runPriority() {
  const item = document.getElementById('loot-item').value.trim();
  const host = document.getElementById('loot-prio');
  if (!item) { host.innerHTML = '<p class="muted">Enter an item name to rank wishlisters.</p>'; return; }
  const d = await api(`/api/loot/priority?item=${encodeURIComponent(item)}`);
  if (!d) return;
  if (!d.ranked.length) { host.innerHTML = '<p class="muted">No one has this item wishlisted.</p>'; return; }
  host.innerHTML = table([
    { label: 'Character', html: r =>
      `<a href="#roster/${encodeURIComponent(r.character)}">${esc(r.character)}</a>` },
    { label: 'Priority', num: true, html: r => r.priority == null ? '—' : r.priority.toFixed(2) },
    { label: 'BiS', html: r => r.is_bis ? '<span class="ok">✓</span>' : '' },
    { label: 'Upgrade', num: true, html: r => fmt(r.upgrade_pct) + (r.upgrade_pct == null ? '' : '%') },
    { label: 'Attend', num: true, html: r => pct(r.attendance_pct) },
    { label: 'Recent wins', num: true, html: r => fmt(r.recent_wins) },
  ], d.ranked);
}

function renderPriority(main) {
  main.innerHTML = panel('Loot priority', `
    <div class="row" style="margin-bottom:.8rem">
      <div class="field"><label>Item name</label><input id="loot-item" placeholder="e.g. Ember Greatsword"></div>
      <button id="loot-go">Rank</button>
    </div>
    <div id="loot-prio" class="muted">Enter an item name to rank wishlisters.</div>`);
  const go = () => runPriority();
  document.getElementById('loot-go').addEventListener('click', go);
  document.getElementById('loot-item').addEventListener('keydown', e => { if (e.key === 'Enter') go(); });
}

function breakdownPanel(title, rows) {
  if (!rows || !rows.length) return '';
  const pills = rows.map(r => pill(`${esc(r.key)} ${r.count}`)).join(' ');
  return panel(title, pills);
}

async function renderEquity(main) {
  const d = await api('/api/analytics/loot');
  if (!d) return;
  const cards = '<div class="cards">'
    + card('Total awards', d.total_awards)
    + card('Avg / player', d.avg_per_player)
    + card('Never won', (d.never_won || []).length)
    + '</div>';
  const drought = panel('Longest drought', table([
    { label: 'Player', html: r => `<a href="#roster/${encodeURIComponent(r.name)}">${esc(r.name)}</a>` },
    { label: 'Days since', num: true, html: r => r.days_since_last_loot == null ? '—' : Math.round(r.days_since_last_loot) },
    { label: 'Won', num: true, key: 'items_won' },
    { label: 'Raids', num: true, html: r => fmt(r.raids_attended) },
  ], d.drought));
  const never = (d.never_won || []).length
    ? panel('No loot yet', d.never_won.map(n => pill(n, 'warn')).join(' ')) : '';
  const bd = (d.breakdown || {});
  main.innerHTML = cards + drought + never
    + breakdownPanel('By slot', bd.slot)
    + breakdownPanel('By difficulty', bd.difficulty)
    + breakdownPanel('By response', bd.response);
}

async function renderWishlist(main) {
  const d = await api('/api/analytics/wishlist');
  if (!d) return;
  const contention = (d.contention || []).length
    ? panel('Most contested drops', table([
        { label: 'Item', html: r => esc(r.item_name || r.item_id) },
        { label: 'Slot', html: r => fmt(r.slot) },
        { label: 'Wishlisters', num: true, key: 'wishlisters' },
        { label: 'BiS', num: true, key: 'bis' },
        { label: 'Open', num: true, key: 'open' },
        { label: 'Avg upgrade', num: true, html: r => r.avg_upgrade == null ? '—' : r.avg_upgrade + '%' },
      ], d.contention))
    : panel('Most contested drops', '<p class="muted">No wishlists uploaded yet.</p>');
  const coverage = (d.coverage || []).length
    ? panel('BiS completion (lowest first)', table([
        { label: 'Player', html: r => `<a href="#roster/${encodeURIComponent(r.name)}">${esc(r.name)}</a>` },
        { label: 'Obtained', num: true, html: r => `${r.obtained}/${r.total}` },
        { label: 'Complete', num: true, html: r => pct(r.pct) },
      ], d.coverage))
    : '';
  main.innerHTML = contention + coverage;
}

export async function render(main, sub) {
  const active = TABS.some(([k]) => k === sub) ? sub : 'fairness';
  main.innerHTML = tabs('loot', TABS, active) + '<div id="loot-body" class="muted">Loading…</div>';
  const body = document.getElementById('loot-body');
  if (active === 'recent') await renderRecent(body);
  else if (active === 'priority') renderPriority(body);
  else if (active === 'equity') await renderEquity(body);
  else if (active === 'wishlist') await renderWishlist(body);
  else await renderFairness(body);
}
