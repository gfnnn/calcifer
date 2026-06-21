# From the Ashes — the project vision

> **In one line:** be the home and the toolkit of the **From the Ashes** guild
> (Hellfire EU) — a warm, living **Discord community bot** for the ~300 members,
> *and* the **roster / team-management tool** the guild's management reaches for
> when they need it — both speaking with one charming voice: **Calcifer**, the
> guild's little demon butler.

This is the single, canonical statement of where the project is going and how it
gets there. It supersedes the per-session brainstorming docs that used to live
beside it; everything below is one plan. The only other docs are pure
references: [`data-sources.md`](data-sources.md) (what each API exposes) and
[`deploy.md`](deploy.md) (how to run and operate it). When this doc and the code
disagree, fix whichever is wrong — but keep them in step.

> **Status:** Phases 0–1 shipped as of June 2026. The architecture, core bot, and
> first boards (Hot 20, Events, News, Guides, Rules, Pulse) are live. The data
> spine (GRM/RCLootCouncil) landed in Phase 2. Phases 3–5 (feeds, recruitment,
> advanced scoring) are in design or early build. This doc is the stable vision;
> see [`ROADMAP.md`](ROADMAP.md) for detailed feature planning and [`DECISIONS.md`](DECISIONS.md)
> for open questions.

---

## 1. The vision — two pillars, one bot

The project serves two audiences from one codebase, one process, and one
database. Every feature belongs to one of these pillars, and **the test for any
new idea is whichever pillar it serves:**

### Pillar A — the guild's living home *(community-facing)*

The Discord *is* the community: ~300 people who raid, log, push keys, and hang
out. Today the bots that serve them **shout** — every M+ key is a tall embed,
three news bots post the same article thrice, the welcome and roster posts rot
until someone hand-edits them, the recruitment channel is a listings dump. The
information is all there; it's just **loud, stale, and scattered.**

Calcifer fixes that not by adding more bots, but by becoming the **butler of the
house** — the keeper who runs the place so well you feel it more than you see it.
That's the role he played in Howl's castle, and it's the test every
community feature must pass: **does this make Calcifer a better butler for these
300 people?** A butler doesn't shout, doesn't hoard clutter, and never makes you
do the housework yourself. The payoff: **muted channels become pages people
open**, the spam stops, the chores disappear, and the whole server gains a single
voice that ties it together — a community hub, not a notification dump.

### Pillar B — the management tool *(used by management, when required)*

Beneath the community face, the same data powers the tools the guild's
**management** (the GM and the **Flamekeepers**) actually run the guild with:
*is the guild healthy? who do I bring? where should the loot go? who should we
recruit? who is this person, really?* These are **pull-when-needed** surfaces —
Discord commands and browser pages — not ambient spam. They are already live
(the GM / Raid Leader / Loot views, the recruitment scout) and they deepen as the
data spine lands (§5).

### The shared spine

Both pillars sit on **one trustworthy record of the roster** — a single SQLite
database, fed by the sync, that answers *"who is this character, and how are they
doing?"* The long-term core (§5c) is to answer *"who is this **person**?"* —
confidently linking every **character → account → Discord user**. That graph is
what turns "a bot with some commands" into the guild's **centralised data hub**,
and it's why the integrations exist at all.

> **Six things that make him a butler** (the experience pillars Pillar A
> decomposes into): **Tidy** (boards + digests over spam), **Personal**
> (member-aware replies, the right register per person), **Anticipatory**
> (drift-maintenance, roster automation, signup nudges), **Quiet**
> (calls-and-responses, opt-in pings, digests over firehoses), **Charming** (the
> voice, moods, a coherent emoji set), **Invisible machinery** (the always-on
> daemon, edits in place, dedup, no fuss).

---

## 2. Calcifer — the persona and the bond

Three things make him *him*; they set who he speaks to and how.

**The bond.** Calcifer is **Harry's bound demon**, and Harry is the **Guild
Master**. The mapping to the film is exact and load-bearing: Howl → **Harry**;
Calcifer the bound fire demon → **the bot**; the moving castle he powers → **the
guild's Discord**; Calcifer *is* the hearth keeping it alive → the always-on
daemon (`serve.py`). He **runs the castle in Harry's name**, grumbling about the
work, but is the literal fire the place depends on. He calls the GM **"Harry"**
(the man) and uses **`gfn`** for Harry's online alias (his characters/accounts) —
for the data spine, `gfn` is Harry's identity anchor.

**The register tiers** follow the bond and map onto the existing access tiers
(§5d):

