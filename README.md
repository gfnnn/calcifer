# Calcifer: the hearth of From the Ashes

Calcifer is a Discord bot for the **From the Ashes** WoW guild (Hellfire EU). It
pulls roster and activity data from WoW Audit, Raider.IO, the Blizzard API and
Warcraft Logs into one SQLite database and serves it back as slash commands,
feed alerts and self-updating boards. Python 3.12, discord.py, aiohttp and
aiosqlite in one process; API details in [docs/data-sources.md](docs/data-sources.md).

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

**Hosting it:** see **[docs/deploy.md](docs/deploy.md)** for the no-cost and
low-cost options compared and a step-by-step setup guide for each recommended
one: an Oracle Cloud Always Free VM or a machine you own (both with
`docker compose up -d --build`), or Fly.io for a few dollars a month.

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
| `FEED_POLL_MINUTES` / `FEED_DEFAULT_CHANNEL_ID` | optional | Turn on the **Calcifer feed hub** (Wowhead news → a Discord channel). |
| `YOUTUBE_FEED_ENABLED`+`YOUTUBE_CHANNEL_IDS` / `WOWPROGRESS_FEED_ENABLED` | optional | Extra feed sources: **YouTube** channel uploads (keyless Atom) and **WoWProgress** guild world/realm rank movement (their JSON export; credits WoWProgress.com per their terms). Default off. |
| `POSTING_MODE` / `TEST_CHANNEL_ID` | optional | Boot defaults for the **deny-by-default posting guardrail** (`off`/`test`/`live`) that gates every board and feed alert; change it live with `/posting`. See [docs/deploy.md](docs/deploy.md#the-posting-guardrail-deny-by-default). |
| `MILESTONES_FEED_ENABLED` | optional | The **guild milestones** feed: new boss kills, Mythic+ score thresholds and level-cap dings, detected from the stored data (no extra API). Default off. |
| `BOARD_REFRESH_MINUTES` / `SCORE_REFRESH_MINUTES` | optional | Re-render **living boards** (e.g. *Calcifer's Hot 20*) and refresh the active key-runners' M+ scores on an interval. |
| `JOURNEY_ENABLED` | optional | The **onboarding journey**: record joins, leaves, role changes and the invite each new member used (People + an append-only events log). Needs the **Server Members intent** switched on in the developer portal, and *Manage Server* to read invite use counts. Default off. |
| `JOURNEY_RETENTION_DAYS` | optional | Forget a person and their journey events this many days after they left (default `180`; `0` keeps them). |
| `WELCOME_CARDS_ENABLED` / `FRONT_DOOR_CHANNEL_ID` | optional | **Welcome cards**: one staff card per join in the private `#front-door` channel, with Said hi / Set Ashes / Set Visitor buttons. Needs `JOURNEY_ENABLED`, and *Manage Roles* with Calcifer's role above the roles he sets. Default off. |
| `JOURNEY_NUDGE_ENABLED` / `JOURNEY_NUDGE_TIME` | optional | The **daily nudge**: one staff post a day in `#front-door` listing who has not been greeted, placed or checked in on. Time is `HH:MM` on the UK clock (Europe/London, DST-safe; default `18:00`). Needs `JOURNEY_ENABLED`. Default off. |
| `JOURNEY_STAFF_ROLES` | optional | Role names that may press the journey buttons (default `Flamekeeper,Officer,GM`; the `GM_DISCORD_ID` user always may). |
| `JOURNEY_ROLE_ALLOWLIST` | optional | The **only** roles Calcifer may add or remove (default `Ashes,Visitor`). Anything else is refused in code, even where Discord would allow it. |
| `JOURNEY_RANK_ROLES` | optional | Roles that mean "placed" (default `Ashes,Visitor`). The ones also on the allow-list get a `Set <role>` button. |
| `JOURNEY_INTEREST_ROLES` | optional | Interest roles members pick for themselves, shown as "Picked" on cards (default empty; they join the allow-list). |
| `JOURNEY_INVITE_LABELS` | optional | Friendly names for invite codes, `code=Label,code=Label` (e.g. `Qske4gDnYC=Storefronts`). |
| `STOREFRONT_INVITES_ENABLED` / `INVITE_CHANNEL_ID` | optional | **Per-storefront invites**: adds `/invites create` and `/invites list`. New invites land people in `INVITE_CHANNEL_ID` (`#rules-and-info`). Needs the *Create Invite* permission. Default off. |
| `INVITE_STOREFRONTS` | optional | The storefronts that each get an invite (default `Raider.IO,Warcraft Logs,WoWProgress,Guilds of WoW,Linktree,In game`). |
| `GAME_LINKED_CHANNEL_IDS` | optional | Channels bridged to in-game chat. The posting guardrail refuses to send **anything** to them, in every mode. |
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
| `/posting status\|mode\|test-channel\|feed-channel\|front-door` | **Posting guardrail** (*GM or Discord admin only*): switch `off`/`test`/`live` and set the test and default feed channels live, overriding the env defaults. `front-door` promotes the journey's staff surfaces (welcome cards) from the test channel to `#front-door`, or sandboxes them again. |
| `/board create\|list\|refresh\|promote\|demote\|disable\|enable\|remove` | **Living boards** (*GM or Discord admin only*): create a self-updating board (e.g. *Calcifer's Hot 20*) in a channel; it stays sandboxed until promoted (see the posting guardrail). |
| `/page post\|list\|refresh\|remove\|export\|import\|reset` | **Sticky pages**: post a GM-authored markdown page (Rules & Info, Guides & Resources) to a channel. Re-running `/page post` with a new channel moves the page (old copy deleted); the same channel is a no-op. Copy lives in `ftabot/content/page_*.json`; `export`/`import`/`reset` handle a copy fix without a deploy. |
| `/invites create\|list` | **Per-storefront invites** (needs `STOREFRONT_INVITES_ENABLED`): `create` makes one permanent invite (no expiry, no use limit) for every storefront that does not have one yet; `list` shows each invite's label, code and use count. Invites are never deleted. |
| `/refreshme` | Refresh your linked character(s)' Mythic+ score now and nudge the boards. |

**Welcome team** (the `JOURNEY_STAFF_ROLES` roles or the GM; needs `JOURNEY_ENABLED`):

| Command | Description |
| --- | --- |
| `/journey unplaced [page] [post]` | Members with no rank role, longest wait first, ten to a page, each with their roster match and `Set Ashes` / `Set Visitor` buttons. Shown only to you unless `post:True` puts it in `#front-door`. |

---

## Calcifer: the feed hub

A blue-flame persona that consumes external feeds and posts branded alerts to
the channels you choose. Turn it on with `FEED_POLL_MINUTES` +
`FEED_DEFAULT_CHANNEL_ID` (the routing map is in
[`docs/boards-contract.md`](docs/boards-contract.md#6-feed-routing-map)), then shape
routing live in Discord:

```
/feed route source:Wowhead news channel:#wow-news
/feed route source:Wowhead news channel:#announcements event_type:wowhead.patch-notes color:Amber ping:@Raider
/feed route source:Raider.IO Mythic+ channel:#mythic-plus
/feed route source:Raid-Helper events channel:#events
/feed route source:WoW Audit raid reminders channel:#raid-chat ping:@Raider
/feed route source:Warcraft Logs live prog channel:#raid-night
/feed route source:Guild milestones channel:#achievements
/feed route source:YouTube channel:#guild-videos
/feed route source:WoWProgress channel:#progression   # guild rank movement
/feed test source:Wowhead news      # post a sample to verify the route
/feed list                          # show rules + muted feeds
/feed mute source:Raider.IO Mythic+ # silence a feed temporarily
```

Anything without a matching rule falls back to `FEED_DEFAULT_CHANNEL_ID`. First
poll of each source primes silently, so there's no history dump.

**Guild milestones** (`MILESTONES_FEED_ENABLED=1`) is the celebratory feed: a
new Heroic or Mythic boss kill (or a full clear), a member's Mythic+ score
crossing 1,500 and every 500 after, and a levelling character reaching the cap.
It reads only what the sync has already stored, and it fires on the *crossing*:
somebody who joins the guild already past a threshold is not announced.

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
  (`WOWHEAD_ENABLED`) and **Blizzard's official news** (`BLIZZARD_NEWS_ENABLED`, off by default while Blizzard has no working RSS feed;
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
- **Raid Progress**: a bar per difficulty for every raid that is open right
  now, with the guild's realm, region and world standing. Which raid is
  "current" comes from Raider.IO's raid catalog, so it follows a new tier by
  itself.
- **The Armoury**: the top 20 by equipped item level with weekly rank movement,
  the gear twin of the Hot 20. Only recently seen characters are ranked, so a
  long-dormant character on an old item level scale cannot top it.
- **The Climb**: who is levelling (with levels gained this week) and who
  reached the cap since the reset. Levels come from the Blizzard API, so this
  board needs `BLIZZARD_CLIENT_ID` / `BLIZZARD_CLIENT_SECRET`; the others are
  keyless.
- **Front Door**: a staff board for the welcome team: joins by source, how
  quickly newcomers are greeted and placed, who stayed and who left, over the
  last 7 and 30 days (see [The onboarding journey](#the-onboarding-journey)).
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

## The onboarding journey

Calcifer supports the Flamekeepers in welcoming new Discord members: he notices
every join and remembers where it came from, so the people doing the welcoming
have the facts in front of them. He never DMs a member, never gives a rank
without a person clicking, and never pings.

It is built as **People + an append-only Events log + Views**:

- **`people`**: one row per Discord account (display name, joined, left, the
  invite they came via, a linked main character once an officer `/link`s one).
- **`journey_events`**: what happened and who did it (`joined`, `left`,
  `rejoined`, `came_via`, `roles_changed`, `greeted`, `placed`, `checked_in`,
  `character_linked`). Rows are only ever added; nothing is edited.
- **Views** read from those two tables and keep no state of their own.

With `JOURNEY_ENABLED=1` the bot:

- inserts everyone already in the server once, as came via `baseline` with no
  events, so the first week is not flooded (a restart repairs any join or leave
  it missed while it was down);
- records each join, leave, rejoin and role change;
- works out **which invite** a new member used by comparing invite use counts
  before and after the join (`unknown` when two people join at once, when
  nothing moved, or when the bot lacks *Manage Server*);
- matches a member's nickname to a roster character (case and accent
  insensitive, understands `Character (Name)`), with its in-game rank name;
- forgets people `JOURNEY_RETENTION_DAYS` after they leave (a daily sweep).

Nothing is posted by that part: the staff-facing surfaces build on it.

### Welcome cards (`WELCOME_CARDS_ENABLED=1`)

Each join puts one card in `#front-door` (private to Flamekeepers, Officers
and the GM):

- the member (a mention that pings nobody), when their account was made and
  when they joined, as Discord timestamps;
- **Came via**: the invite's friendly label (`JOURNEY_INVITE_LABELS`), or the
  bare code, or `unknown`;
- **Picked**: their interest roles (`JOURNEY_INTEREST_ROLES`), or "nothing yet";
- **Roster match**: the character their nickname points at, with its in-game
  rank and how sure the match is, or "no match";
- **Status**: who said hi and who placed them, and when.

Three buttons, for the welcome team only (`JOURNEY_STAFF_ROLES`):

| Button | What it does |
| --- | --- |
| **Said hi** | Logs `greeted` with whoever clicked. |
| **Set Ashes** / **Set Visitor** | Gives that role (swapping out the other one), then logs `placed` with the role and whoever clicked. |

The card edits itself to show who did what and greys out what is done. It also
updates when the member picks an interest role, is given a rank by hand, or
leaves ("Left before anyone got to them"). Someone coming back gets a new card
marked **Back again** with their previous join date. Clicking twice logs once,
and the buttons keep working after a restart.

### The daily nudge (`JOURNEY_NUDGE_ENABLED=1`)

Once a day at `JOURNEY_NUDGE_TIME` (18:00 UK time by default; it follows the
UK clock through the summer-time changes) Calcifer posts one message in
`#front-door` with up to three short lists:

- **Not greeted after a day**: joined 24 hours ago or more, nobody has clicked
  Said hi.
- **Not placed after two days**: joined 48 hours ago or more, no rank role
  (a rank given by hand in Discord counts as placed).
- **Day-7 check-in due**: joined a week ago, still here, not checked in on.
  Each of these has a numbered **Checked in** button, which logs `checked_in`
  with whoever clicked and greys itself out.

Each list shows at most ten people and then "and N more". A person stays on a
list from day to day only while the thing is still outstanding, and drops off
the moment it is done. On a quiet day nothing is posted. Members who were
already here when the journey was switched on are never listed, and after 14
days someone stops being a daily reminder (they are backlog, which is what
`/journey unplaced` is for). A restart never double-posts: the date of the
last nudge is stored.

### The Front door board

A living board for `#front-door` (create it with `/board create`, type *Front
Door*; it edits itself in place like every board). Over the **last 7 days**
and the **last 30 days** it shows:

- **Joined**, split by source (the invite labels);
- **Greeted within a day** and **Placed within two days**, as a percentage of
  the newcomers who have been here long enough for that deadline to pass (so
  the figure never dips just because someone arrived an hour ago);
- **Left**;
- **Picked**: what interest roles newcomers chose (once
  `JOURNEY_INTEREST_ROLES` is set);
- **Scout leads who joined**: a `/recruit lead` whose character an officer has
  `/link`ed to a newcomer is moved to `joined` automatically, and named here;

and, once, **Still here after 30 days**, for the people who joined 30 to 37
days ago. It reports numbers, not people, and needs `JOURNEY_ENABLED`.

### Per-storefront invites (`STOREFRONT_INVITES_ENABLED=1`)

To know *where* people come from, each place the guild is listed gets its own
invite. `/invites create` (officers) makes one permanent invite per storefront
in `INVITE_STOREFRONTS`, pointing at `INVITE_CHANNEL_ID`, and stores the code
with its label. It only fills gaps, so it is safe to run again after adding a
storefront. `/invites list` shows every invite's label, code and use count.

From then on welcome cards and the Front door board say "Raider.IO" or "In
game" instead of a code. The two older invites are kept and named: `dxPgsGGDrb`
is **Old shared link** and `Qske4gDnYC` is **Storefronts (shared)**. Nothing
here ever deletes an invite: a link that has been handed out keeps working and
stays traceable. `JOURNEY_INVITE_LABELS` can still rename any code.

### The unplaced sweep (`/journey unplaced`)

For clearing a backlog: every member holding none of `JOURNEY_RANK_ROLES`,
longest wait first, ten to a page (`page:2` for the next ten). Each line has
the member, when they joined and their roster match; the numbered buttons
underneath (`1 · Ashes`, `1 · Visitor`, ...) place that person with exactly
the same checks and logging as the card buttons, and the list redraws without
them. It reads the server's live roles, so someone given a rank by hand drops
off by themselves.

Calcifer only ever touches the roles on `JOURNEY_ROLE_ALLOWLIST`. Cards follow
the posting guardrail like everything else: in `test` mode, and in `live` mode
until `/posting front-door stage:live`, they land in the test channel.

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
| Placing new members, the unplaced backlog | the welcome card buttons, `/journey unplaced` |
| Storefront invite links | `/invites create`, `/invites list` |
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

The bot uses default intents, plus one privileged intent when the onboarding
journey is on: with `JOURNEY_ENABLED=1` it asks for **Server Members** (switch
it on under *Bot → Privileged Gateway Intents* in the developer portal first,
or the bot will refuse to log in). It never asks for the "message content"
intent.

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
| `ftabot/raids.py` | Pure raid-tier knowledge: which raids are open, their names, the guild's progress rows. |
| `ftabot/milestones.py` | Pure milestone detection (boss kills, M+ thresholds, level caps) + the stored marks. |
| `ftabot/journey.py` | The onboarding journey: People + the append-only events log, invite attribution, roster matching, retention. |
| `ftabot/boards/` | Living-board renderers (Hot 20, Raid Progress, The Armoury, The Climb, Roster Pulse, News, Events, Recruitment, Front Door). |
| `ftabot/feeds/` | Feed sources (news, M+, affixes, realm progress, milestones, ...) and the poll runner. |
| `ftabot/views.py` | View-models behind the GM / Raid / Loot report commands. |
| `ftabot/wow_data.py` | Editable raid-buff table for comp coverage. |
| `ftabot/cogs/` | Discord slash commands (`members`, `reports`, `alerts`, `admin`, `recruit`, `feeds`, `boards`, `pages`, `posting`, `scores`). |
| `ftabot/ingest.py` | The only HTTP surface: `/healthz`, the Raid-Helper webhook, the SavedVariables upload. |
| `ftabot/serve.py` | All-in-one entry point: bot + loops + sync + ingest listener. |
| `ftabot/ui.py` | Renders members into Discord embeds. |
| `scripts/sync_cli.py` | Headless sync entry point. |
| `scripts/backup_db.py` | Consistent copy of the live SQLite database, with pruning. |
| `docker-compose.yml` · `fly.toml` | Deploy configs for a VM / home machine and for Fly.io. |
| `docs/boards-contract.md` | The board/page design contract, channel layout and feed routing map. |
| `docs/data-sources.md` | API capability reference (what each service exposes). |
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
