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
```

Python 3.12. CI (`.github/workflows/ci.yml`) runs **ruff lint + `pytest` with 80%
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
- **`db.py`** — async (`aiosqlite`) layer; use as `async with Database(path) as db`
  or `connect()/close()`. Single `members` table keyed by
  `(character_name, realm_slug, region)`, plus loot/wishlist/attendance/snapshots
  and the feed-hub tables. **Critical invariant:** `_SYNC_COLUMNS` (API-sourced
  fields) and manual columns (`discord_id`, `is_main`, `main_character_id`,
  `notes`, `raid_team`, `officer_role`) are written separately — a roster sync
  **never** overwrites human-entered data. New columns are auto-added on connect
  via `_MIGRATION_COLUMNS` (lightweight forward-only migration; no migration tool).
- **`services/`** — one async client per API (`wowaudit`, `raiderio`, `blizzard`,
  `warcraftlogs`, `raidhelper`). WoW Audit is the source of truth when a key is
  set; Raider.IO is the keyless fallback that always works. The **data spine**
  also lives here as pure offline parsers: `savedvars.py` (a dependency-free WoW
  SavedVariables Lua reader) + `rclootcouncil.py` / `grm.py` (addon mappers).
- **Data spine** — `spine.py` orchestrates ingestion of the GM's uploaded addon
  `.lua` (via `POST /api/upload/savedvars`, secret `SAVEDVARS_UPLOAD_SECRET`,
  pushed by `scripts/upload_savedvars.py`): RCLootCouncil → `loot_history`, and
  GRM → the `identity_members` table + `identity.py`'s pure alt↔main account
  grouping overlaid onto `members`. GRM ingestion is an **authoritative manual
  write** (it owns `is_main`/`main_character_id`), distinct from API syncs.
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
- **`cogs/`** — Discord slash commands grouped by audience: `members`, `reports`
  (everyone), `admin`, `recruit`, `feeds`, `boards`, `scores` (officer-only).
  Registered in `bot.py:setup_hook`; setting `DISCORD_GUILD_ID` makes commands
  appear instantly instead of ~1h global propagation. `BOT_COMMANDS_ENABLED=0`
  skips the cogs and clears any registered commands (the bot still runs feeds/boards).
- **`web.py`** — `create_app(config)` returns the FastAPI app (HTML pages + JSON
  API + Swagger at `/docs`). Officer/sync endpoints are gated by `SYNC_TOKEN`
  (compared in constant time); the Raid-Helper webhook verifies a shared secret.
- **`serve.py`** — all-in-one host entry point: runs uvicorn, the bot, the periodic
  sync, and the feed/board/score loops as asyncio tasks on one loop. A long-running
  task dying brings the process down so the host restarts cleanly; one-shot setup
  tasks finishing does not.

### Integration hub ("Calcifer" feed hub)

Optional subsystem that consumes external feeds (Wowhead, Raider.IO M+, WoW Audit
raids, WCL prog, Raid-Helper webhooks) and posts branded Discord alerts.

- **`feeds/`** — `base.py` (`FeedSource`/`FeedEvent`), one module per source, and
  `runner.py` whose `poll_once` is the pure-ish pipeline: fetch → dedup
  (`seen_items`) → route → render → publish. It takes injected sources, an aiohttp
  session, and a `send` coroutine so it is fully testable offline.
- **`router.py`** — pure `match → route → style` logic over the `feed_rules` table,
  with a default-channel fallback so nothing is lost. Keep matching pure/testable.
- **`publisher.py`** / **`persona.py`** — render events into styled embeds.
- Push events (Raid-Helper webhooks) land in the durable `feed_outbox` queue so the
  bot posts them even across a restart (`drain_outbox`).

### Living boards + the posting guardrail

- **`boards/`** — bot-owned messages that re-render from the DB and edit
  themselves in place. `base.py` (`Board`/`BoardPayload` + a `REGISTRY`),
  `engine.py` (`refresh_board`: render → diff-by-fingerprint → edit, with
  self-heal if the message vanished), `messenger.py` (the bot-backed sender that
  forces `allowed_mentions=none` so boards never ping), and one renderer per
  board (`hot20.py` = Calcifer's Hot 20). Renderers are pure `(db, config) →
  payload`, offline-tested like `metrics.py`. The `/board` cog and the
  `_board_loop`/`_score_loop` in `serve.py` drive them.
- **`posting.py`** — the **deny-by-default posting guardrail**. *Every* outbound
  message (boards and feed alerts) resolves its destination through a `PostGate`
  first: `POSTING_MODE=off` posts nothing, `test` redirects to `TEST_CHANNEL_ID`,
  `live` lets only `/board promote`d surfaces reach their real channel. New
  posting surfaces must route through the gate.

## Conventions

- **Async throughout.** `pytest.ini` sets `asyncio_mode = auto`, so `async def`
  tests need no decorator. Use the `db` fixture in `tests/conftest.py` for a real
  temp-file SQLite database. Tests are offline — never add ones that hit the network.
- **No `os.environ` outside `config.py`.** Thread `Config` through instead.
- **Sync vs. manual fields.** Any new character attribute must be classified: if the
  API owns it, add it to `_SYNC_COLUMNS`; if a human owns it, keep it out so syncs
  don't clobber it. Adding a column also means adding it to `_MIGRATION_COLUMNS`.
- **Keep `metrics.py` and `router.py` pure** (no I/O) — that is what makes them testable.
- Background loops (`serve.py`, feed loop) deliberately swallow exceptions with
  `# noqa: BLE001` to stay alive; follow that pattern for long-running loops only.
- Tests are organized partly by feature (`test_web.py`, `test_metrics.py`,
  `test_wowaudit.py`) and partly by rollout phase (`test_phase*.py`).