- **To Harry (the GM)** — familiar, contract-bound ("*Another job, Harry? The
  things I do for this contract.*"). Needs `GM_DISCORD_ID` to recognise him.
- **To Flamekeepers / management** — the castle's lieutenants who keep the flame;
  a notch more cooperative. They own the management-only spaces.
- **To Team Chemistry (the CE prog team)** — the competitive register is allowed
  here, sharp about the raid, in the team-only channels.
- **To the guild** — landlord of the hearth: sardonic, warm, keeps them warm
  whether they thank him or not.

**Why a fire demon fits.** "From the Ashes" is **phoenix / rebirth**, on realm
**Hellfire** — fire *is* the guild's identity, which is *why* a fire demon is the
right familiar. The guild's **ember** (`0xCC4422`) is the house's fire; Calcifer's
**blue flame** (`0x2EA3FF`) is his own. The colour contrast (§6) is lore, not
decoration.

**The voice.** Canon Calcifer — dramatic, sardonic, put-upon, secretly proud,
bargain-driving, short and dry. Calibrated to the guild's own About Us
(community-first, founded 2008, zero-pressure — *with* a Cutting Edge edge in
Team Chemistry). Two hard rules:

- **Snark targets situations, never people** — wipes, the contract, the chores,
  the patch; **never** a learner, a low parse, or a missed key. (Their values:
  patience with newer players, a safe/supportive environment.)
- **He never writes em dashes** (an en dash once or twice at most), so his copy
  never reads machine-made. Enforced in code at two layers: the line banks (and
  their tests) carry none, and the feed publisher runs everything Calcifer
  *authors* through `brand.sanitize` at render time, while leaving *relayed*
  external text (a Wowhead headline) untouched.

He also **paces to the content**: fast and excited for Mythic+ (an adrenaline
burst), measured and methodical for raid and logs (a patient, weeks-long burn).

---

## 3. How we build it — architecture & approach

The reason the vision is *achievable* and not a wishlist: nearly every feature is
a variation on the **same few foundations**, all inside the code that already
exists.

**Extend, don't fork.** Everything lives in the existing `ftabot` process. It
reuses the DB, the service clients, and the all-in-one `serve.py` event loop —
**no second deploy, no second token, no new runtime dependency** beyond
`discord.py>=2.6` for Components V2. New long-running work is just another
concurrent task in `serve.py` (the feed loop, the board loop, the score loop,
the periodic sync), each self-enabling on config exactly like the sync does.
`db.py` auto-migrates new tables/columns, so `seen_items`, `feed_rules`,
`feed_outbox`, `boards`, etc. drop in with no migration tool.

**The one engine = managed posts + one voice.** Two foundations turn the whole
feature list into small renderers:

1. **Living boards / managed posts** — a bot-owned message, pinned in a channel,
   that **renders from the DB and edits itself in place** (never re-posts). The
   `boards` table holds channel + message id + config; `boards/engine.py` does
   render → diff-by-fingerprint → edit, with self-heal if the message vanished.
   *Everything* — the M+ leaderboard, the events board, the news highlights, the
   roster post, the recruitment board, the concierge cards — is an instance of
   this one pattern.
2. **Calcifer's voice over existing data** — one `brand.py` (colour, icon,
   author/footer, moods, line banks, the em-dash rule) that every surface renders
   through, so thirty different messages read as one product.

**Pure, offline-tested renderers.** The scoring (`metrics.py`), routing
(`router.py`), and board renderers (`boards/*`) are pure `(data) → result` with
no I/O, unit-tested against temp-file SQLite with the network mocked. Keep them
that way — it's what makes the project safe to extend.

**The posting guardrail (deny-by-default).** *Every* outbound message — boards
and feed alerts alike — resolves its destination through one `PostGate` first.
`POSTING_MODE=off` posts nothing; `test` redirects everything to a confirmed
`TEST_CHANNEL_ID`; `live` lets only `/board promote`d surfaces reach their real
channel. So a surface is reviewed privately and promoted deliberately before the
community ever sees it, and a deploy can never surprise the server. Any new
posting surface must route through the gate.

**The web app doubles as a webhook receiver.** `web.py` already runs FastAPI on
`$PORT`, so push sources (Raid-Helper) hit a signature-verified route here and
enqueue a durable `feed_outbox` row; pull sources run on the feed loop. No extra
service.

**Standing constraints (binding on every phase):**

- **Default Discord intents only** — no privileged Message Content intent. Every
  feed is push or pull, never channel-scraping.
- **Persist + self-heal** — board `message_id`s and `seen_items` live in the DB
  on a **persistent disk**, so a restart never double-posts; a deleted board is
  re-created, not errored.
- **Server time = Europe/Paris (CET/CEST)** — all "this week" / "next raid" /
  reset logic computes on Paris time (EU reset is Wednesday); every *displayed*
  time is a Discord dynamic `<t:…>` stamp so it localises to each viewer. Never
  hardcode "20:15 CET".
- **No stateful interaction handlers** — interactivity is **link buttons** (open
  a URL), **slash commands** (the request/response pattern), and **native
  Discord** (Channels & Roles / Onboarding for opt-in roles; Raid-Helper for
  sign-ups). Calcifer's job is structure and presentation, not interaction
  engineering.
