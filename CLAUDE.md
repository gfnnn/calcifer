# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Discord bot + SQLite database for the **From the Ashes** WoW guild (Hellfire
EU). It pulls live roster data from WoW community APIs into one SQLite file,
lets the guild query it from Discord, and posts feed alerts / living boards /
sticky pages back into Discord. **There is no dashboard and no browsable API**:
every runtime control is a Discord command, and anything else is a change
request to this repo (code, `content/*.json`, or env vars). See
`README.md` for the full feature/command list and `docs/` for the technical
references (`boards-contract.md` for building boards and the channel layout,
`communication.md`, `data-sources.md`, `deploy.md`; index in `docs/README.md`).

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
python -m pytest tests/test_cogs.py::test_name      # one test

# Run the pieces (all read the same SQLite DB; sync is the only net-bound part)
python -m scripts.sync_cli --list                   # seed/refresh DB headless
python -m ftabot.bot                                # Discord bot only (slash commands, no loops)
python -m ftabot.serve                              # bot + loops + scheduled sync + ingest listener (one process; Dockerfile uses this)

# Live-API smoke checks (hit the network; run from a FRESH deployed session
# that has the secrets injected — see docs/verification-runbook.md)
python -m scripts.smoke_all                         # every configured integration, one table
python -m scripts.check_wowaudit                    # one integration at a time
python -m scripts.check_raiderio                    #   (also check_blizzard,
python -m scripts.check_warcraftlogs                #    check_raidhelper)

# Operations
python -m scripts.preflight                         # offline go/no-go check of the deploy config (.env)
python -m scripts.backup_db --dest backups          # consistent copy of the live SQLite DB (+ pruning)
docker compose up -d --build                        # run the all-in-one process on any Docker host
./deploy/vm/setup.sh | deploy.sh [--pull] | status.sh   # Docker VM host (Oracle): one-time setup, start/update, health

