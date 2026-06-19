// Bootstrap: build the nav, register sections, wire the unlock overlay, and
// start the hash router. Section modules are registered here as each phase lands
// (Phase 0 ships placeholders the later phases override).

import { NAV, registerSection, render, start } from './router.js';
import { setUnauthorizedHandler, unlock } from './api.js';
import { esc } from './components.js';
import * as home from './sections/home.js';
import * as roster from './sections/roster.js';
import * as raid from './sections/raid.js';
import * as loot from './sections/loot.js';
import * as recruitment from './sections/recruitment.js';
import * as comms from './sections/comms.js';
import * as settings from './sections/settings.js';

// --- nav ----------------------------------------------------------------
function buildNav() {
  const nav = document.getElementById('nav');
  nav.innerHTML = NAV.map(n =>
    `<a href="#${n.key}" data-key="${n.key}">${esc(n.label)}</a>`
  ).join('');
}

// --- unlock overlay -----------------------------------------------------
function showUnlock() {
  const o = document.getElementById('unlock');
  if (o) { o.hidden = false; document.getElementById('unlock-token').focus(); }
}
function hideUnlock() {
  const o = document.getElementById('unlock');
  if (o) o.hidden = true;
}
function wireUnlock() {
  setUnauthorizedHandler(showUnlock);
  const form = document.getElementById('unlock-form');
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const input = document.getElementById('unlock-token');
    const err = document.getElementById('unlock-error');
    err.hidden = true;
    if (await unlock(input.value)) {
      input.value = '';
      hideUnlock();
      render(); // re-run the route now that the cookie is set
    } else {
      err.hidden = false;
    }
  });
}

// --- sections -----------------------------------------------------------
// Phase 0 placeholder. Replaced section-by-section in later phases.
function placeholder(label) {
  return (main) => {
    main.innerHTML = `<div class="panel"><h2>${esc(label)}</h2>`
      + '<p class="muted">This section lands in a later phase of the dashboard '
      + 'rebuild. The shell, design system, and routing are in place.</p></div>';
  };
}
function registerSections() {
  NAV.forEach(n => registerSection(n.key, { title: n.label, render: placeholder(n.label) }));
  // Real sections override the Phase-0 placeholder as each phase lands.
  registerSection('home', { title: 'Home', render: home.render });
  registerSection('roster', { title: 'Roster', render: roster.render });
  registerSection('raid', { title: 'Raid', render: raid.render });
  registerSection('loot', { title: 'Loot', render: loot.render });
  registerSection('recruitment', { title: 'Recruitment', render: recruitment.render });
  registerSection('comms', { title: 'Comms', render: comms.render });
  registerSection('settings', { title: 'Settings', render: settings.render });
}

// --- boot ---------------------------------------------------------------
buildNav();
wireUnlock();
registerSections();
start();
