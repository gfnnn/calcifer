// Home section: a guild-health pulse strip + the "needs attention" triage
// queue. Painted from GET /api/home — all logic lives server-side, this only
// maps the dict through the shared components.

import { api } from '../api.js';
import { card, panel, esc, sparklinePoints, pill } from '../components.js';

function fmtScore(v) {
  return (v == null) ? '—' : Math.round(v);
}

// A signed delta vs the previous health point, with a direction class.
function healthDelta(score, previous) {
  if (score == null || previous == null) return {};
  const d = score - previous;
  if (Math.abs(d) < 0.5) return {};
  const sign = d > 0 ? '+' : '';
  return { delta: `${sign}${Math.round(d)}`, deltaDir: d > 0 ? 'up' : 'down' };
}

function pulseStrip(pulse) {
  const sparks = (pulse.trends && pulse.trends.sparklines) || {};
  const h = pulse.health || {};
  const counts = pulse.counts || {};

  const healthCard = card('Guild Health', fmtScore(h.score), {
    ...healthDelta(h.score, h.previous),
    spark: sparklinePoints(sparks.health_score),
  });

  const activeSub = counts.total ? `${counts.active} of ${counts.total} active` : null;
  const activityCard = card('Activity', counts.total ? `${counts.active}/${counts.total}` : '—', {
    sub: activeSub,
    spark: sparklinePoints(sparks.active_count),
  });

  const prep = pulse.prep_rate == null ? '—' : `${Math.round(pulse.prep_rate * 100)}%`;
  const readinessCard = card('Prepared', prep, {
    sub: pulse.vault_rate == null ? null : `${Math.round(pulse.vault_rate * 100)}% vault`,
  });

  const retSpark = sparklinePoints(sparks.member_count);
  const retentionCard = card('Roster', counts.total ?? '—', {
    sub: pulse.progression || null,
    spark: retSpark,
  });

  const buffs = pulse.missing_buffs || [];
  const buffInner = buffs.length
    ? buffs.map(b => pill(b, 'warn')).join(' ')
    : '<span class="ok">All raid buffs covered</span>';

  return `<div class="cards">${healthCard}${activityCard}${readinessCard}${retentionCard}</div>`
    + panel('Composition & buffs', `<div>${buffInner}</div>`);
}

function triageItem(it) {
  const sev = `<span class="sev ${esc(it.severity)}">${esc(it.severity)}</span>`;
  const body = `<div class="ti-body"><div class="ti-title">${esc(it.title)}</div>`
    + `<div class="ti-detail">${esc(it.detail)}</div></div>`;
  const link = `<a href="${esc(it.action_url)}">View →</a>`;
  return `<div class="triage-item ${esc(it.severity)}">${sev}${body}${link}</div>`;
}

function triageQueue(items) {
  if (!items.length) {
    return panel('Needs attention',
      '<div class="triage-empty">✓ All clear — nothing needs your attention.</div>');
  }
  return panel('Needs attention',
    `<div class="triage">${items.map(triageItem).join('')}</div>`);
}

export async function render(main) {
  main.innerHTML = '<div class="muted">Loading…</div>';
  const d = await api('/api/home');
  if (!d) return; // 401 → unlock overlay handled by api.js
  main.innerHTML = pulseStrip(d.pulse || {}) + triageQueue(d.attention || []);

  // Surface the open-item total as a badge on the Home nav link.
  const navHome = document.querySelector('#nav a[data-key="home"]');
  if (navHome) {
    const total = d.attention_total || 0;
    navHome.querySelector('.badge')?.remove();
    if (total > 0) {
      const b = document.createElement('span');
      b.className = 'badge';
      b.textContent = total;
      navHome.appendChild(b);
    }
  }
}