# Data-spine (addon SavedVariables) tooling — pure, offline
python -m scripts.inspect_savedvars dump.lua        # report the shape of a .lua dump
python -m scripts.redact_savedvars in.lua out.lua   # sanitise a real dump into a test fixture
python -m scripts.upload_savedvars dump.lua         # POST a dump to /api/upload/savedvars
```

Python 3.12. CI (`.github/workflows/ci.yml`) runs **ruff lint + `pytest` with 88%
coverage** on every PR and on pushes to `main`; formatting (`ruff format`) is
available but not enforced. `.github/workflows/release.yml` cuts a GitHub Release
on `v*` tags. (There is deliberately no scheduled sync workflow: public Actions
logs and artifacts would publish the guild roster. The host runs the sync.)
Contribution workflow and the GitHub-Flow branching/release model live in
`CONTRIBUTING.md` and `docs/branching.md`.

## Architecture

API connections feed one shared SQLite database; Discord is the only front end.
The **sync** (and the two inbound pushes) write; the **cogs** read (plus
officer-entered manual fields) and the **boards/feeds/pages** post.

```
WoW Audit / Raider.IO / Blizzard / Warcraft Logs  ──►  ftabot/sync.py  ──►  SQLite (ftabot.db)
Raid-Helper webhook / addon .lua upload  ──►  ftabot/ingest.py  ──────────────►  ▲
                                                                                 │ reads
                              ftabot/cogs/* (slash commands) ────────────────────┤
                              boards / feeds / pages (Discord posts) ────────────┘
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
  token cache used by Blizzard + Warcraft Logs (re-mints on 401). Open every
  outbound session with `_http.new_session()` (it sets `trust_env=True`):
  a bare `aiohttp.ClientSession` ignores `HTTPS_PROXY` and fails at DNS behind
  a proxy, including in a Claude Code web session. WoW Audit is the
  source of truth when a key is set; Raider.IO is the keyless fallback that always
  works. The **data spine** also lives here as pure offline parsers: `savedvars.py`
  (a dependency-free WoW SavedVariables Lua reader) + `rclootcouncil.py` / `grm.py`
  (addon mappers).
- **Data spine** — `spine.py` orchestrates ingestion of the GM's uploaded addon
  `.lua` (via `POST /api/upload/savedvars` on `ingest.py`, secret
  `SAVEDVARS_UPLOAD_SECRET`, pushed by `scripts/upload_savedvars.py`): RCLootCouncil → `loot_history`, and
  GRM → the `identity_members` table + `identity.py`'s pure alt↔main account
  grouping overlaid onto `members`. GRM ingestion is an **authoritative manual
  write** (it owns `is_main`/`main_character_id`), distinct from API syncs.
  `scripts/inspect_savedvars.py` / `redact_savedvars.py` help confirm addon shapes
  against a real dump and produce committable fixtures — see `docs/data-spine-validation.md`.
- **`sync.py`** — orchestrates fetch → enrich → upsert → prune → loot/wishlist →
  snapshot, returning a `SyncResult`. `enrich_max_age_hours`/`enrich_limit` throttle
  re-enrichment; raiders are always enriched.
- **`metrics.py`** — pure scoring functions (activity, readiness, guild health,
  loot priority). No I/O — keep it pure and unit-test it directly. "Active"
  reads `last_login` (WoW Audit), else `last_crawled_at` (Raider.IO's crawl
  time, the keyless signal), and only then `last_synced`: never promote
  `last_synced` above them, it says when *we* looked and makes everyone active.
- **`raids.py`** — pure raid-tier knowledge. Raider.IO's `raid_progression` dict
  is **not** in chronological order (an ended tier or a one-boss side raid can
  come first), so "the current raid" is decided from its raid catalog
  (`/raiding/static-data`: names, boss counts, open/close dates per region) via
  `current_raids` / `headline_slug`. Everything degrades to "first key" when no
  catalog is available. `guild_raid_rows` flattens the guild's own progress +
  standings into the `guild_raids` table the sync fills.
- **`milestones.py`** — pure milestone detection (`detect`) + the DB-backed
  `collect`. Edge-triggered against `milestone_marks` (last value per
  entity/metric), so a new joiner or the first run announces nothing; found
  milestones are stored in `milestones` and offered to the runner by the
  `milestones` feed for a week (so one held by the guardrail still posts).
- **`recruit.py`** (pure scout scoring: weak-slot detection + trajectory +
  `LEAD_STATES`) + **`scout.py`** (orchestrates WCL/Raider.IO discovery; pure
  `lead_dicts` flattens reports into leads) back the `/scout` command and the
  `/recruit lead` recruitment funnel (`scout_leads` table). There is no LFG
  *listing* API on Raider.IO/WoWProgress, so the funnel inverts the scout: the
  candidates it surfaces (with their `lfg_active` flag) are the captured leads.
- **`scores.py`** — targeted, bounded Mythic+ score refresh (raiders + top
  scorers) that re-pulls only who matters, then nudges the living boards.
- **`models.py`** (`Member` dataclass + `details_json` blob, WoW reference data),
  **`views.py`** (view-models behind the GM/Raid/Loot report commands), **`wow_data.py`** (editable
  raid-buff table), **`ui.py`** (Member → Discord embeds), **`brand.py`**
  (Calcifer's colour/icon/voice/mood layer; `persona.py` is the older feed-only
  voice module). The full Discord formatting palette these render *through*
  (markdown, `<t:…>` timestamps, mentions, embeds, Components V2, limits) is
  catalogued in [`docs/communication.md`](docs/communication.md).
- **`content/`** — GM-editable JSON, changed by change request: `page_*.json`
  (the **sticky pages'** copy for Rules & Info and Guides & Resources; a
  `/page import` override in `content_docs` beats the file until `/page reset`) and `recruitment.json` (the
  Recruitment board's cultural prose). `content.load(name, default)` reads the
  bundled file and falls back to a default on a missing/malformed file, so a
  surface never crashes on a hand-edit typo.
- **`prose.py`** — the managed-prose token layer shared by the sticky pages and
  the Recruitment board: `SafeDict` (keeps an unknown `{token}` visible),
  `safe_format` (never crashes a render) and `live_tokens` (current
  member-count/progression/recruiting values). One home so both surfaces import
  the same substitution without a cycle.
- **`journey.py`** — the **onboarding journey**, the first piece of the core
  structure *People + an append-only Events log + Views*. `people` is one row
  per Discord account; `journey_events` is what happened to them and who did it
  (`EVENT_TYPES`). **Events are only ever inserted** (`db.add_journey_event`
  has no update/delete sibling; only the retention sweep removes rows), and
  every view (cards, `/journey`, the nudge, the Front door board) is *derived*
  from those two tables, so build new people-facing features as views over the
  log rather than new state. The module is split like `metrics.py`: pure
  helpers (`attribute_invite`, the invite-count diff; `match_roster`, a Discord
  name to a roster character + GRM rank name + confidence) and DB-backed
  recorders (`record_join`/`record_leave`/`record_roles_changed`, `reconcile`
  for the one-off `baseline` backfill and missed joins/leaves after a restart,
  `prune` for retention). The gateway listeners (`on_member_join`/`_remove`/
  `_update`, `on_invite_create`/`_delete`) are thin adapters in
  `cogs/journey.py`, loaded only when `JOURNEY_ENABLED` is on and *before* the
  `BOT_COMMANDS_ENABLED` switch (they are not commands). Member names and ids
  are never logged at INFO. Every surface renders from one view-model,
  `journey.Status` (`status_from` folds a person's events for their *current
  stay*; a rank role given by hand in Discord counts as placed). Staff actions
  (`record_greeted`, `record_placed`) append an event once, so a double click
  is harmless.
- **`journey_cards.py`** — the pure welcome-card renderer (`render_card` →
  embed + `ButtonSpec`s). `cogs/journey.py` posts one card per join through
  `FrontDoorMessenger` (always `allowed_mentions=none`), keeps its message id
  in `journey_cards`, and edits it in place on a click, a role change or a
  leave. `/journey unplaced` (same cog, `require_staff`) lists members with no
  rank role from `journey.unplaced` (pure, over live members) via
  `render_unplaced`; its buttons and the card's share one placement path
  (`JourneyCog._place`: guard → allow-list → apply role → log `placed`), so add
  any new "set a role" button on top of that, never beside it.
  The **daily nudge** is the same shape: `journey.nudge_lists` (pure: three
  lists from the statuses and "now"), `render_nudge` (`None` on a quiet day),
  `JourneyCog.post_nudge`, and `serve._journey_nudge_loop`, which asks
  `journey.nudge_due` each minute whether the UK wall clock has reached
  `JOURNEY_NUDGE_TIME` (Europe/London, the agreed exception to "server time is
  Europe/Paris"; the last nudge date is stored so a restart never repeats it).
  For reporting, `journey.stays` cuts the log into visits and
  `journey.front_door` turns them into the Front Door board's numbers;
  `reconcile_scout_leads` (run after a `/link` and in the daily journey loop)
  moves a `scout_leads` row to `joined` and logs `lead_joined` when its
  character is linked to someone who joined through the journey.
  Invite codes are shown through `journey.invite_labels(db, config)`: the two
  `LEGACY_INVITES`, then the `invites` table (`cogs/invites.py`: `/invites
  create|list`, one permanent `unique=True` invite per storefront, behind
  `STOREFRONT_INVITES_ENABLED`), then `JOURNEY_INVITE_LABELS`. **Never delete
  an invite or an `invites` row**: a handed-out link must stay traceable.
  The destination is `runtime_settings.effective_front_door_channel`:
  the front door is a surface behind the `PostGate`, promoted with
  `/posting front-door`.
- **`cogs/_persistent.py`** — the shared **restart-proof button** helper. One
  `ActionButton` (`discord.ui.DynamicItem`) is registered in `setup_hook`;
  every button's `custom_id` is `calcifer:<namespace>:<action>:<args>` and
  clicks are routed to the handler registered for `(namespace, action)`. No
  view objects or message ids are kept in memory. Build any new button with
  `_persistent.button(...)`; never a bare `discord.ui.View` with callbacks.
- **`cogs/`** — Discord slash commands grouped by audience: `members`, `reports`,
  `alerts` (everyone), `admin`, `recruit`, `feeds`, `boards`, `pages`, `posting`,
  `scores`, `invites` (officer-only), `journey` (the welcome team; also holds the journey's
  gateway listeners). `_guards.py` holds the shared `require_admin`/`_is_admin`
  permission gate (Manage-Server perm or the admin role) so every cog gates through
  one helper. Registered in `bot.py:setup_hook`; setting `DISCORD_GUILD_ID` makes
  commands appear instantly instead of ~1h global propagation. `BOT_COMMANDS_ENABLED=0`
  skips the cogs and clears any registered commands (the bot still runs feeds/boards).
- **`ingest.py`** — the **only HTTP surface**: a small aiohttp listener (`Ingest`)
  with exactly three routes: `GET /healthz` (host liveness; DB reachable +
  Discord gateway state, always 200 so a gateway reconnect can't trip a restart
  storm), `POST /webhooks/raidhelper` (shared secret; persists the event for the
  Weekly Events board, then routes the alert into `feed_outbox`) and
  `POST /api/upload/savedvars` (shared secret; the data-spine intake). Both POST
  routes answer 503 until their secret is configured. `serve.py` sets
  `ingest.feed_sender` / `ingest.bot` once the bot exists. **Do not add
  dashboard, read-API or admin routes here**: new controls are Discord commands.
- **`runtime_settings.py`** — the live config overlay. A frozen `Config` is built
  once from env, so nothing can mutate it; instead a small whitelist
  (`OVERRIDABLE`: `posting_mode`, `test_channel_id`, `feed_default_channel_id`,
  `front_door_stage`)
  is saved into the `settings` table by the **`/posting`** cog and resolved
  *through here* per use (`effective_post_gate`, `effective_feed_channel`) — the
  override wins, else the env `Config` default. The board/feed loops and the
  `/board`+`/page` cogs build their `PostGate` from this, so a `/posting mode`
  change applies on the next post. Loop-launch toggles stay env-only.
- **`serve.py`** — all-in-one host entry point: runs the bot, the periodic sync,
  the feed/board/score/digest loops and the ingest listener as asyncio tasks on
  one loop. A long-running
  task dying brings the process down so the host restarts cleanly; one-shot setup
  tasks finishing does not.

### Integration hub ("Calcifer" feed hub)

Optional subsystem that consumes external feeds (Wowhead news, Raider.IO M+,
Raider.IO weekly affixes, Raider.IO realm progression, WoW Audit raids, WCL prog,
Raid-Helper webhooks) and posts branded Discord alerts.

- **`feeds/`** — `base.py` (`FeedSource`/`FeedEvent`), one module per source
  (`wowhead`, `rss_news` (a generic config-driven RSS news source, e.g. Blizzard
  official news), `raiderio_mplus`, `raiderio_affixes`, `raiderio_realm_progress`,
  `wcl_prog`, `wowaudit_raids`, `milestones` (reads the DB, no network)), the shared pure RSS reader `_rss.py` (used by
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
  in `recruitment_config`), `raidprog` (Raid Progress, from `guild_raids`),
  `armoury` (The Armoury: top item levels among recently seen characters),
  `climb` (The Climb: levelling, needs Blizzard-sourced `level`) and
  `frontdoor` (Front Door: the welcome team's staff board, laid out from the
  pure `journey.front_door` view-model over the journey log). Boards that
  show weekly movement share `_weekly.weekly_baseline` (seed, compare, roll at
  the EU reset) over the `board_baselines` table. The `/board` cog and the `_board_loop`/`_score_loop`
  in `serve.py` drive them. (Rules & Info and Guides & Resources are **not**
  boards — they are sticky pages; see `pages.py`.)
- **`pages.py`** — **sticky pages**: multi-message, GM-authored Discord-markdown
  pages the bot owns (currently `rules_info` + `guides_resources`). Copy lives in
  `content/page_<name>.json` (optionally overridden by a `/page import`, stored
  in the `content_docs` table and validated by `validate_page_doc`); `render_page` substitutes the live `{tokens}` from `prose.py`
  but posts the GM's words verbatim (no voice sanitise). `refresh_page` is the
  reconcile engine (pure of Discord, drives the boards messenger + `delete`):
  first run posts, a channel change deletes the old copy and re-posts, same
  channel + same copy is a no-op, and a copy edit edits each message in place.
  Placement is the `/page` cog (`cogs/pages.py`); the destination resolves
  through the `PostGate` with `promoted=True` (the channel is an officer's
  explicit choice). The board loop in `serve.py` also reconciles placed pages, so
  their live tokens stay current. Adding a page = a bundled
  `content/page_<name>.json` + one `PageDef` in `PAGES`; the `/page` choices
  derive from the registry.
- **`posting.py`** — the **deny-by-default posting guardrail**. *Every* outbound
  message (boards, sticky pages and feed alerts) resolves its destination through
  a `PostGate` first: mode `off` posts nothing, `test` redirects to the test
  channel, `live` lets only `/board promote`d surfaces reach their real
  channel. A channel listed in `GAME_LINKED_CHANNEL_IDS` (the in-game chat
  bridges) is refused in every mode. The mode and test channel are set live with `/posting` (env
  `POSTING_MODE` / `TEST_CHANNEL_ID` are the boot defaults); the off → test →
  promote flow lives in the `/posting` + `/board` cogs. New posting
  surfaces must route through the gate.

### SQLite tables (in `db_schema.py:SCHEMA`)

`members` (roster), `loot_history`, `identity_members` (GRM ground truth),
`wishlist_items`, `raid_events` + `attendance`, `guild_snapshots` (daily trend
points); feed hub: `seen_items`, `feed_rules`, `feed_outbox`, `feed_mutes`,
`feed_digest_queue`, `feed_subscriptions`, `news_items`, `mythic_affixes`,
`realm_progress`, `calendar_events`, `mplus_bests`, `milestone_marks`,
`milestones`; our own raid progress: `guild_raids`; boards: `boards`,
`board_baselines`, `recruitment_config` (officer-set recruitment board state);
sticky pages: `sticky_pages` (placement + posted message ids), `content_docs`
(`/page import` copy overrides for the bundled `content/*.json`); recruitment funnel:
`scout_leads` (scouted candidates tracked through new→contacted→trialing→
joined/passed; discovered fields refreshed by a re-scout, officer state/notes
preserved); onboarding journey: `people` + `journey_events` (append-only; see
`journey.py`), `journey_cards` (where each welcome card is) and `invites`
(per-storefront invite codes and their labels); `settings` (the `/posting` live config overlay — see
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
  permission check or import one from a sibling cog. The welcome team
  (Flamekeeper / Officer / GM, `JOURNEY_STAFF_ROLES`) gates through
  `require_staff` in the same module: every journey button and command checks
  it, and Manage Server alone does not qualify.
- **Calcifer only touches allow-listed roles.** Any role add/remove goes
  through `journey.check_role_allowed` (`JOURNEY_ROLE_ALLOWLIST` plus the
  interest roles) before Discord is asked, and only ever as the result of a
  staff click. Don't call `add_roles`/`remove_roles` anywhere else.
- New API clients should subclass `services/_http.py:BaseHTTPClient` (or
  `OAuthClientCredentials`) rather than re-implementing the session/token plumbing.
- **Discord is the control surface.** A new runtime control is a slash command
  (officer-gated through `_guards.py`); a behaviour change is hard-coded and
  shipped as a change request. Don't add web UI, read APIs or new env toggles
  for things a command or a code change covers.
- Tests are organized partly by feature (`test_cogs*.py`, `test_metrics.py`,
  `test_wowaudit.py`) and partly by rollout phase (`test_phase*.py`). The ingest
  routes are exercised through the `ingest_client` fixture in `conftest.py`.
- **One voice, no em dashes.** Everything Calcifer *authors* (board framing,
  footers, feed lines, command copy, voice banks) passes through
  `brand.sanitize` and carries no em dashes; tests enforce it on the banks.
  Relayed external text (a Wowhead headline, a Raid-Helper title) is shown
  verbatim. Snark targets situations, never people.
- **Boards edit in place and never ping.** A board re-renders and edits its own
  message (diff by fingerprint, self-heal if deleted); it never re-posts, and
  `boards/messenger.py` forces `allowed_mentions=none`. Build boards from
  `docs/boards-contract.md`.
- **Env carries secrets, ids and on/off switches only.** Layouts, wording,
  thresholds and schedules are hard-coded and changed by change request. The
  one deliberate addition is the server's own vocabulary for the journey
  (role names, invite labels: `JOURNEY_*`), which belongs to the Discord
  server, not to the code.
- **New feeds are modular.** A new source is a `FeedSource` in `feeds/` plus
  routing rules, never a new cog or a bespoke posting path.
- **Calcifer supports the welcome; people do it.** The journey never DMs a
  member, never assigns a role without a staff click, and never pings.
- **The identity graph is authoritative, not self-service.** Character →
  account comes from GRM (`identity.py`); account → Discord is an officer link
  (`/link`). Don't add flows that pester members to self-register.
- **Intents: default, plus Server Members for the journey. Never Message
  Content.** `JOURNEY_ENABLED` turns on `intents.members` (a deliberate,
  agreed exception: joins, leaves and role changes cannot be seen without it,
  and it must also be switched on in the developer portal). With the flag off
  the bot runs on default intents exactly as before. The Message Content
  intent stays off for good: every feed is push (webhook) or pull (poll),
  never channel scraping.
- **Buttons are stateless and restart-proof.** Interactivity is link buttons,
  slash commands, native Discord (Channels & Roles, Onboarding, Raid-Helper
  for sign-ups) and, for staff surfaces, buttons built with
  `cogs/_persistent.py`: everything a click needs is in its `custom_id`, the
  handler re-reads the database, and nothing depends on an in-memory view. No
  timeout-bound views and no per-message callback state.
- **Persist and self-heal.** Every message id, dedup key and baseline a surface
  relies on lives in the DB, so a restart never double-posts.
- **Server time is Europe/Paris** (`Config.raid_timezone`, EU reset Wednesday).
  "This week" / reset logic computes on Paris time; every *displayed* time is a
  Discord `<t:…>` stamp. Never hard-code a wall-clock string like "20:15 CET".
- **Bounded Mythic+ pulls.** M+ boards and feeds never poll the whole roster:
  raiders first, then active/high scorers, hard-capped (`mplus_feed_member_cap`).
- **Every surface has an on/off switch**, so cutover from an old bot is a toggle
  with no double-posting window.
- **No new runtime dependency or second process** for a feature: new
  long-running work is another task in `serve.py`.

### Surface design rules

Apply these when adding any post, board, alert or command:

1. **One voice, several registers.** The same Calcifer for everyone; warmth
   scales with the audience (visitor, guild, prog team, officers, GM).
2. **Page or knock, never nag.** Ambient information is a board (pull);
   a time-sensitive event is an alert (push, opt-in ping); a question gets a
   command reply. Nothing interrupts that doesn't have to.
3. **Detail scales with audience.** The same event can be a one-liner in a guild
   channel and a full readout in a team channel: route by channel, not by
   shouting.
4. **Officers hold the dials, in Discord.** Configuration is a slash command
   wherever it reasonably can be.
5. **Deny by default.** Nothing reaches a real channel until a human promoted
   it: off → test → live.
6. **Gate by the cheapest mechanism that works.** Channel permissions + feed
   routing first; code-level tiers only when that can't express it.
7. **Pull for management, push for community, sparingly.** Management views
   answer when asked; community surfaces are ambient pages plus a thin opt-in
   stream.