- **Content in the repo** — static prose (rules, welcome) lives as template files
  the GM edits via a GitHub change request; Calcifer owns the post and renders
  it. No in-Discord editor or dashboard (a future, out-of-scope project).
- **Bounded M+ pulls** — M+ boards/feeds never poll all ~300. The pollset is
  bounded (raiders first, then above a score/percentile threshold *or* logged in
  within a window) and hard-capped (`mplus_feed_member_cap`), so it stays under
  Raider.IO limits at scale.
- **Cutover by toggle** — every feed/board/post has an on/off switch, so go-live
  is the GM flipping Calcifer's surfaces on and the old bots off, with no
  double-posting window.

---

## 4. Pillar A — the guild's living home

Every pain point in the real server resolves to a butler's move, and each runs on
data the bot already holds:

| The room (channel) | The problem today | The butler's fix |
| --- | --- | --- |
| `#rules-and-info` | facts **drift stale** (progression, recruiting) | managed post: hand-written prose stays, live tokens refresh; drift-watch nudges (§4 boards) |
| `#guides-resources` | **wall of raw `discord.gg` links** | masked-link / button grid + class emoji; dead-invite watch |
| M+ bot feed | **a card per key** + logs interleaved | retire the Raider.IO bot → threshold/PB alerts + the M+ board + weekly digest + a log lane |
| News channel | **3 bots, duplicated image-embed bloat** | one deduped feed → a two-zone **News Highlights** board |
| `#event-signups` | **scrolling Raid-Helper posts** | one-glance **Events board** + M+ group-formation math; sign-ups stay in Raid-Helper |
| Team Chemistry roster | **hand-edited twice a week** | auto-rotating **roster post** from WoW Audit |
| Recruitment (mgmt) | **spammy listings dump** | calls-and-responses (`/scout`, `/recruit`) + one curated **Recruitment Board** (§5b) |

### 4a. Living boards — the centrepiece

A **Board** is a bot-owned message that re-renders from the DB on an interval (or
when an event marks it dirty), turning a channel of dead alerts into a *live
page*. The engine + `/board` cog + the `_board_loop`/`_score_loop` in `serve.py`
are **shipped**; boards roll out one renderer at a time:

| Board | Refreshes | Content | Status |
| --- | --- | --- | --- |
| **Calcifer's Hot 20** (M+ leaderboard) | hourly + on score change | guild top 20 by M+ score; ▲/▼ vs last week; biggest climber | **shipped** |
| **Weekly Events** | Raid-Helper webhook + tick | the week's events at a glance: localised time stamp, sign-up count, "Sign up ➜" jump-links (per-role split + comp gaps pending a validated payload) | **shipped** |
| **Great Vault tracker** | every 6h | who has 1/3, 2/3, 3/3 vault slots this reset | planned |
| **Raid Progress** | on new kill | bosses down N/H/M, best pull on the prog boss | planned |
| **Key of the Week** | weekly reset | single highest timed key + the group | planned |
| **News Highlights** | on new high-priority article | two zones: highlighted patch notes/blue posts up top, clean latest-list below | **shipped** |
| **Roster pulse** | every 6h | `/stats` made ambient (members, active %, avg ilvl/M+) | **shipped** |
| **Raid-night roster** (Team Chemistry) | on WoW Audit change | next prog night's selected roster by role, auto-rotating Wed↔Sun | planned |
| **Concierge helper cards** | on data change | one sticky per channel: "here's what you can do here" + slash/link CTAs | planned |
| **Recruitment board** (mgmt) | on `/scout` / new LFG | open slots + ranked, vetted leads | planned |

Boards are **mobile-first** (most of the 300 are on phones) and **never ping**
(`allowed_mentions=none`).

### 4b. The feed / integration hub *(shipped)*

The hub consumes external feeds and posts branded alerts, replacing the scattered
third-party bots with one voice. The pipeline (`feeds/runner.py`) is
**fetch → dedup (`seen_items`) → route (`router.py`) → render (`publisher.py` +
`brand`/`persona`) → publish**, with a durable `feed_outbox` for push events.
First poll of each source primes dedup silently (no history dump). We **consume
push where a service offers it, pull where it doesn't, and replace
Discord-bot-only sources:**

| Source | Emits | How | Status |
| --- | --- | --- | --- |
| **Wowhead** (RSS) | news / blue-post / patch-notes | pull RSS (rebrandable; replaces **PatchBot**) | on by default |
| **Raider.IO M+** | timed high keys | pull (bounded pollset) | off by default |
| **Raid-Helper** | event created/updated/deleted | **push** webhook (secret-verified) → `feed_outbox` | off by default |
| **WoW Audit raids** | raid-night reminders + signup count | pull | off by default |
| **Warcraft Logs prog** | best pull per boss + kills (absorbs **Wipefest**) | pull | off by default |

