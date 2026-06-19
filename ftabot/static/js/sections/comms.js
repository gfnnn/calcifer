// Comms section: the "what posts to Discord" hub — Boards, Feeds, and Pages.
// Ported from the inline _render_boards / _render_feeds / _render_content.

import { api, send } from '../api.js';
import { card, panel, esc, code, tag, toast, tabs } from '../components.js';

const TABS = [['boards', 'Boards'], ['feeds', 'Feeds'], ['pages', 'Pages']];

// --- Boards ------------------------------------------------------------------

const BOARD_CFG = {};

async function loadBoards(host) {
  const d = await api('/api/boards');
  if (!d) return;
  const types = d.available_types.map(t =>
    `<option value="${esc(t.type)}">${esc(t.name)}</option>`).join('');
  let rows = '';
  d.boards.forEach(b => {
    BOARD_CFG[b.id] = b.config;
    const dest = b.effective_destination ? code(b.effective_destination) : '<span class="muted">nowhere</span>';
    rows += `<tr><td>#${b.id}</td><td>${esc(b.name)}</td><td>${code(b.channel_id)}</td>`
      + `<td>${tag(b.state, b.state)}</td><td>${b.posted ? 'posted' : '—'}</td><td>${dest}</td><td>`
      + (b.promoted
          ? `<button class="sm" data-id="${b.id}" data-act="demote">Demote</button>`
          : `<button class="sm" data-id="${b.id}" data-act="promote">Promote</button>`)
      + (b.enabled
          ? `<button class="sm ghost" data-id="${b.id}" data-act="disable">Disable</button>`
          : `<button class="sm" data-id="${b.id}" data-act="enable">Enable</button>`)
      + `<button class="sm" data-id="${b.id}" data-act="refresh">Refresh</button>`
      + `<button class="sm" data-id="${b.id}" data-act="config">Config</button>`
      + `<button class="sm danger" data-id="${b.id}" data-act="remove">Remove</button></td></tr>`;
  });
  const listInner = d.boards.length
    ? `<table><thead><tr><th>#</th><th>Board</th><th>Channel</th><th>State</th><th>Posted</th>`
      + `<th>Goes to</th><th>Actions</th></tr></thead><tbody>${rows}</tbody></table>`
    : '<p class="muted">No boards yet.</p>';
  host.innerHTML = panel('Create a board', `
      <div class="row">
        <div class="field"><label>Type</label><select id="btype">${types}</select></div>
        <div class="field"><label>Production channel ID</label><input id="bchan" placeholder="123456789012345678"></div>
        <button id="bcreate">Create</button>
      </div>
      <p class="muted">New boards start sandboxed; promote one to send it to its real channel (needs posting mode <b>live</b>).</p>`)
    + panel('Boards', `<div id="blist">${listInner}</div>`);

  document.getElementById('bcreate').onclick = async () => {
    const board_type = document.getElementById('btype').value;
    const channel_id = document.getElementById('bchan').value.trim();
    if (!channel_id) { toast('Enter a channel ID', 'bad'); return; }
    if (await send('POST', '/api/boards', { board_type, channel_id })) loadBoards(host);
  };
  host.querySelector('#blist').addEventListener('click', async e => {
    const b = e.target.closest('button[data-act]');
    if (!b) return;
    const id = b.dataset.id, act = b.dataset.act;
    if (act === 'remove') {
      if (confirm(`Remove board #${id}?`) && await send('DELETE', `/api/boards/${id}`)) loadBoards(host);
      return;
    }
    if (act === 'config') {
      const v = prompt('Board config (JSON):', JSON.stringify(BOARD_CFG[id] || {}));
      if (v === null) return;
      let cfg;
      try { cfg = JSON.parse(v); } catch (x) { toast('Invalid JSON', 'bad'); return; }
      if (await send('PUT', `/api/boards/${id}/config`, { config: cfg })) loadBoards(host);
      return;
    }
    const r = await send('POST', `/api/boards/${id}/${act}`);
    if (r) loadBoards(host);
  });
}

// --- Feeds -------------------------------------------------------------------

