# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Discord bot + FastAPI web app + SQLite database for the **From the Ashes**
WoW guild (Hellfire EU). It pulls live roster data from WoW community APIs into
one SQLite file and lets the guild query it from Discord or a browser. See
`README.md` for the full feature/command list and `docs/` for design docs
(`vision.md` for the whole vision + roadmap, `data-sources.md`, `deploy.md`).

## Commands

```bash
# Install
python -m pip install -r requirements.txt          # runtime
python -m pip install -r requirements-dev.txt       # + test deps

# Lint (gates CI; config in ruff.toml)
ruff check ftabot scripts tests                     # lint
ruff check --fix ftabot scripts tests               # auto-fix

# Tests (offline, no network, real SQLite via temp files)
python -m pytest                                    # full suite
python -m pytest -q                                 # how CI runs it
python -m pytest tests/test_metrics.py              # one file
python -m pytest tests/test_web.py::test_name       # one test

# Run the pieces (all read the same SQLite DB; sync is the only net-bound part)
python -m scripts.sync_cli --list                   # seed/refresh DB headless
python -m ftabot.bot                                # Discord bot only
python -m ftabot.web                                # web/API only (:8080)
python -m ftabot.serve                              # web + bot + scheduled sync (one process; Dockerfile uses this)

# Live-API smoke checks (hit the network; run from a FRESH deployed session
# that has the secrets injected — see docs/verification-runbook.md)
python -m scripts.smoke_all                         # every configured integration, one table
python -m scripts.check_wowaudit                    # one integration at a time
python -m scripts.check_raiderio                    #   (also check_blizzard,
python -m scripts.check_warcraftlogs                #    check_raidhelper)

# Data-spine (addon SavedVariables) tooling — pure, offline
python -m scripts.inspect_savedvars dump.lua        # report the shape of a .lua dump
python -m scripts.redact_savedvars in.lua out.lua   # sanitise a real dump into a test fixture
python -m scripts.upload_savedvars dump.lua         # POST a dump to /api/upload/savedvars
```

Python 3.12. CI (`.github/workflows/ci.yml`) runs **ruff lint + `pytest` with 88%
coverage** on every PR and on pushes to `main`; formatting (`ruff format`) is
available but not enforced. `.github/workflows/sync.yml` runs the sync on a
schedule, and `.github/workflows/release.yml` cuts a GitHub Release on `v*` tags.
Contribution workflow and the GitHub-Flow branching/release model live in
`CONTRIBUTING.md` and `docs/branching.md`.

## Architecture

Three independent surfaces over one shared SQLite database. The **sync** writes;
the **bot** and **web** only read (plus officer-entered manual fields).

```
WoW Audit / Raider.IO / Blizzard / Warcraft Logs  ──►  ftabot/sync.py  ──►  SQLite (ftabot.db)
                                                                              ▲ reads
                                              ftabot/cogs/* (Discord) ────────┤
                                              ftabot/web.py (FastAPI) ────────┘
```

Key flow and layering:

- **`config.py`** — frozen `Config` dataclass built by `Config.from_env()`. It is
  the *only* place that reads `os.environ`; everything else takes a `Config`. Each
  feature self-enables from config (bot needs `DISCORD_TOKEN`, sync needs
  `SYNC_INTERVAL_MINUTES > 0`, feeds need `FEED_POLL_MINUTES`+channel).
- **`db.py`** + **`db_schema.py`** — async (`aiosqlite`) layer; use as
  `async with Database(path) as db` or `connect()/close()`. `db_schema.py` holds the
  DDL (`SCHEMA`) and the column manifests, kept out of `db.py` so the query methods
  stay easy to scan. The single `members` table is keyed by
  `(character_name, realm_slug, region)`, alongside loot/wishlist/attendance/snapshot
  tables and the feed-hub/board tables (see the schema list below).