Officers route each source live with `/feed route|list|remove|disable|enable|
mute|unmute|test` — per-source channel, colour, role-ping, and muting, no
redeploy. Anything unmatched falls back to `FEED_DEFAULT_CHANNEL_ID`.

### 4c. Taming the firehose — digests over spam *(planned)*

The plumbing is a firehose-to-channel model today; the next step adds a delivery
*mode* so a rule can be realtime **or** batched (a `mode`/`digest_window` key in
the existing `style_json`, plus a `feed_digest_queue` table):

- **M+** — realtime only for *notable* keys (≥ threshold, a personal best, a
  score-milestone crossing); the long tail rolls into a weekly "🔑 Keys this
  week" roundup; the Hot 20 board carries "who's pushing" ambiently. The digest
  and board count **everyone** (chill runs included) so nobody is gatekept.
- **Wowhead** — tier on ingest: `high` (patch notes, hotfixes, blue posts, PTR)
  feeds the News Highlights board (and may fire one realtime ping for a major
  drop); `low` filler batches into a weekly roundup or is dropped per-rule.
  `seen_items` dedup kills the duplicate-across-bots problem for free.
- **Logs** — a low-key "📊 new log" lane so loggers get credit without noise.

### 4d. Personal & toggleable *(planned)*

Without any stateful handlers: **subscription roles** via native Channels & Roles
or a `/alerts` slash toggle (so only opt-ins get pinged); **per-rule persona tone
and cadence** in `style_json` (serious channels quiet, `#banter` full Calcifer);
a personal **`/me`** card (your characters' ilvl/M+/vault/wishlist) *(shipped)*;
`/feed preview` & `/board preview`; optional quiet hours (Paris time).

---

## 5. Pillar B — the management tool

The surfaces management pulls up when they need them. These are command/page
surfaces, not ambient spam, gated to the right tier (§5d).

### 5a. The views *(shipped)*

Three role-specific views render in the browser (the Raid and Loot sections of
the `/app` dashboard, plus the GM health pulse on Home) and in
Discord (`/gmreport`, `/raidcheck`, `/loot`, `/wishlist`, `/lootpriority`,
`/trends`), reading the same DB the sync populates. The scoring is pure
(`metrics.py`):

- **GM view** — *is the guild healthy?* A composite **guild-health** score
  (activity, readiness, progression, role balance, retention), the officer corps,
  composition, raid-buff coverage, and trend lines over snapshots.
- **Raid Leader view** — *who do I bring?* A **readiness board** per raider
  (performance, gear percentile, prep, engagement, wishlist) with flags, plus
  buff-coverage gaps. Filterable by team.
- **Loot view** — *where should it go?* A **fairness** table (items per raid,
  attendance, days since last) and an advisory **loot-priority** ranking for a
  dropped item (BiS, upgrade %, attendance, recency, recent wins).

### 5b. Recruitment

- **`/scout` (shipped)** — not "paste a name, vet it" but a **roster gap-analysis
  + market scan**: find our weak slots (per class/spec, by parse/M+/ilvl), then
  fetch better players *of the same class* from Warcraft Logs `characterRankings`
  + Raider.IO who clear our bar and out-score the incumbent — like-for-like,
  strong slots left alone. Scored on **trajectory over peak** (a steady multi-
  season climb beats one spiky season), with poachability context (current guild
  + progress) attached. Public APIs expose discovery *by performance*, not *by
  intent* — so these are **cold leads**, not raised hands.
- **The recruitment funnel + board (planned)** — add two pull `FeedSource`s for
  *intent* (WoWProgress LFG + Raider.IO recruitment), vet each advertised name
  with the existing scorer, and cache lifecycle in a `scout_leads` table
  (`new → shortlisted → contacted → trial → passed/expired`). One curated,
  management-only **Recruitment Board** then shows *open slots × fresh, vetted,
  un-contacted* candidates — raised hands floated above cold leads — instead of a
  listings dump. Officers mark state with slash commands; no stateful handlers.

### 5c. The data spine — the long-term core

The APIs (WoW Audit · Raider.IO · Blizzard · WCL) describe **characters**. They
can't tell us **who's who**. That truth lives in two addons on the GM's PC, and
**ingesting them is the foundational workstream everything else rides on**:

- **Guild Roster Manager (GRM)** — officer-maintained **identity**: alt↔main
  groupings, join dates, name changes, notes.
- **RCLootCouncil** — the full **loot ledger** (WoW Audit's API doesn't expose
  loot).

Both persist as WoW SavedVariables `.lua`, parsed **offline** and **merged** with
the API data per character (detail in [`data-sources.md`](data-sources.md) §5).
The result is a high-confidence **character → account → Discord** identity graph
+ a real loot ledger — sourced from data the GM **already collects**, never from
pestering members to self-register. **Linking is authoritative, not
self-service:** Calcifer is user-focused, not a data-capture tool.

**The intake has shipped.** A dependency-free SavedVariables parser
(`services/savedvars.py`), the two mappers (`services/rclootcouncil.py`,
`services/grm.py`), the account-grouping (`identity.py`) and a secret-protected
upload route (`/api/upload/savedvars`, fed by the stdlib `scripts/
upload_savedvars.py` on the GM's PC) now land **loot → `loot_history`** and
**GRM identity → the `identity_members` table + an alt↔main overlay on the
roster**. The character → account half of the graph is live; the last hop —
attaching **Discord** to each account — stays a manual/officer link until a
reliable signal exists, and confident member-personalisation deepens as it does.
The web profile (`/member/{name}`) now surfaces each character's GRM **join
date, rank and main/alt standing** alongside the (now real) loot ledger.

> ⚠️ **Not yet validated on real data.** Every piece is unit-tested, but only
> against synthetic fixtures — the GRM nesting/field names are inferred from the
> addon source, not a live dump. Tracked in
> [`data-spine-validation.md`](data-spine-validation.md); confirm against a real
> `Guild_Roster_Manager.lua` before relying on it in production.

### 5d. Access tiers

Command visibility and board placement follow one ladder, which is also
Calcifer's register (§2) and the existing admin gate:

**GM (Harry) → Flamekeepers / management → Team Chemistry (prog) → the guild →
visitors.** Visitors see `#rules-and-info` / guides / news; the guild sees
community + M+/events; Team Chemistry + management see the rules-and-roster
channel; **management only** sees recruitment + the identity graph (members
self-view their own).

---

## 6. The design system — look & voice *(brand layer shipped; V2 + charm planned)*

The shared presentation language so every channel reads like a designed page, not
a bot's scratchpad. **Hot, clean, charm.** One module, `brand.py`, is the single
source of truth — colours, icons, author/footer builders, moods, line banks — and
every surface renders through it. No inline hex, no ad-hoc emoji.

**Colour encodes source + urgency** (one accent per panel, never rainbow):

| Token | Hex | Means |
| --- | --- | --- |
| `GUILD_EMBER` | `0xCC4422` | "our own data" — roster, GM/raid/loot, boards about us |
| `BLUE_FLAME` | `0x2EA3FF` | "Calcifer relayed a feed" — news, M+, external |
| `AMBER` | `0xF5A623` | "soon / attention" — thin signups, prog stalls |
| `EMBER_RED` | `0xCC2222` | "now / big" — major patch, blocker, **first kill** |
| `class_color()` | per-class | identity — member cards, leaderboard rows |

**Iconography** is a fixed vocabulary (🔑 M+, 💀 kill, 🔥 progress, 📅 events,
📰 news, 🔵 blue post, 🎁 vault, 🔎 recruit, 🛡️/💚/⚔️ roles). A curated custom
**emoji kit** (`:calcifer_*:` moods, `:fta_*:` glyphs, role + class crests) is
resolved by name from the guild at runtime, with the unicode above as fallback —
nothing blocks on the upload.

**Components V2 (`discord.py>=2.6`)** is the headline look upgrade for boards:
Containers (a bordered, accent-coloured panel), Sections (text + thumbnail),
Separators, media galleries, and **link buttons** — migrated board-by-board,
backwards-compatible. Simple one-line alerts and command replies stay classic
embeds.

**Charm** grows the voice past the starter banks: 6–10 lines per mood/event with
anti-repeat memory; a **mood** system (Thinking / Smug / Grumpy / Delighted /
Alarmed / Sleepy / Scheming / Dismal) expressed via author-icon + prefix emoji +
line pool; context-aware quips; board sign-offs; the rotating presence. Domain
pacing (§2) and the per-area tone dial compose with it.

**Cleanliness / rate-limit contract:** edit never re-post; diff before editing;
stagger channels; cadences run hourly/6h (far under the ~30–60s/message floor);
respect embed/V2 length budgets (Hot 20 caps at top 20).

---

## 7. The roadmap — one sequence

Each phase is independently shippable (Discord + web + offline tests) and lands a
visible win. Phases 0–1 have **shipped**; the rest is the forward plan. The data
spine (§5c) runs as a **parallel track** rather than blocking these.

| Phase | What ships | Status |
| --- | --- | --- |
| **0 · Foundations** | rebrand → Calcifer; `discord.py>=2.6`; `brand.py`; the board engine (`boards` table + `_board_loop` + diff-edit + self-heal); the deny-by-default posting guardrail; the feed hub (sources → router → publisher), `/feed`, the `/scout` recruiter; the GM/Raid/Loot views | ✅ shipped |
| **1 · First board** | **Calcifer's Hot 20** (guild top-20 M+) + a bounded targeted M+ score refresh (`/refreshme` + the score loop) | ✅ shipped |
| **2 · Tidy the rooms** | ✅ Weekly Events board (read-only over Raid-Helper) · ✅ News Highlights board (retire 3 news bots) · ✅ rules-info drift-watch · ✅ guides-resources link tidy · the auto-rotating raid-night roster post | partial |
| **3 · Quiet the firehoses** | digest mode (`feed_digest_queue`) · M+ threshold/PB gating + log lane · `/alerts` subscription roles · per-rule tone/cadence · `/me` | planned |
| **4 · Celebration & charm** | first-kill milestones · Raid Progress board · Weekly MVP / "Ashes Awards" · achievement shoutouts · concierge helper cards · the mood/emoji/voice charm pass | planned |
| **5 · Recruitment funnel** | WoWProgress + Raider.IO LFG feeds → vetting → the lifecycle Recruitment Board (`scout_leads`) + `/recruit` calls | planned |
| **— · Data spine** *(parallel)* | SavedVariables parser + companion uploader + upload route + RCLootCouncil loot + GRM identity → roster alt↔main overlay **shipped**; character↔Discord link remains | 🟡 in progress |

### 7a. The delivery contract — what "done" means for any surface

Phases 0–1 are live, so the project is now **post-live**: every remaining surface
ships *onto a running bot* rather than into an empty repo. That changes the unit
of work from "write the feature" to "land it without disturbing the 300 people
already in the castle". A feature is not delivered until it clears this gate, and
the gate is what turns each "planned" row below into a concrete finish line:

1. **A pure renderer with offline tests.** The scoring/routing/render logic is a
   pure `(db, config) → payload` function, unit-tested against temp-file SQLite
   with the network mocked — exactly like `metrics.py`, `router.py`, and
   `boards/hot20.py`. No surface ships without its test.
2. **A self-enabling, default-off config flag.** One env flag turns the feature
   on, defaulting off, following `feeds_enabled` / `boards_enabled` /
   `scores_enabled`. Landing it means a `Config` field (read only in
   `config.py`), an `.env.example` entry, and a README Configuration row — as one
   change, not three.
3. **It routes through the posting guardrail.** Every byte bound for Discord
   resolves its destination via `PostGate.resolve(...)`; no surface calls
   `channel.send` directly. Non-negotiable for any new posting surface.
4. **It survives a restart.** Every message id, dedup key, and baseline it leans
   on persists in the DB on the persistent disk, so a redeploy never double-posts
   and a deleted message self-heals — the `boards` / `seen_items` / `feed_outbox`
   pattern.
5. **Toggle → review in private → promote.** Validated in `POSTING_MODE=test`
   against live data in the sandbox channel, reviewed in every state, then `live`
   + `/board promote` (boards) or the rule moved off the test channel (feeds). The
   old bot it replaces is switched off in the same step, so there is never a
   double-posting window.
6. **The docs move in step.** When it lands, its README command/route/config rows
   appear, its §7 status flips to ✅, and any §9 concern it closes is deleted. The
   backlog shrinks, never drifts.

Two cross-cutting prerequisites unblock whole groups of these. One has now
**shipped**; the other still gates go-live:

- **A real server timezone — ✅ shipped.** The time-anchored surfaces (Weekly
  Events, raid-night roster, raid reminders, "this week" digests) now read their
  wall-clock times through `Config.raid_timezone`: Europe/Paris as an IANA zone
  via `zoneinfo` (`RAID_TZ`), DST-correct across the CET↔CEST switch, replacing
  the old static `RAID_TZ_OFFSET_HOURS` (any fixed offset is itself a zone name).
- **The config values in §8.** "Values still needed" is the literal set of inputs
  these features are blocked on — a missing channel id, role id, or webhook secret
  is usually the only thing between a *built* surface and its go-live toggle.

### 7b. Path to delivery — what each planned feature still needs

Each row names the **inputs** it is blocked on (config / data / an upstream
feature), the **build** (the new table/source/renderer/command — every one a
variation on the shipped engine), and **done** (its finish line, on top of the
§7a gate). Nothing here needs a new deploy, token, or runtime dependency.

**Phase 2 — Tidy the rooms**

| Feature | Inputs it needs | Build | Done when |
| --- | --- | --- | --- |
| Weekly Events board | Raid-Helper webhook secret + server id; the events channel id (server tz ✅ landed) | an `events` board renderer over the stored Raid-Helper events + M+ group-formation math, with `<t:…>` stamps | the week's events render at a glance (time, signups by role, comp gaps, jump-links); sign-ups stay in Raid-Helper |
| News Highlights board | the feed default channel; the high/low tiering rule on ingest | a two-zone `news` board renderer over a stored news cache + Wowhead tiering | the 3 news bots are retired and duplicates are gone (dedup proven) |
| rules-info drift-watch | the rules/info prose as a repo template; the channel id; the live tokens to refresh | a managed `rules` post renderer + a drift nudge | hand-written prose stays, live tokens refresh, drift is flagged |
| guides-resources tidy | the curated link + class-emoji set (content in repo) | a `guides` board (masked-link / button grid) + a dead-invite watch | the raw `discord.gg` wall becomes a button grid |
| raid-night roster post | WoW Audit team key; the Wed↔Sun schedule (server tz ✅ landed) | a `raidnight` board over WoW Audit's selected roster, auto-rotating | the twice-weekly hand-edit is gone |

**Phase 3 — Quiet the firehoses**

| Feature | Inputs | Build | Done when |
| --- | --- | --- | --- |
| Digest mode | a weekly flush schedule (Paris time) | a `mode`/`digest_window` key in `style_json` + a `feed_digest_queue` table + a digest renderer | any rule can be realtime *or* batched; batched items post on schedule |
| M+ gating + log lane | the notable-key thresholds (level exists; add PB / percentile / milestone); a personal-best store | gate notable keys to realtime, roll the rest into the weekly digest; a low-key log lane | only notable keys ping; chill runs still counted in the digest + Hot 20 |
| `/alerts` subscription roles | the opt-in role ids (native Channels & Roles or the slash toggle) | an `/alerts` command mapping toggles → role add/remove (request/response only) | loud pings are opt-in; only opt-ins are notified |
| Per-rule tone/cadence | (none new) | a `tone`/`cadence` key in `style_json`, composed with `PERSONA_TONE` | serious channels quiet, `#banter` full Calcifer |
| `/me` card | (none new — existing member data) | a `/me` command rendering the caller's characters' ilvl/M+/vault/wishlist | a member can pull their own card |

**Phase 4 — Celebration & charm**

| Feature | Inputs | Build | Done when |
| --- | --- | --- | --- |
| First-kill milestones | WCL prog creds; a stored "already killed" set | detect a new boss kill in the prog feed; a celebratory render (`EMBER_RED`) | a first kill fires exactly once, idempotent across restarts |
| Raid Progress board | WCL creds; the prog channel | a `progress` board (N/H/M down, best pull on the prog boss) | current progression renders ambiently |
| Weekly MVP / Ashes Awards | the metric to rank on; a weekly schedule + a winners history | a weekly award renderer with anti-repeat memory | a weekly MVP posts without trivially repeating a winner |
| Achievement shoutouts | an achievement source (Blizzard guild **activity/achievements** / WCL) | detect + render new achievements | a shoutout fires on a notable achievement |
| Concierge helper cards | the per-channel CTA copy (content in repo) | a `concierge` board (one sticky per channel) | each channel has a "here's what you can do" card |
| Charm pass | the `:calcifer_*:` / `:fta_*:` emoji uploaded; expanded line banks (Claude-drafted, GM-curated via GitHub) | grow `brand.py` line banks + the mood system with anti-repeat memory | 6–10 lines per mood, anti-repeat, em-dash rule still enforced by tests |

**Phase 5 — Recruitment funnel**

| Feature | Inputs | Build | Done when |
| --- | --- | --- | --- |
| LFG intent feeds | the WoWProgress + Raider.IO recruitment endpoints; a `scout_leads` table | two pull `FeedSource`s for *intent*, each advertised name vetted by the existing scorer; lifecycle `new → shortlisted → contacted → trial → passed/expired` | raised hands are ingested, vetted, and cached |
| Recruitment Board (mgmt) | `RECRUIT_ROLES_OPEN` + ilvl floor (set); a management-only channel | a `recruitment` board: open slots × fresh, vetted, un-contacted leads, raised hands above cold ones; officer slash commands mark state | the listings dump becomes one curated board |
| `/recruit` calls | (the scorer + `scout_leads`) | a `/recruit` command over the lead lifecycle | officers act on leads from Discord |

**Data spine — the parallel track** *(intake shipped; see §5c)*

| Component | Inputs | Build | Status |
| --- | --- | --- | --- |
| Companion uploader | `SAVEDVARS_UPLOAD_SECRET` | stdlib `scripts/upload_savedvars.py` + secret-verified `POST /api/upload/savedvars` on `web.py` | ✅ shipped |
| RCLootCouncil ingestion | (none — schema is stable) | `savedvars.py` parser + `services/rclootcouncil.py` → `loot_history` | ✅ shipped |
| GRM ingestion | tighten aliases against a live `Guild_Roster_Manager.lua` | `services/grm.py` → `identity_members` (defensive, shape-agnostic) | ✅ shipped, validate on real data |
| Identity graph | a reliable character↔Discord signal (lead candidate: Battle.net **user-OAuth** owned-characters profile — §9, data-sources §2a) | `identity.py` account grouping + `db.apply_accounts` alt↔main overlay; Discord link still manual | 🟡 account half done; Discord link remains |

---

## 8. Guardrails, decisions, and what's still needed

**What "a hit" looks like:** bots retired (PatchBot, the 3 news bots, the raw
Raider.IO alerts → one Calcifer voice); channels unmuted (M+/news become pages
people open); chores gone (no hand-editing the roster or info post); commands
actually used (discoverability via `/me`, helper cards); one coherent house, in
Calcifer's colours and voice.

**Design rules that keep him loved:** less, not more (every feature *reduces*
noise or *removes* a chore); opt-in, never opt-out (loud stuff is a subscription);
curation is the service (fewer, better, current surfaces); warm by default, sharp
only in the raid room; never punch down; visible wins first.

**Locked decisions (an interview resolved every open fork):** rebrand the
existing ftabot as Calcifer (one app/token); Hot 20 is the first board; Components
V2 for boards; managed host + persistent disk; the data spine is a parallel track;
GM-aware voice (needs `GM_DISCORD_ID`); management-only identity graph; voice
banks Claude-drafted, GM-curated via GitHub; all emoji tiers uploaded, resolved by
name; digests weekly fronted by the News Highlights board; recruitment intent from
WoWProgress + Raider.IO, vetted via WCL; persona tone by area (`light` default,
`full` in social, `off` in announcements).

**Values still needed (config, not decisions — drop them in whenever):** the GM's
numeric Discord user ID; the GM / Flamekeeper / management role IDs; the
Raid-Helper API key + server ID (Events board read access); target channel IDs per
board/feed (or set live via `/board` / `/feed`); the M+ score-percentile/login
thresholds; `RECRUIT_ROLES_OPEN` + ilvl floor; a sample RCLootCouncil + GRM `.lua`
so the parser maps to the real schema.

---

## 9. Open concerns & hardening backlog

Known rough edges, kept here (tied to the vision they protect) rather than lost
in a side note. None is urgent. Several entries have **shipped** and been
removed: the no-em-dash *one voice* pass (authored feed/cog titles cleaned +
`publisher.render_embed` now sanitizes the line Calcifer writes); the *deployment
hardening* (a non-root `USER` in the Dockerfile + capped runtime-dependency
majors); the *empty-fetch prune guard* (a 0-member roster fetch no longer wipes
the table); *case-insensitive boolean config*; the *constant-time admin token*
+ `http(s)`-only link allowlist; the *GM health delta* (the stored snapshot now
carries the retention component the live view uses); *date-portion attendance
ordering*; the *sync token cookie* (the `/app` unlock posts the token as JSON
into an http-only cookie, so it stays out of the URL); and the *real server
timezone* (raid/event times now resolve through `Config.raid_timezone` —
Europe/Paris as an IANA `zoneinfo` zone, DST-correct — instead of the static
`RAID_TZ_OFFSET_HOURS`).

Three items remain, roughly highest-value first:

- **The Blizzard connector skips on the first transient failure, and taps only
  two of the surfaces it documents.** `BlizzardClient._get`
  ([`services/blizzard.py`](../ftabot/services/blizzard.py)) retries once on a
  401 (stale token) but lets a 429 or 5xx raise straight to `sync.py`, which
  swallows it best-effort — so a momentary Blizzard blip silently blanks that
  run's *authoritative* rank/level/race/ilvl, where the Raider.IO path backs off
  on 429/5xx and chunks at `ENRICH_CONCURRENCY`. At ~300 members one sequential
  pass sits far under Blizzard's published per-client quota (~36k req/hour, 100
  req/second), so this is resilience, not throughput: give the Blizzard path the
  same bounded backoff (the quota is now recorded in
  [`data-sources.md`](data-sources.md) §2) so a transient failure degrades
  instead of dropping a column. The same connector leaves three *free,
  first-party* surfaces unwired, each lowering our reliance on third-party feeds:
  the Account/Protected **user-OAuth** profile (the §5c identity signal — now the
  leading candidate for the open character↔Discord link), guild
  **activity/achievements** (the Blizzard half of Phase 4's shoutouts), and the
  **WoW Token** index. All three are catalogued in `data-sources.md` §2a and
  threaded into the §7b rows; none needs a new deploy, token, or runtime
  dependency.
- The scout's land-odds baseline (no signal for guilded candidates yet)
- Raid-Helper webhook replay binding (static secret is acceptable for now)

> These are low-impact hardening items. When addressed, move to the roadmap or
> close the decision. Open questions live in [`DECISIONS.md`](DECISIONS.md).

---

## 10. Where the rest of the detail lives

This doc is the vision and the plan. The remaining detail lives in:

- [`data-sources.md`](data-sources.md) — what each external API exposes, how auth
  works, and the local-addon data spine (§5) — the reference behind `services/`.
- [`deploy.md`](deploy.md) — running and operating it from a phone, and the
  posting-guardrail workflow.
- [`../README.md`](../README.md) — features, commands, and quick start.
- [`../CLAUDE.md`](../CLAUDE.md) — the codebase architecture, for working on it.

The implementation itself is the source of truth for *how* things work
(`metrics.py` for the scoring formulas, `db.py` for the schema, `config.py` for
every setting); this doc is the source of truth for *what* we're building and
*why*.
