# Calcifer: the hearth of From the Ashes

## At a glance

**What it is:** a Discord bot that runs the community for an online gaming guild
with a Discord server of 300+ members (the server's size, not a count of bot users). It
pulls member and activity data from several public game APIs into one database,
then uses it to keep the guild's Discord tidy: self-updating information boards,
one news feed in place of several noisy bots, and officer tools for recruitment,
attendance and loot.

**The problem it solves:** the guild's information was scattered across
spreadsheets, websites and half a dozen third-party bots, each posting in its own
style. Calcifer collects the data once, stores it in one place and presents it
consistently, so officers spend less time chasing information by hand.

**Stack:** Python 3.12, discord.py, aiohttp, SQLite (aiosqlite) and Docker, set up
to deploy on Railway. Tested with pytest (965 offline tests, 88% coverage gate in CI) and
linted with ruff.

**Status:** in development, and not currently deployed. It is my own unpaid hobby
project for a guild I run.

**How it was built:** with AI assistance. I designed and directed the project and
reviewed the changes; the code was written with Claude (Anthropic's AI
assistant), and most commits are co-authored with it.

**Try it locally:** the test suite needs no accounts, keys or network access.

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

To run the bot itself you need your own Discord bot token; see
[Quick start](#quick-start) below.

---

> The little fire demon who keeps the **From the Ashes** guild (Hellfire EU)
> warm, tidy, and alive. He runs the castle in the GM's name: pulling the
> guild's scattered data into one place and using it to light up the Discord.

**From the Ashes** is a community built by its members, for its members, and the
Discord is its home. Calcifer is here to protect that home and make it *feel*
like one. His founding job: retire the noisy pile of third-party bots, each with
its own channel, filters, and posting quirks, and replace them with a single
voice that **consumes the data once, stores it, and uses it to light up the
server** with features for the community.

One bot, one process, one SQLite database, two audiences:

- 🏰 **The guild's living home** *(community-facing)*: self-updating boards,
  deduped news, one-glance event and key summaries, and a single charming voice
  in place of a notification dump.
- 🛠️ **The management tool** *(pull-when-needed)*: the GM / Raid Leader / Loot
  views and the recruitment scout, all reading the same trustworthy roster record.

> 📖 The full story, design system, and roadmap live in
> **[docs/vision.md](docs/vision.md)**. This README is the practical front door:
> what it does, how to run it, and every command.

Under the hood he pulls live data from the WoW community APIs into one SQLite
file and lets the guild query it from **Discord**:

- **WoW Audit as the source of truth** ([API](https://wowaudit.com/api-docs)) when
  a team key is set: gear, Great Vault, wishlist/BiS, parses, all pre-joined.
- **Open-API fallbacks/supplements**: [Raider.IO](https://raider.io/api)
  (no key: roster, M+ score, raid progression), the
  [Blizzard API](https://develop.battle.net) (rank, race, level, equipment) and
  [Warcraft Logs](https://www.warcraftlogs.com/api/docs) (parse averages).
- **Discord slash commands**: lookup, roster, search, stats, reports.
- **Discord posts**: routed feed alerts, self-updating living boards and sticky
  pages, all behind one posting guardrail.
- **Officer tooling**: link characters to Discord users, flag mains/alts, add notes.
- **Standalone sync CLI** so you can seed/refresh the database (e.g. from cron)
  without running the bot.

📋 See **[docs/data-sources.md](docs/data-sources.md)** for a full review of what
each API exposes and how we use it.

---

## Quick start

```bash
# 1. Install dependencies
python -m pip install -r requirements.txt

# 2. Configure
cp .env.example .env        # then edit .env (only DISCORD_TOKEN is required)

# 3. Seed the database from the live roster (no Discord needed)
python -m scripts.sync_cli --list

# 4. Run the Discord bot
python -m ftabot.bot
```

`ftabot.bot` runs the slash commands only. Only the **sync** needs the external
APIs.

### Run everything in one process (cloud / mobile)

```bash
python -m ftabot.serve   # Discord bot + feeds/boards + scheduled sync + ingest listener
```

This is what the `Dockerfile` runs. Each piece self-enables: the bot starts if
`DISCORD_TOKEN` is set, the feed/board/score loops start when their interval is
set, and the roster auto-refreshes if `SYNC_INTERVAL_MINUTES > 0`. A small
[ingest listener](#inbound-http-ingest-only) always binds `$PORT` for the host's
health check and the two inbound data pushes.

**Deploying from your phone (no dev environment):** see
**[docs/deploy.md](docs/deploy.md)**: Railway/Render (always-on, managed from a browser),
a free GitHub Actions sync, or Termux on Android.

> **Network note:** the sync calls `raider.io` (and optionally
> `*.api.blizzard.com` / `warcraftlogs.com`). Those hosts must be reachable from
> wherever you run it.

---

## Configuration

All settings come from environment variables (a local `.env` is loaded
automatically). See [`.env.example`](.env.example) for the full list.

| Variable | Required | Purpose |
| --- | --- | --- |
| `DISCORD_TOKEN` | ✅ (bot) | Discord bot token. |
| `DISCORD_GUILD_ID` | optional | Register slash commands to one server instantly. |
| `ADMIN_ROLE_NAME` | optional | Discord role allowed to run officer commands (default `Officer`). |
| `GUILD_NAME` / `GUILD_REALM` / `GUILD_REGION` | optional | Defaults to `From the Ashes` / `hellfire` / `eu`. |
| `DATABASE_PATH` | optional | SQLite file path (default `ftabot.db`). |
| `WEB_HOST` / `WEB_PORT` | optional | Bind address of the ingest listener (default `0.0.0.0:8080`; hosts inject `PORT`). |
| `WOWAUDIT_API_KEY` | optional | Makes WoW Audit the primary source of truth. |
| `BLIZZARD_CLIENT_ID` / `BLIZZARD_CLIENT_SECRET` | optional | Enables Blizzard enrichment. |
| `WARCRAFTLOGS_CLIENT_ID` / `_SECRET` | optional | Enables Warcraft Logs parse averages (the raid tier is auto-detected; `WARCRAFTLOGS_ZONE_ID` only pins a specific tier). |
| `WARCRAFTLOGS_ZONE_IDS` / `RECRUIT_*` | optional | Tunes the `/scout` recruitment scout (tier zones, season window, ilvl floor, open roles). |
| `FEED_POLL_MINUTES` / `FEED_DEFAULT_CHANNEL_ID` | optional | Turn on the **Calcifer feed hub** (Wowhead news → a Discord channel). See [docs/vision.md](docs/vision.md#4b-the-feed--integration-hub-shipped). |
| `YOUTUBE_FEED_ENABLED`+`YOUTUBE_CHANNEL_IDS` / `WOWPROGRESS_FEED_ENABLED` | optional | Extra feed sources: **YouTube** channel uploads (keyless Atom) and **WoWProgress** guild world/realm rank movement (their JSON export; credits WoWProgress.com per their terms). Default off. |
| `POSTING_MODE` / `TEST_CHANNEL_ID` | optional | Boot defaults for the **deny-by-default posting guardrail** (`off`/`test`/`live`) that gates every board and feed alert; change it live with `/posting`. See [docs/deploy.md](docs/deploy.md#the-posting-guardrail-deny-by-default). |
| `BOARD_REFRESH_MINUTES` / `SCORE_REFRESH_MINUTES` | optional | Re-render **living boards** (e.g. *Calcifer's Hot 20*) and refresh the active key-runners' M+ scores on an interval. |
| `SAVEDVARS_UPLOAD_SECRET` | optional | Enables the **data-spine upload** (`POST /api/upload/savedvars`) for RCLootCouncil loot + GRM identity `.lua`. See [docs/data-sources.md](docs/data-sources.md#5-local-desktop-addon-databases--the-identity--loot-ground-truth). |

Everything except `DISCORD_TOKEN` has a sensible default, so the pipeline works
with **Raider.IO only**, no API keys, out of the box. Add `WOWAUDIT_API_KEY` to
switch to the richer WoW Audit data set.

---

## Discord commands

**Everyone:**

| Command | Description |
| --- | --- |
| `/member <name> [realm]` | Full profile card for a character (role icons, class/spec, rank name, the season's raids; level for sub-cap characters, M+ score at cap). |
| `/roster` | The team's sign-up roster for the next raid, grouped confirmed / benched / absent (from WoW Audit). |
| `/whois <@user>` | Characters linked to a Discord user. |
| `/me` | Your own team characters at a glance (ilvl / M+ / vault / wishlist). *Team roster only.* |
| `/stats` | Team roster summary (counts by role/class, average M+). *Team roster only.* |
| `/gmreport` | **GM view**: guild health score, officers, composition, buff coverage. |
| `/raidcheck [team]` | **Raid Leader view**: readiness board with per-raider flags. |
| `/loot [player]` | Loot history for a team player, or the team fairness overview. *Team roster only.* |
| `/wishlist <player>` | A team player's BiS wishlist progress. *Team roster only.* |
| `/lootpriority <item>` | Advisory loot-council priority for a dropped item. *Team roster only.* |
| `/trends` | Guild trends (health, members, ilvl, M+) over recent snapshots. *GM only.* |

**Officers** (the `ADMIN_ROLE_NAME` role or *Manage Server* permission):

| Command | Description |
| --- | --- |
| `/sync [prune]` | Refresh the roster from the APIs. |
| `/link <name> [@user]` | Link/unlink a character to a Discord user. |
| `/setmain <alt> [main]` | Flag a character as an alt of a main (or clear it). |
| `/note <name> [text]` | Set/clear an officer note on a member. |
| `/setteam <name> [team]` | Assign a character to a raid team. |
| `/setofficer <name> [role]` | Set/clear a character's officer responsibility. |
| `/scout [wow_class]` | **Recruitment scout**: find roster upgrades for weak slots (like-for-like by class) from Warcraft Logs + Raider.IO. |
| `/recruit slots\|ilvl\|note\|rule\|message\|show\|reset` | **Recruitment board config**: set open slots per role (+ ilvl floor and note), manage the trial rules, override the pitch, preview the board. |
| `/recruit lead capture\|list\|set\|note\|remove` | **Recruitment funnel**: capture the scout's candidates as trackable leads and move them through the funnel (new → contacted → trialing → joined/passed). |
| `/feed route\|list\|remove\|disable\|enable\|mute\|unmute\|test` | **Calcifer feed hub**: route each feed (Wowhead, M+, Raid-Helper, raid reminders, prog) to a channel with its own style/ping. |
| `/posting status\|mode\|test-channel\|feed-channel` | **Posting guardrail** (*GM or Discord admin only*): switch `off`/`test`/`live` and set the test and default feed channels live, overriding the env defaults. |
| `/board create\|list\|refresh\|promote\|demote\|disable\|enable\|remove` | **Living boards** (*GM or Discord admin only*): create a self-updating board (e.g. *Calcifer's Hot 20*) in a channel; it stays sandboxed until promoted (see the posting guardrail). |
| `/page post\|list\|refresh\|remove\|export\|import\|reset` | **Sticky pages**: post a GM-authored markdown page (Rules & Info, Guides & Resources) to a channel. Re-running `/page post` with a new channel moves the page (old copy deleted); the same channel is a no-op. Copy lives in `ftabot/content/page_*.json`; `export`/`import`/`reset` handle a copy fix without a deploy. |
| `/refreshme` | Refresh your linked character(s)' Mythic+ score now and nudge the boards. |

---

## Calcifer: the feed hub

A blue-flame persona that consumes external feeds and posts branded alerts to
the channels you choose. Turn it on with `FEED_POLL_MINUTES` +
`FEED_DEFAULT_CHANNEL_ID` (see [`docs/vision.md`](docs/vision.md#4b-the-feed--integration-hub-shipped)
for the full design), then shape routing live in Discord:

```
/feed route source:Wowhead news channel:#wow-news
/feed route source:Wowhead news channel:#announcements event_type:wowhead.patch-notes color:Amber ping:@Raider
/feed route source:Raider.IO Mythic+ channel:#mythic-plus
/feed route source:Raid-Helper events channel:#events
/feed route source:WoW Audit raid reminders channel:#raid-chat ping:@Raider
/feed route source:Warcraft Logs live prog channel:#raid-night
/feed route source:YouTube channel:#guild-videos
/feed route source:WoWProgress channel:#progression   # guild rank movement
/feed test source:Wowhead news      # post a sample to verify the route
/feed list                          # show rules + muted feeds
/feed mute source:Raider.IO Mythic+ # silence a feed temporarily
```

Anything without a matching rule falls back to `FEED_DEFAULT_CHANNEL_ID`. First
poll of each source primes silently, so there's no history dump.

---

## Living boards & the posting guardrail

**Living boards** are bot-owned messages that re-render from the database on an
interval and edit themselves in place (never re-post). The shipped boards:

- **Calcifer's Hot 20**: the guild's top-20 Mythic+ leaderboard with weekly
  rank movement.
- **News Highlights**: a calm two-zone news page that replaces the trio of
  news bots: **Top stories** (retail blue-tracker posts, live patch
  notes/hotfixes, general WoW news) is the larger list up top, with a short
  **PTR & Blizzard** corner below for test-realm coverage and other Blizzard
  games. Items are sorted by their RSS section category (`Live`/`WoW` high,
  `PTR`/`Blizzard` low); the classic-games sections (`Classic`/`Mists`/`TBC`)
  stay off the page. It renders from news the feeds archive (**Wowhead**
  (`WOWHEAD_ENABLED`) and **Blizzard's official news** (`BLIZZARD_NEWS_ENABLED`,
  archived into the top zone), so turn the feed loop on (`FEED_POLL_MINUTES`)
  to fill it. Adding another news feed is one `RssNewsSource` line; the feed
  loop nudges the board right after every news poll, so a new post reaches the
  page without waiting for the board tick.
- **Weekly Events**: the week's Raid-Helper events at a glance (localised time
  stamp, sign-up count, a "Sign up ➜" jump-link), fed by the Raid-Helper webhook
  (`RAIDHELPER_WEBHOOK_SECRET`). Sign-ups stay in Raid-Helper; the board is
  read-only.
- **Roster Pulse**: the guild's heartbeat, showing member count, activity,
  readiness and composition at a glance.
- **Recruitment ("We're Looking")**: the guild's open front door, with open slots
  per role (count, ilvl floor, note), the trial rules, and a live-token pitch.
  Officers configure it from Discord with the `/recruit` commands; the cultural
  prose is GM-editable in [`ftabot/content/recruitment.json`](ftabot/content/recruitment.json).

Create one with `/board create`, refresh on a schedule with
`BOARD_REFRESH_MINUTES`, and keep M+ scores current with `SCORE_REFRESH_MINUTES`
(or a member's own `/refreshme`).

**Sticky pages** are the boards' hand-written sibling and the single home for
the guild's managed prose: multi-message Discord markdown pages (headers, `-#`
subtext, emoji) the bot owns: the **Rules & Info** welcome page and the
**Guides & Resources** link page. Place one with
`/page post page:<name> channel:#somewhere`: the first run posts it, pointing it
at a different channel deletes the old copy and re-posts there, and the same
channel does nothing (or quietly applies a copy edit in place). The copy itself
lives in [`ftabot/content/page_*.json`](ftabot/content/) and changes with a
change request. For a fix that can't wait for a deploy, `/page export` hands you
the live copy as JSON, `/page import` applies an edited file (validated against
Discord's limits) and `/page reset` returns to the bundled file. The
board loop keeps placed pages in sync, and pages carry the same live `{tokens}`
(progression, member count, recruiting) so their facts never drift stale. Pages
always post with mentions disabled, so pasted `@role`/`<@id>` text never pings.

Every outbound message (boards **and** feed alerts) passes through one
**deny-by-default posting guardrail** before it can reach Discord, set with
`/posting mode` (boot default: `POSTING_MODE`):

- **`off`** (default): nothing posts anywhere.
- **`test`**: every post is redirected to the test channel (`/posting test-channel`, default `TEST_CHANNEL_ID`).
- **`live`**: boards you have `/board promote`d reach their real channel; the
  rest stay sandboxed.

So a board is reviewed privately and explicitly promoted before the community
ever sees it, and a deploy can never surprise the server. Full workflow in
[docs/deploy.md](docs/deploy.md#the-posting-guardrail-deny-by-default).

---

## Inbound HTTP (ingest only)

There is **no dashboard and no browsable API**: the bot is controlled from
Discord, and anything that isn't a command is a change request against this
repo. The process still listens on one port for the two sources that *push*
data to it, plus the host's liveness probe:

| Route | Description |
| --- | --- |
| `POST /webhooks/raidhelper` | **Raid-Helper webhook**: verifies a shared secret (`?token=`/`X-Signature`), feeds the Weekly Events board and posts the alert. Answers 503 until `RAIDHELPER_WEBHOOK_SECRET` is set. |
| `POST /api/upload/savedvars` | **Data-spine intake**: RCLootCouncil loot + GRM identity `.lua` (auth `SAVEDVARS_UPLOAD_SECRET`); the GM's `scripts/upload_savedvars.py` posts here. Answers 503 until the secret is set. |
| `GET /healthz` | Liveness for the host: DB reachable, member count, Discord gateway state. |

Every other path returns 404.

### How to change things

| You want to change... | Do it with... |
| --- | --- |
| Posting mode / test channel / default feed channel | `/posting mode`, `/posting test-channel`, `/posting feed-channel` |
| Which boards exist, where, and whether they are live | `/board ...` |
| Feed routing, styling, pings, mutes | `/feed ...` |
| Sticky page placement | `/page post`, `/page remove` |
| Sticky page copy | a change request to `ftabot/content/page_*.json` (or `/page export` then `/page import` for a fix that can't wait for a deploy) |
| Recruitment slots, rules, pitch, leads | `/recruit ...` |
| Member links, mains/alts, notes, teams | `/link`, `/setmain`, `/note`, `/setteam`, `/setofficer` |
| An on-demand roster refresh | `/sync` |
| Board layouts, wording, thresholds, schedules, new feeds | a change request (code + env vars) |

---

### Discord bot setup

1. Create an application + bot at <https://discord.com/developers/applications>.
2. Copy the **bot token** into `DISCORD_TOKEN`.
3. Invite the bot with the `applications.commands` and `bot` scopes.
4. (Recommended) set `DISCORD_GUILD_ID` to your server's ID so commands appear
   immediately instead of waiting up to an hour for global propagation.

The bot uses only default intents (no privileged "message content" intent).

---

## Keeping the roster fresh

Run the sync on a schedule with cron (it's idempotent):

```cron
# Refresh every 6 hours
0 */6 * * * cd /path/to/calcifer && /usr/bin/python -m scripts.sync_cli >> sync.log 2>&1
```

Or use `/sync` from Discord whenever an officer wants an on-demand refresh.

By default a sync **prunes** characters who are no longer on the guild roster.
Pass `--no-prune` (CLI) or `prune: false` (`/sync`) to keep them.

---

## How it fits together

```
   ┌───────────┐  ┌──────────┐  ┌───────────┐  ┌───────────────┐
   │ WoW Audit │  │ Raider.IO│  │ Blizzard  │  │ Warcraft Logs │
   │ (primary) │  │ (open)   │  │ (optional)│  │  (optional)   │
   └─────┬─────┘  └────┬─────┘  └─────┬─────┘  └───────┬───────┘
         │ gear/vault/  │ roster+M+/   │ rank/race/     │ parse avg
         │ wishlist/    │ ilvl/raid    │ level/gear     │
         │ parses       │              │                │
         └──────────────┴───────┬──────┴────────────────┘
                                ▼
                         ftabot/sync.py  ──►  SQLite (ftabot.db)
                                                    │
                        ┌───────────────────────────┴───────────────┐
                        ▼ reads                                      ▼ reads
            ftabot/cogs/* (Discord slash cmds)        boards / feeds / pages (Discord posts)
```

| Path | Responsibility |
| --- | --- |
| `ftabot/config.py` | Environment-driven settings. |
| `ftabot/models.py` | `Member` dataclass (incl. `details` blob) + WoW reference data. |
| `ftabot/db.py` | Async SQLite layer; sync vs. manual fields kept separate; auto-migrates new columns. |
| `ftabot/services/` | API clients: `wowaudit`, `raiderio`, `blizzard`, `warcraftlogs`, `raidhelper`. |
| `ftabot/sync.py` | Orchestrates fetch → enrich → upsert → prune → loot/wishlist → snapshot. |
| `ftabot/metrics.py` | Pure scoring: activity, readiness, guild health, loot priority. |
| `ftabot/views.py` | View-models behind the GM / Raid / Loot report commands. |
| `ftabot/wow_data.py` | Editable raid-buff table for comp coverage. |
| `ftabot/cogs/` | Discord slash commands (`members`, `reports`, `alerts`, `admin`, `recruit`, `feeds`, `boards`, `pages`, `posting`, `scores`). |
| `ftabot/ingest.py` | The only HTTP surface: `/healthz`, the Raid-Helper webhook, the SavedVariables upload. |
| `ftabot/serve.py` | All-in-one entry point: bot + loops + sync + ingest listener. |
| `ftabot/ui.py` | Renders members into Discord embeds. |
| `scripts/sync_cli.py` | Headless sync entry point. |
| `docs/vision.md` | The project vision and pillars. |
| `docs/ROADMAP.md` · `docs/DECISIONS.md` | Feature planning (phases 0–5, delivery contracts) · decisions locked + open questions. |
| `docs/data-sources.md` | API capability reference (what each service exposes). |
| `docs/wow-121-discord.md` | Getting the most out of WoW 12.1's native Discord integration with Calcifer (bridge layout + linking/Linked-Roles proposals). |
| `tests/` | Pytest suite (DB, metrics, views, cogs, boards, feeds, ingest). |

A roster sync only ever overwrites **API-sourced** columns. Manually entered
data (Discord links, main/alt relationships and officer notes) is preserved
across syncs.

---

## Development

```bash
python -m pip install -r requirements-dev.txt
ruff check ftabot scripts tests     # lint gate
python -m pytest                    # full test suite
```

The test suite runs entirely offline (no network, real SQLite via temp files).
CI (`.github/workflows/ci.yml`) runs **lint + tests + 88% coverage** on every PR
and on pushes to `main`. Before opening a PR, see
[`CONTRIBUTING.md`](CONTRIBUTING.md) and the branching/release model in
[`docs/branching.md`](docs/branching.md).