- **`db_schema.py` — column ownership (the central invariant).** Every `members`
  column is declared once in `MEMBER_COLUMNS` as a `MemberColumn` with a
  `ColumnOwner` of `SYNC` (API-owned), `MANUAL` (human-owned: `discord_id`,
  `is_main`, `main_character_id`, `notes`, `raid_team`, `officer_role`), or `SYSTEM`
  (identity/timestamps/`details_json`). `SYNC_COLUMNS`, `MANUAL_COLUMNS` and
  `MIGRATION_COLUMNS` are **derived** from it — classify a new column in exactly one
  place. A roster sync writes only sync-owned columns, so it **never** overwrites
  human-entered data; `tests/test_columns.py` guards a misclassification.
- **`migrations.py`** — forward-only, **versioned** migration runner executed on
  every `Database.connect`. Each `Migration` has a unique `version` and an
  *idempotent* (check-before-alter) `apply`; a `schema_migrations` table records what
  has run. SQLite can't drop/alter columns, so migrations only add columns or
  backfill — never remove. Append new migrations; never renumber or reuse a version.
  **`SCHEMA` is executed on every `connect` *before* the migrations run**, so never
  put an index (or anything) in `SCHEMA` that references a column a migration only
  adds later — on an existing DB it runs before the column exists and crashes
  startup with "no such column". Create such an index *in the migration that adds
  the column* (see migration 6 / `wowaudit_team`).
- **`services/`** — one async client per API (`wowaudit`, `raiderio`, `blizzard`,
  `warcraftlogs`, `raidhelper`), all built on **`services/_http.py`**: `BaseHTTPClient`
  centralises the aiohttp session lifecycle (own-or-share a session) and the
  `Retry-After` parsing, and `OAuthClientCredentials` is the shared OAuth2
  token cache used by Blizzard + Warcraft Logs (re-mints on 401). WoW Audit is the
  source of truth when a key is set; Raider.IO is the keyless fallback that always
  works. The **data spine** also lives here as pure offline parsers: `savedvars.py`
  (a dependency-free WoW SavedVariables Lua reader) + `rclootcouncil.py` / `grm.py`
  (addon mappers).
- **Data spine** — `spine.py` orchestrates ingestion of the GM's uploaded addon
  `.lua` (via `POST /api/upload/savedvars`, secret `SAVEDVARS_UPLOAD_SECRET`,
  pushed by `scripts/upload_savedvars.py`): RCLootCouncil → `loot_history`, and
  GRM → the `identity_members` table + `identity.py`'s pure alt↔main account
  grouping overlaid onto `members`. GRM ingestion is an **authoritative manual
  write** (it owns `is_main`/`main_character_id`), distinct from API syncs.
  `scripts/inspect_savedvars.py` / `redact_savedvars.py` help confirm addon shapes
  against a real dump and produce committable fixtures — see `docs/data-spine-validation.md`.
- **`sync.py`** — orchestrates fetch → enrich → upsert → prune → loot/wishlist →
  snapshot, returning a `SyncResult`. `enrich_max_age_hours`/`enrich_limit` throttle
  re-enrichment; raiders are always enriched.
- **`metrics.py`** — pure scoring functions (activity, readiness, guild health,
  loot priority). No I/O — keep it pure and unit-test it directly.
- **`recruit.py`** (pure scout scoring: weak-slot detection + trajectory +
  `LEAD_STATES`) + **`scout.py`** (orchestrates WCL/Raider.IO discovery; pure
  `lead_dicts` flattens reports into leads) back the `/scout` command and the
  `/recruit lead` recruitment funnel (`scout_leads` table). There is no LFG
  *listing* API on Raider.IO/WoWProgress, so the funnel inverts the scout: the
  candidates it surfaces (with their `lfg_active` flag) are the captured leads.
- **`scores.py`** — targeted, bounded Mythic+ score refresh (raiders + top
  scorers) that re-pulls only who matters, then nudges the living boards.
- **`models.py`** (`Member` dataclass + `details_json` blob, WoW reference data),
  **`views.py`** (shared view-models for GM/Raid/Loot), **`wow_data.py`** (editable
  raid-buff table), **`ui.py`** (Member → Discord embeds), **`brand.py`**
  (Calcifer's colour/icon/voice/mood layer; `persona.py` is the older feed-only
  voice module). The full Discord formatting palette these render *through*
  (markdown, `<t:…>` timestamps, mentions, embeds, Components V2, limits) is
  catalogued in [`docs/communication.md`](docs/communication.md).