async function loadFeeds(host) {
  const d = await api('/api/feeds');
  if (!d) return;
  const cards = '<div class="cards">'
    + card('Outbox pending', d.pending_outbox)
    + card('Digest pending', d.pending_digests)
    + card('Default channel', d.default_channel_id || '—')
    + '</div>';
  const rulesInner = d.rules.length
    ? `<table><thead><tr><th>#</th><th>Source</th><th>Event type</th><th>Match</th><th>Channel</th>`
      + `<th>State</th><th>Actions</th></tr></thead><tbody>`
      + d.rules.map(r => {
          const m = (r.match && r.match.contains) ? r.match.contains.join(', ') : '';
          return `<tr><td>#${r.id}</td><td>${esc(r.source)}</td><td>${esc(r.event_type || '—')}</td>`
            + `<td>${esc(m || '—')}</td><td>${code(r.channel_id)}</td>`
            + `<td>${tag(r.enabled ? 'on' : 'off', r.enabled ? 'on' : 'off')}</td><td>`
            + (r.enabled
                ? `<button class="sm ghost" data-id="${r.id}" data-act="disable">Disable</button>`
                : `<button class="sm" data-id="${r.id}" data-act="enable">Enable</button>`)
            + `<button class="sm danger" data-id="${r.id}" data-act="remove">Remove</button></td></tr>`;
        }).join('') + '</tbody></table>'
    : '<p class="muted">No routing rules — feeds fall back to the default channel.</p>';
  const mutesInner = d.muted_sources.length
    ? d.muted_sources.map(s =>
        `<span class="pill">${esc(s)} <button class="sm" data-src="${esc(s)}">unmute</button></span>`).join(' ')
    : '<p class="muted">No sources muted.</p>';

  host.innerHTML = panel('Add a routing rule', `
      <div class="row">
        <div class="field"><label>Source</label><input id="fsource" placeholder="wowhead"></div>
        <div class="field"><label>Event type (optional)</label><input id="ftype" placeholder="wowhead.patch-notes"></div>
        <div class="field"><label>Contains (comma-sep)</label><input id="fcontains" placeholder="hotfix, ptr"></div>
        <div class="field"><label>Channel ID</label><input id="fchan" placeholder="123456789012345678"></div>
        <div class="field"><label>Colour</label><input id="fcolor" placeholder="blue"></div>
        <div class="field"><label>Ping</label><input id="fping" placeholder="&lt;@&amp;roleid&gt;"></div>
        <button id="fcreate">Add rule</button>
      </div>`)
    + cards
    + panel('Routing rules', `<div id="frules">${rulesInner}</div>`)
    + panel('Muted sources', `
        <div class="row"><div class="field"><label>Mute a source</label><input id="fmute" placeholder="raiderio_mplus"></div>
        <button id="fmutebtn">Mute</button></div>
        <div id="mutes">${mutesInner}</div>`);

  document.getElementById('fcreate').onclick = async () => {
    const body = {
      source: document.getElementById('fsource').value.trim(),
      channel_id: document.getElementById('fchan').value.trim(),
      event_type: document.getElementById('ftype').value.trim(),
      contains: document.getElementById('fcontains').value.trim(),
      color: document.getElementById('fcolor').value.trim(),
      ping: document.getElementById('fping').value.trim(),
    };
    if (!body.source || !body.channel_id) { toast('Source and channel ID are required', 'bad'); return; }
    if (await send('POST', '/api/feeds/rules', body)) loadFeeds(host);
  };
  host.querySelector('#frules').addEventListener('click', async e => {
    const b = e.target.closest('button[data-act]');
    if (!b) return;
    const id = b.dataset.id, act = b.dataset.act;
    if (act === 'remove') {
      if (confirm(`Remove rule #${id}?`) && await send('DELETE', `/api/feeds/rules/${id}`)) loadFeeds(host);
      return;
    }
    if (await send('POST', `/api/feeds/rules/${id}/${act}`)) loadFeeds(host);
  });
  document.getElementById('fmutebtn').onclick = async () => {
    const s = document.getElementById('fmute').value.trim();
    if (s && await send('POST', '/api/feeds/mute', { source: s })) loadFeeds(host);
  };
  host.querySelector('#mutes').addEventListener('click', async e => {
    const b = e.target.closest('button[data-src]');
    if (b && await send('DELETE', `/api/feeds/mute/${encodeURIComponent(b.dataset.src)}`)) loadFeeds(host);
  });
}

