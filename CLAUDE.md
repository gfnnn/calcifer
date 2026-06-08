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
- **`recruit.py`** (pure scout scoring: weak-slot detection + trajectory) +
  **`scout.py`** (orchestrates WCL/Raider.IO discovery) back the `/scout` command.
- **`scores.py`** — targeted, bounded Mythic+ score refresh (raiders + top
  scorers) that re-pulls only who matters, then nudges the living boards.
- **`models.py`** (`Member` dataclass + `details_json` blob, WoW reference data),
  **`views.py`** (shared view-models for GM/Raid/Loot), **`wow_data.py`** (editable
  raid-buff table), **`ui.py`** (Member → Discord embeds), **`brand.py`**
  (Calcifer's colour/icon/voice/mood layer; `persona.py` is the older feed-only
  voice module). The full Discord formatting palette these render *through*
  (markdown, `<t:…>` timestamps, mentions, embeds, Components V2, limits) is
  catalogued in [`docs/communication.md`](docs/communication.md).
- **`content/`** — GM-editable JSON (`guides.json`, `rules.json`) for the managed
  Guides and Rules boards. `content.load(name, default)` reads the bundled file and
  falls back to a default on a missing/malformed file, so a board never crashes on a
  hand-edit typo.
- **`cogs/`** — Discord slash commands grouped by audience: `members`, `reports`,
  `alerts` (everyone), `admin`, `recruit`, `feeds`, `boards`, `scores`
  (officer-only). `_guards.py` holds the shared `require_admin`/`_is_admin`
  permission gate (Manage-Server perm or the admin role) so every cog gates through
  one helper. Registered in `bot.py:setup_hook`; setting `DISCORD_GUILD_ID` makes
  commands appear instantly instead of ~1h global propagation. `BOT_COMMANDS_ENABLED=0`
  skips the cogs and clears any registered commands (the bot still runs feeds/boards).
- **`web.py`** — `create_app(config)` returns the FastAPI app (HTML pages + JSON
  API + Swagger at `/docs`). Officer/sync endpoints are gated by `SYNC_TOKEN`
  (compared in constant time); the Raid-Helper webhook verifies a shared secret.
- **`serve.py`** — all-in-one host entry point: runs uvicorn, the bot, the periodic
  sync, and the feed/board/score loops as asyncio tasks on one loop. A long-running
  task dying brings the process down so the host restarts cleanly; one-shot setup
  tasks finishing does not.

### Integration hub ("Calcifer" feed hub)

Optional subsystem that consumes external feeds (Wowhead news, Raider.IO M+,
Raider.IO weekly affixes, Raider.IO realm progression, WoW Audit raids, WCL prog,
Raid-Helper webhooks) and posts branded Discord alerts.

- **`feeds/`** — `base.py` (`FeedSource`/`FeedEvent`), one module per source
  (`wowhead`, `raiderio_mplus`, `raiderio_affixes`, `raiderio_realm_progress`,
  `wcl_prog`, `wowaudit_raids`), and `runner.py` whose `poll_once` is the pure-ish
  pipeline: fetch → dedup (`seen_items`) → route → render → publish. It takes
  injected sources, an aiohttp session, and a `send` coroutine so it is fully
  testable offline. Some sources also persist state other features read (affixes →
  `mythic_affixes`, realm progress → `realm_progress`, news → `news_items`).
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
  (News Highlights), `events` (Weekly Events from Raid-Helper), `guides`
  (Guides & Resources), `rules` (Rules & Info), `pulse` (Roster Pulse). The
  `/board` cog and the `_board_loop`/`_score_loop` in `serve.py` drive them.
- **`posting.py`** — the **deny-by-default posting guardrail**. *Every* outbound
  message (boards and feed alerts) resolves its destination through a `PostGate`
  first: `POSTING_MODE=off` posts nothing, `test` redirects to `TEST_CHANNEL_ID`,
  `live` lets only `/board promote`d surfaces reach their real channel. The
  off → test → promote flow lives in the `/board` cog. New posting surfaces must
  route through the gate.

### SQLite tables (in `db_schema.py:SCHEMA`)

`members` (roster), `loot_history`, `identity_members` (GRM ground truth),
`wishlist_items`, `raid_events` + `attendance`, `guild_snapshots` (daily trend
points); feed hub: `seen_items`, `feed_rules`, `feed_outbox`, `feed_mutes`,
`feed_digest_queue`, `feed_subscriptions`, `news_items`, `mythic_affixes`,
`realm_progress`, `calendar_events`, `mplus_bests`; boards: `boards`,
`board_baselines`; and `schema_migrations` (migration bookkeeping).

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
  `migrations.py` (`migrate=True` columns are picked up by the members-columns
  migration; new tables/columns elsewhere need their own ordered migration).
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
