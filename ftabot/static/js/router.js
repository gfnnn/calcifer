// Hash router for the single-shell dashboard. Sections register a renderer;
// `location.hash` (`#home`, `#raid/readiness`, ...) selects which one paints
// <main id=app>. No framework — just the platform's hashchange event.

export const NAV = [
  { key: 'home', label: 'Home' },
  { key: 'roster', label: 'Roster' },
  { key: 'raid', label: 'Raid' },
  { key: 'loot', label: 'Loot' },
  { key: 'recruitment', label: 'Recruitment' },
  { key: 'comms', label: 'Comms' },
  { key: 'settings', label: 'Settings' },
];

const sections = new Map();

// def: { title?, render(main, sub) }. A later phase overrides the Phase-0
// placeholder by registering the same key again.
export function registerSection(key, def) {
  sections.set(key, def);
}

// Parse the hash into { key, sub }. Defaults to the first nav entry.
export function currentRoute() {
  const raw = location.hash.replace(/^#/, '');
  const [key, sub] = raw.split('/');
  return { key: key || NAV[0].key, sub: sub || null };
}

function labelFor(key) {
  const item = NAV.find(n => n.key === key);
  return item ? item.label : key;
}

// Paint the active section. Exposed so app.js can re-render after an unlock.
export function render() {
  const { key, sub } = currentRoute();
  const main = document.getElementById('app');
  const def = sections.get(key);

  document.querySelectorAll('#nav a').forEach(a => {
    a.classList.toggle('active', a.dataset.key === key);
  });
  const title = document.getElementById('section-title');
  if (title) title.textContent = (def && def.title) || labelFor(key);
  const actions = document.getElementById('topbar-actions');
  if (actions) actions.innerHTML = '';

  if (!def) {
    main.innerHTML = '<div class="muted pad">Unknown section.</div>';
    return;
  }
  try {
    def.render(main, sub);
  } catch (e) {
    main.innerHTML = `<div class="bad pad">Failed to render: ${e}</div>`;
  }
}

export function start() {
  window.addEventListener('hashchange', render);
  if (!location.hash) location.hash = `#${NAV[0].key}`;
  else render();
}
