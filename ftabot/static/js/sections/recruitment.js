// Recruitment section: the lead funnel + scout capture + board config. Ported
// from the inline _render_recruiting; all endpoints already exist and are gated.

import { api, send } from '../api.js';
import { panel, table, esc, tag, toast } from '../components.js';

let DATA = {};
let pollTimer = null;

function fmt(v) { return v == null ? '' : v; }

function leadRow(l) {
  const opts = DATA.lead_states.map(s =>
    `<option value="${esc(s)}"${s === l.state ? ' selected' : ''}>${esc(s)}</option>`).join('');
  const who = `${esc(l.name)} <span class="muted">${esc(l.class_name || '')} ${esc(l.role || '')}</span>`
    + (l.lfg ? ' ' + tag('LFG', 'on') : '');
  return `<tr><td>#${l.id}</td><td>${who}</td>`
    + `<td class="num">${l.score == null ? '—' : Math.round(l.score)}</td>`
    + `<td><select data-id="${l.id}" data-act="state">${opts}</select></td>`
    + `<td><input data-id="${l.id}" data-act="note" value="${esc(l.notes || '')}" placeholder="note"></td>`
    + `<td><button class="sm danger" data-id="${l.id}" data-act="remove">Remove</button></td></tr>`;
}

function slotForm(role) {
  const s = (DATA.config.slots || {})[role] || {};
  return `<div class="row" data-role="${esc(role)}">`
    + `<div class="field"><label>${esc(role.toUpperCase())} open</label>`
    + `<input class="s_open" value="${esc(fmt(s.open))}" size="4"></div>`
    + `<div class="field"><label>min ilvl</label><input class="s_ilvl" value="${esc(fmt(s.ilvl))}" size="5"></div>`
    + `<div class="field"><label>note</label><input class="s_note" value="${esc(s.note || '')}"></div>`
    + `<button class="sm" data-role="${esc(role)}" data-act="slot">Save</button></div>`;
}

function capturePanel() {
  const cap = DATA.capture || {};
  if (!DATA.scout_available) {
    return panel('Capture',
      '<p class="muted">Live scouting needs Warcraft Logs credentials. Manage existing leads below.</p>');
  }
  return panel('Capture', `
    <div class="row">
      <div class="field"><label>Class (optional)</label><input id="capclass" placeholder="Paladin"></div>
      <button id="capbtn"${cap.running ? ' disabled' : ''}>Capture leads</button>
    </div>
    <p class="muted" id="capst">${esc(cap.running ? 'Scouting…' : (cap.last_summary || cap.last_error || 'idle'))}</p>`);
}

function configPanel() {
  const rules = (DATA.config.rules || []).map((r, i) =>
    `<li>${esc(r)} <button class="sm danger" data-idx="${i}" data-act="delrule">×</button></li>`).join('');
  return panel('Board config',
    DATA.roles.map(slotForm).join('')
    + `<h2>Trial rules</h2><ul id="rules">${rules || '<li class="muted">None (board shows defaults)</li>'}</ul>`
    + '<div class="row"><div class="field"><label>Add rule</label><input id="newrule"></div>'
    + '<button id="addrule">Add</button></div>'
    + '<h2>Pitch message</h2><div class="row"><div class="field"><label>Override (blank = default)</label>'
    + `<input id="msg" value="${esc(DATA.config.message || '')}" size="50"></div><button id="savemsg">Save</button></div>`
    + '<p><button class="ghost" id="reset">Reset all recruitment config</button></p>');
}

function leadsPanel() {
  const inner = DATA.leads.length
    ? `<table><thead><tr><th>#</th><th>Lead</th><th class="num">Score</th><th>State</th><th>Note</th><th></th></tr></thead>`
      + `<tbody>${DATA.leads.map(leadRow).join('')}</tbody></table>`
    : '<p class="muted">No leads yet. Capture some (or run <code>/recruit lead capture</code> in Discord).</p>';
  return panel('Leads', `<div id="leads">${inner}</div>`);
}

async function capture() {
  const wow_class = document.getElementById('capclass').value.trim();
  const r = await send('POST', '/api/leads/capture', wow_class ? { wow_class } : {});
  if (r) { document.getElementById('capbtn').disabled = true; poll(); }
}

async function poll() {
  const d = await api('/api/recruiting');
  if (!d) return;
  const c = d.capture || {};
  const st = document.getElementById('capst');
  if (st) st.textContent = c.running ? 'Scouting…' : (c.last_summary || c.last_error || 'idle');
  if (c.running) pollTimer = setTimeout(poll, 2500);
  else load(document.getElementById('app'));
}

function wire(main) {
  const cap = document.getElementById('capbtn');
  if (cap) cap.onclick = capture;

  const leads = main.querySelector('#leads');
  leads.addEventListener('change', async e => {
    const el = e.target.closest('[data-act="state"]');
    if (el) await send('POST', `/api/leads/${el.dataset.id}/state`, { state: el.value });
  });
  leads.addEventListener('keydown', async e => {
    const el = e.target.closest('[data-act="note"]');
    if (el && e.key === 'Enter' && await send('PUT', `/api/leads/${el.dataset.id}/note`, { note: el.value })) el.blur();
  });
  leads.addEventListener('click', async e => {
    const b = e.target.closest('button[data-act="remove"]');
    if (b && confirm(`Remove lead #${b.dataset.id}?`) && await send('DELETE', `/api/leads/${b.dataset.id}`)) load(main);
  });

  document.getElementById('addrule').onclick = async () => {
    const t = document.getElementById('newrule').value.trim();
    if (t && await send('POST', '/api/recruiting/rules', { text: t })) load(main);
  };
  document.getElementById('savemsg').onclick = async () => {
    if (await send('PUT', '/api/recruiting/message', { text: document.getElementById('msg').value })) load(main);
  };
  document.getElementById('reset').onclick = async () => {
    if (confirm('Clear all recruitment config?') && await send('POST', '/api/recruiting/reset')) load(main);
  };
  main.querySelector('.panel:last-child').addEventListener('click', async e => {
    const slot = e.target.closest('button[data-act="slot"]');
    if (slot) {
      const row = slot.closest('[data-role]');
      const body = {
        open: row.querySelector('.s_open').value,
        ilvl: row.querySelector('.s_ilvl').value,
        note: row.querySelector('.s_note').value,
      };
      if (await send('PUT', `/api/recruiting/slots/${slot.dataset.role}`, body)) load(main);
      return;
    }
    const del = e.target.closest('button[data-act="delrule"]');
    if (del && await send('DELETE', `/api/recruiting/rules/${del.dataset.idx}`)) load(main);
  });
}

async function load(main) {
  if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
  main.innerHTML = '<div class="muted">Loading…</div>';
  DATA = await api('/api/recruiting');
  if (!DATA) return;
  main.innerHTML = capturePanel() + leadsPanel() + configPanel();
  wire(main);
}

export function render(main) {
  load(main);
}
