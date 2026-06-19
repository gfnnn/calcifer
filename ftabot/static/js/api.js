// Fetch + auth core. Ported from the old inline _ADMIN_JS, as an ES module.
// Auth is enforced by the JSON APIs; a 401 reveals the unlock overlay (wired in
// app.js) rather than reloading, so the SPA shell stays put.

let onUnauthorized = () => {};

// app.js registers what to do when an API returns 401 (show the overlay).
export function setUnauthorizedHandler(fn) {
  onUnauthorized = fn;
}

// GET JSON. Returns null on 401 (caller should bail: `if (!d) return;`).
export async function api(path) {
  const r = await fetch(path, { headers: { Accept: 'application/json' } });
  if (r.status === 401) { onUnauthorized(); return null; }
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

// Mutating request (POST/PUT/DELETE). Surfaces server `detail` on error.
// Returns null on 401 or error, the parsed body (or {}) on success.
export async function send(method, path, body) {
  const opts = { method };
  if (body !== undefined) {
    opts.headers = { 'Content-Type': 'application/json' };
    opts.body = JSON.stringify(body);
  }
  const r = await fetch(path, opts);
  if (r.status === 401) { onUnauthorized(); return null; }
  if (!r.ok) {
    let detail = r.statusText;
    try { detail = (await r.json()).detail || detail; } catch (e) { /* ignore */ }
    throw new Error(detail);
  }
  try { return await r.json(); } catch (e) { return {}; }
}

// Post the token to the existing unlock endpoint, which sets the http-only
// cookie. Returns true on success.
export async function unlock(token) {
  const r = await fetch('/api/sync/unlock', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ token }),
  });
  return r.ok;
}