- **`content/`** — GM-editable JSON: `page_*.json` (the **sticky pages'** bundled
  default copy for Rules & Info and Guides & Resources; the dashboard saves
  overrides to `content_docs` instead of the file) and `recruitment.json` (the
  Recruitment board's cultural prose). `content.load(name, default)` reads the
  bundled file and falls back to a default on a missing/malformed file, so a
  surface never crashes on a hand-edit typo.
- **`prose.py`** — the managed-prose token layer shared by the sticky pages and
  the Recruitment board: `SafeDict` (keeps an unknown `{token}` visible),
  `safe_format` (never crashes a render) and `live_tokens` (current
  member-count/progression/recruiting values). One home so both surfaces import
  the same substitution without a cycle.
- **`cogs/`** — Discord slash commands grouped by audience: `members`, `reports`,
  `alerts` (everyone), `admin`, `recruit`, `feeds`, `boards`, `pages`, `scores`
  (officer-only). `_guards.py` holds the shared `require_admin`/`_is_admin`
  permission gate (Manage-Server perm or the admin role) so every cog gates through
  one helper. Registered in `bot.py:setup_hook`; setting `DISCORD_GUILD_ID` makes
  commands appear instantly instead of ~1h global propagation. `BOT_COMMANDS_ENABLED=0`
  skips the cogs and clears any registered commands (the bot still runs feeds/boards).
- **`web.py`** — `create_app(config)` returns the FastAPI app (the dashboard
  shell + JSON API + Swagger at `/docs`). It serves **one single-page dashboard at
  `/app`** (a static, dependency-free shell in `ftabot/static/`: `index.html` +
  `css/app.css` + ES-module `js/` — `app.js` bootstrap, `router.js` hash router,
  `api.js` fetch/unlock, `components.js`, and one module per `js/sections/`:
  home, roster, raid, loot, recruitment, comms, settings). `/` redirects to
  `/app`; `/static` is a `StaticFiles` mount. The shell is served unconditionally
  and **every JSON API is gated by `SYNC_TOKEN`** (constant-time; the first 401
  reveals an unlock overlay that posts to `/api/sync/unlock`, setting an http-only
  cookie). The Raid-Helper webhook verifies a shared secret; `/healthz`,
  `/webhooks/*` and `/api/upload/savedvars` stay open. All logic lives in
  `views.py` (async, composing pure `metrics.py`) — the JS only fetches a dict and
  paints, which keeps the coverage gate satisfiable. **Home** hydrates from
  `GET /api/home` (`views.home` + `views.attention` — a guild-health pulse + a
  ranked "needs attention" triage queue merged with live `SyncManager` status);
  **Roster** from `/api/members` (+ `/api/members/{name}/detail` for the profile
  drill-down + embedded manual-field editor → `/api/admin/members/{id}`); **Raid**
  from `/api/raid` + `/api/roster`; **Loot** from `/api/loot` (+ `/api/loot/
  priority`); **Recruitment** from `/api/recruiting` + `/api/leads/*`; **Comms**
  from `/api/boards`, `/api/feeds`, `/api/pages` + `/api/content/{page}` (sticky-
  page copy → `content_docs`); **Settings** from `/api/settings`, `/api/sync*`,
  `/api/overview`. These mirror the `/board`, `/feed`, `/page`, `/recruit`, and
  member-admin (`/link`, `/setteam`, …) Discord cogs. Every control writes DB
  state the `serve.py` board/feed loops (and the cogs) re-read each tick, so a
  change goes live within one refresh interval (an on-demand refresh renders
  immediately when the bot shares the process, else reports `queued`).
  `ScoutManager` mirrors `SyncManager` to run the network-bound recruitment scout
  (`/api/leads/capture`) as a background task.
- **`runtime_settings.py`** — the live config overlay. A frozen `Config` is built
  once from env, so the dashboard can't mutate it; instead a small whitelist
  (`OVERRIDABLE`: `posting_mode`, `test_channel_id`, `feed_default_channel_id`)
  is saved into the `settings` table and resolved *through here* per use
  (`effective_post_gate`, `effective_feed_channel`) — the override wins, else the
  env `Config` default. The board/feed loops and the `/board`+`/page` cogs build
  their `PostGate` from this so Discord and the dashboard share one live mode.
  Loop-launch toggles stay env-only (the Settings page shows them read-only).
- **`serve.py`** — all-in-one host entry point: runs uvicorn, the bot, the periodic
  sync, and the feed/board/score loops as asyncio tasks on one loop. A long-running
  task dying brings the process down so the host restarts cleanly; one-shot setup
  tasks finishing does not.

### Integration hub ("Calcifer" feed hub)

Optional subsystem that consumes external feeds (Wowhead news, Raider.IO M+,
Raider.IO weekly affixes, Raider.IO realm progression, WoW Audit raids, WCL prog,
Raid-Helper webhooks) and posts branded Discord alerts.

- **`feeds/`** — `base.py` (`FeedSource`/`FeedEvent`), one module per source
  (`wowhead`, `rss_news` (a generic config-driven RSS news source, e.g. Blizzard
  official news), `raiderio_mplus`, `raiderio_affixes`, `raiderio_realm_progress`,
  `wcl_prog`, `wowaudit_raids`), the shared pure RSS reader `_rss.py` (used by
  `wowhead` + `rss_news`), and `runner.py` whose `poll_once` is the pure-ish
  pipeline: fetch → dedup (`seen_items`) → route → render → publish. It takes
  injected sources, an aiohttp session, and a `send` coroutine so it is fully
  testable offline. Some sources also persist state other features read (affixes →
  `mythic_affixes`, realm progress → `realm_progress`, news → `news_items`; both
  `wowhead` and `rss_news` archive into `news_items` for the News board).
- **`router.py`** — pure `match → route → style` logic over the `feed_rules` table,
  with a default-channel fallback so nothing is lost. Keep matching pure/testable.
  A rule whose style is `{"mode": "digest"}` buffers events into `feed_digest_queue`
  for periodic roundup posts instead of posting immediately.
- **`publisher.py`** / **`persona.py`** — render events into styled embeds.
- Push events (Raid-Helper webhooks) land in the durable `feed_outbox` queue so the
  bot posts them even across a restart (`drain_outbox`, with a visibility-timeout
  claim so a crashed delivery is retried, not lost).
- **`/alerts`** lets members opt into per-mention pings for a source
  (`feed_subscriptions`); the runner appends their mentions to the content.

### Living boards + the posting guardrail

- **`boards/`** — bot-owned messages that re-render from the DB and edit
  themselves in place, turning a scrolling channel into one living page. `base.py`
  (`Board`/`BoardPayload` + a `REGISTRY`), `engine.py` (`refresh_board`: render →
  diff-by-fingerprint → edit, with self-heal if the message vanished), `messenger.py`
  (the bot-backed sender that forces `allowed_mentions=none` so boards never ping),
  and one renderer per board. Renderers are pure `(db, config, board_config) →
  payload`, offline-tested like `metrics.py`, and self-register into `REGISTRY` on
  import. Current boards: `hot20` (Calcifer's Hot 20 M+ leaderboard), `news`
  (News Highlights), `events` (Weekly Events from Raid-Helper), `pulse` (Roster
  Pulse), `recruitment` (We're Looking; officer-configured via `/recruit`, state
  in `recruitment_config`). The `/board` cog and the `_board_loop`/`_score_loop`
  in `serve.py` drive them. (Rules & Info and Guides & Resources are **not**
  boards — they are sticky pages; see `pages.py`.)
- **`pages.py`** — **sticky pages**: multi-message, GM-authored Discord-markdown
  pages the bot owns (currently `rules_info` + `guides_resources`). Copy lives in
  `content/page_<name>.json` with a dashboard override in the `content_docs`
  table; `render_page` substitutes the live `{tokens}` from `prose.py`
  but posts the GM's words verbatim (no voice sanitise). `refresh_page` is the
  reconcile engine (pure of Discord, drives the boards messenger + `delete`):
  first run posts, a channel change deletes the old copy and re-posts, same
  channel + same copy is a no-op, and a copy edit edits each message in place.
  Placement is the `/page` cog (`cogs/pages.py`); the destination resolves
  through the `PostGate` with `promoted=True` (the channel is an officer's
  explicit choice). The board loop in `serve.py` also reconciles placed pages, so
  dashboard edits go live within one refresh interval. Adding a page = a bundled
  `content/page_<name>.json` + one `PageDef` in `PAGES`; the `/page` choices and
  the dashboard Content section derive from the registry.
- **`posting.py`** — the **deny-by-default posting guardrail**. *Every* outbound
  message (boards, sticky pages and feed alerts) resolves its destination through
  a `PostGate` first: `POSTING_MODE=off` posts nothing, `test` redirects to
  `TEST_CHANNEL_ID`, `live` lets only `/board promote`d surfaces reach their real
  channel. The off → test → promote flow lives in the `/board` cog. New posting
  surfaces must route through the gate.

### SQLite tables (in `db_schema.py:SCHEMA`)

`members` (roster), `loot_history`, `identity_members` (GRM ground truth),
`wishlist_items`, `raid_events` + `attendance`, `guild_snapshots` (daily trend
points); feed hub: `seen_items`, `feed_rules`, `feed_outbox`, `feed_mutes`,
`feed_digest_queue`, `feed_subscriptions`, `news_items`, `mythic_affixes`,
`realm_progress`, `calendar_events`, `mplus_bests`; boards: `boards`,
`board_baselines`, `recruitment_config` (officer-set recruitment board state);
sticky pages: `sticky_pages` (placement + posted message ids), `content_docs`
(dashboard copy overrides for the bundled `content/*.json`); recruitment funnel:
`scout_leads` (scouted candidates tracked through new→contacted→trialing→
joined/passed; discovered fields refreshed by a re-scout, officer state/notes
preserved); `settings` (the dashboard's live config overlay — see
`runtime_settings.py`); and `schema_migrations` (migration bookkeeping).

## Conventions

- **Async throughout.** `pytest.ini` sets `asyncio_mode = auto`, so `async def`
  tests need no decorator. Use the `db` fixture in `tests/conftest.py` for a real
  temp-file SQLite database. Tests are offline — never add ones that hit the network.
  (The live `scripts/check_*` smoke checks are the deliberate exception and are
  excluded from coverage.)
- **No `os.environ` outside `config.py`.** Thread `Config` through instead.
- **Sync vs. manual fields.** Any new character attribute must be classified by
  adding a `MemberColumn` to `MEMBER_COLUMNS` in `db_schema.py` with the right
  `ColumnOwner` (and `in_sync_upsert`/`migrate` flags). The `SYNC_COLUMNS` /
  `MANUAL_COLUMNS` / `MIGRATION_COLUMNS` partitions derive from it automatically.
  If the change needs a schema migration on existing DBs, append a `Migration` to
  `migrations.py`. **Caveat that has bitten us:** the members-columns migration
  (`_m1`, `migrate=True`) runs **once** and is then recorded, so it only adds
  columns to a DB that hasn't run it yet (fresh installs). It will **not**
  back-fill a column onto an *established* DB that already ran `_m1` before the
  column existed. So any members column added *after* `_m1` is in place — i.e.
  essentially every new one now — needs **its own ordered `Migration`** that ADDs
  the column (`_add_column_if_missing`), plus its index if any. Keep `migrate=True`
  too (it still serves fresh DBs), but don't rely on it alone for prod. New
  tables/columns elsewhere likewise need their own ordered migration.
- **Keep `metrics.py` and `router.py` pure** (no I/O) — that is what makes them
  testable. Board renderers and feed sources follow the same pure-core pattern.
- Background loops (`serve.py`, feed loop) deliberately swallow exceptions with
  `# noqa: BLE001` to stay alive; follow that pattern for long-running loops only.
- Officer-only cogs gate through `cogs/_guards.py:require_admin` — don't re-roll a
  permission check or import one from a sibling cog.
- New API clients should subclass `services/_http.py:BaseHTTPClient` (or
  `OAuthClientCredentials`) rather than re-implementing the session/token plumbing.
- Tests are organized partly by feature (`test_web.py`, `test_metrics.py`,
  `test_wowaudit.py`) and partly by rollout phase (`test_phase*.py`).
