# Integration verification status

A running checklist of which external integrations have been **verified against
their live API from a deployed session**, versus only validated offline (unit
tests + defensive mapping). Tick an item off once it has been confirmed working
live, and note the date + who/what confirmed it.

> **Why this file exists.** Live API calls need real credentials, which are only
> injected into a session's container at startup. A session started *before* a
> secret was added cannot see it, so some integrations get their offline logic
> verified in one session while the live smoke test is deferred to a later
> session that boots with the key present. This list is the hand-off so the live
> check is not forgotten.

## How to tick an item off

1. Open a **fresh** Claude Code session (so the relevant secret is injected — a
   secret added to environment settings does **not** reach an already-running
   session).
2. Run the integration's smoke test (see the command per item below).
3. On success, change `- [ ]` to `- [x]`, append `— verified <date>`, and commit.

## Checklist

### WoW Audit (`services/wowaudit.py`)

- [x] **Env var named correctly** — app reads `WOWAUDIT_API_KEY`; secret confirmed renamed from `WOW_AUDIT_API`. *(2026-06-06)*
- [x] **API host reachable** — `wowaudit.com/v1` returns `403` to an unauthenticated request from the environment, i.e. not network-blocked. *(2026-06-06)*
- [x] **Offline logic** — `tests/test_wowaudit.py` passes 12/12 (auth header, `/team` + `/characters` mapping, wishlist flatten, attendance parse). *(2026-06-06)*
- [ ] **Live authenticated call — NOT YET TESTED.** Blocked only because the
  session that did the above work started before `WOWAUDIT_API_KEY` was added,
  so the key was never injected into that container. No code issue is known.
  **To verify:** in a fresh session run `python -m scripts.check_wowaudit` and
  confirm it prints `✓ WoW Audit integration is working.` with the team name and
  a non-zero character count. If it prints `unauthorized`, the key value itself
  is wrong/expired (not a code problem).

### Other integrations

These follow the same pattern (offline tests pass; live verification tracked
here as it happens). Add a smoke test + a checklist entry when each is first
exercised live.

- [ ] Blizzard API (`BLIZZARD_CLIENT_ID` / `BLIZZARD_CLIENT_SECRET`) — live call not yet verified here.
- [ ] Warcraft Logs API (`WARCRAFTLOGS_CLIENT_ID` / `WARCRAFTLOGS_CLIENT_SECRET`) — live call not yet verified here.
- [ ] Raider.IO (keyless fallback) — live call not yet verified here.
- [ ] Raid-Helper webhook receiver — live delivery not yet verified here.

---

See [`data-sources.md`](data-sources.md) for what each API returns and
[`deploy.md`](deploy.md) for how secrets are configured.