// --- Pages (placement + copy) ------------------------------------------------

async function loadPages(host) {
  const [pages, content] = await Promise.all([api('/api/pages'), api('/api/content')]);
  if (!pages || !content) return;
  const copyByName = Object.fromEntries(content.map(c => [c.page, c]));

  host.innerHTML = '<div id="pages-list">' + pages.pages.map(p => {
    const c = copyByName[p.name] || { posts: [], source: 'bundled', updated_at: null };
    const src = c.source === 'custom' ? '<span class="pill ok">custom copy</span>' : '<span class="pill">bundled copy</span>';
    const where = p.placed
      ? `Posted in ${code(p.posted_channel_id || p.channel_id)} (${p.message_count} message(s)).`
      : 'Not placed in Discord yet.';
    const boxes = c.posts.concat(['']).map((post, i) => {
      const label = i < c.posts.length ? `Message ${i + 1}` : 'New message (optional)';
      return `<div class="field"><label>${label}</label>`
        + `<textarea maxlength="${MAXLEN}" data-box>${esc(post)}</textarea></div>`;
    }).join('');
    return `<section class="panel" data-page="${esc(p.name)}">`
      + `<h2>${esc(p.title)} ${src}</h2><p class="muted">${where}</p>${boxes}`
      + `<div class="row">`
      + `<button data-act="save">Save copy</button>`
      + `<button class="ghost" data-act="revert">Revert to bundled</button>`
      + `<button class="ghost" data-act="place">Place…</button>`
      + `<button class="ghost" data-act="refresh">Refresh</button>`
      + `<button class="danger" data-act="remove">Remove placement</button>`
      + `</div></section>`;
  }).join('') + '</div>';

  // Attach to the freshly-built inner element so reloads don't stack listeners.
  document.getElementById('pages-list').addEventListener('click', async e => {
    const b = e.target.closest('button[data-act]');
    if (!b) return;
    const sec = b.closest('[data-page]');
    const name = sec.dataset.page;
    const act = b.dataset.act;
    if (act === 'save') {
      const posts = [...sec.querySelectorAll('textarea[data-box]')].map(t => t.value).filter(v => v.trim().length);
      if (!posts.length) { toast('A page needs at least one message', 'bad'); return; }
      try {
        const r = await send('PUT', `/api/content/${name}`, { posts });
        if (r) toast('Saved — live on next page refresh', 'ok');
      } catch (x) { toast(String(x.message || x), 'bad'); }
      return;
    }
    if (act === 'revert') {
      if (confirm('Discard the custom copy and return to the bundled default?')
          && await send('DELETE', `/api/content/${name}`)) loadPages(host);
      return;
    }
    if (act === 'place') {
      const ch = prompt(`Channel ID to post ${name} in:`);
      if (ch && await send('POST', `/api/pages/${name}/place`, { channel_id: ch.trim() })) loadPages(host);
      return;
    }
    if (act === 'remove') {
      if (confirm(`Remove ${name}?`) && await send('DELETE', `/api/pages/${name}`)) loadPages(host);
      return;
    }
    if (act === 'refresh' && await send('POST', `/api/pages/${name}/refresh`)) toast('Refresh queued', 'ok');
  });
}
// Discord per-message character limit (mirrors pages.MAX_POST_CHARS).
const MAXLEN = 2000;

// --- entry -------------------------------------------------------------------

export async function render(main, sub) {
  const active = TABS.some(([k]) => k === sub) ? sub : 'boards';
  main.innerHTML = tabs('comms', TABS, active) + '<div id="comms-body" class="muted">Loading…</div>';
  const body = document.getElementById('comms-body');
  if (active === 'feeds') await loadFeeds(body);
  else if (active === 'pages') await loadPages(body);
  else await loadBoards(body);
}
