// Settings section: Runtime overrides | Sync trigger | System status. Ported
// from the inline _render_settings / _render_admin / _render_overview.

import { api, send } from '../api.js';
import { card, panel, esc, code, tag, toast, tabs } from '../components.js';

const TABS = [['runtime', 'Runtime'], ['sync', 'Sync'], ['status', 'System status']];

// --- Runtime -----------------------------------------------------------------

async function loadRuntime(host) {
  const items = await api('/api/settings');
  if (!items) return;
  host.innerHTML = '<p class="muted">These override the environment defaults live (no redeploy). '
    + 'Clearing a value reverts it to the environment.</p><div id="rt-list">'
    + items.map(s => {
        let input;
        if (s.kind === 'choice') {
          input = `<select id="in_${s.key}">`
            + s.choices.map(c => `<option value="${esc(c)}"${String(s.effective) === c ? ' selected' : ''}>${esc(c)}</option>`).join('')
            + '</select>';
        } else {
          input = `<input id="in_${s.key}" value="${esc(s.override == null ? '' : s.override)}" placeholder="${esc(s.env == null ? '(unset)' : s.env)}">`;
        }
        const ov = s.override != null ? tag('override', 'on') : '<span class="muted">env default</span>';
        return `<div class="panel"><h2>${esc(s.label)} ${ov}</h2><p class="muted">${esc(s.help)}</p>`
          + `<div class="row"><div class="field"><label>Value</label>${input}</div>`
          + `<button data-key="${esc(s.key)}" data-act="save">Save</button>`
          + `<button class="ghost" data-key="${esc(s.key)}" data-act="revert">Revert to env</button></div>`
          + `<p class="muted">Environment default: ${code(s.env)} · effective now: ${code(s.effective)}</p></div>`;
      }).join('') + '</div>';

  // Attach to the freshly-built inner element so reloads don't stack listeners.
  document.getElementById('rt-list').addEventListener('click', async e => {
    const b = e.target.closest('button[data-act]');
    if (!b) return;
    const key = b.dataset.key;
    if (b.dataset.act === 'revert') {
      if (await send('DELETE', `/api/settings/${key}`)) loadRuntime(host);
      return;
    }
    const val = document.getElementById(`in_${key}`).value;
    if (await send('PUT', '/api/settings', { settings: { [key]: val } })) loadRuntime(host);
  });
}

// --- Sync --------------------------------------------------------------------

let syncTimer = null;

function syncView(host, s) {
  const line = s.running ? 'Sync running…' : esc(s.last_summary || s.last_error || 'idle');
  host.innerHTML = panel('Manual sync', `
    <p>Pull a fresh roster from the configured APIs. Safe to run anytime.</p>
    <button id="syncbtn"${s.running ? ' disabled' : ''}>Sync now</button>
    <p class="muted" id="syncst">${line}${s.last_finished ? ' · last finished ' + esc(s.last_finished) : ''}</p>`);
  document.getElementById('syncbtn').onclick = async () => {
    const r = await send('POST', '/api/sync');
    if (r) { document.getElementById('syncbtn').disabled = true; pollSync(host); }
  };
}

async function pollSync(host) {
  const s = await api('/api/sync/status');
  if (!s) return;
  const st = document.getElementById('syncst');
  if (s.running) {
    if (st) st.textContent = 'Sync running…';
    syncTimer = setTimeout(() => pollSync(host), 1500);
  } else {
    loadSync(host);
  }
}

async function loadSync(host) {
  if (syncTimer) { clearTimeout(syncTimer); syncTimer = null; }
  const s = await api('/api/sync/status');
  if (!s) return;
  syncView(host, s);
}

// --- System status -----------------------------------------------------------

async function loadStatus(host) {
  const d = await api('/api/overview');
  if (!d) return;
  const c = d.counts;
  const cards = '<div class="cards">'
    + card('Members', c.members)
    + card('Boards', `${c.boards} (${c.boards_promoted} live)`)
    + card('Feed rules', `${c.feed_rules} (${c.feed_rules_enabled} on)`)
    + card('Leads', c.leads)
    + card('Health', d.health == null ? '—' : Math.round(d.health))
    + card('Posting', d.posting.mode)
    + '</div>';
  const feats = Object.keys(d.features).map(k =>
    tag(k, d.features[k] ? 'on' : 'off')).join(' ');
  const s = d.sync || {};
  const sl = s.running ? 'Sync running…' : esc(s.last_summary || s.last_error || 'idle');
  host.innerHTML = cards
    + panel('Integrations & loops',
        `<p>${feats}</p><p class="muted">Green = enabled. Loop toggles come from the environment and need a restart to change.</p>`)
    + panel('Sync',
        `<p>${sl}${s.last_finished ? ' · last finished ' + esc(s.last_finished) : ''}</p>`)
    + panel('Posting guardrail',
        `<p>Mode <b>${esc(d.posting.mode)}</b> · test channel ${code(d.posting.test_channel_id)}</p>`);
}

// --- entry -------------------------------------------------------------------

export async function render(main, sub) {
  const active = TABS.some(([k]) => k === sub) ? sub : 'runtime';
  main.innerHTML = tabs('settings', TABS, active) + '<div id="set-body" class="muted">Loading…</div>';
  const body = document.getElementById('set-body');
  if (active === 'sync') await loadSync(body);
  else if (active === 'status') await loadStatus(body);
  else await loadRuntime(body);
}
