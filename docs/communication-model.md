# Calcifer's Communication Model — who hears what, and how

> This is the **central alignment doc** for how Calcifer communicates. It maps
> every surface the bot speaks through onto two axes: **who the audience is** and
> **what kind of message it is**. Read this before adding any new post, command,
> board, or alert. If a new feature doesn't fit cleanly into a cell of the matrix
> in §4, that's a signal to stop and design it properly first.
>
> Pairs with [`vision.md`](vision.md) (the why), [`MESSAGING.md`](../MESSAGING.md)
> (the voice banks), and [`communication.md`](communication.md) (the Discord
> formatting toolbox). This doc is the **bridge**: it says *which* tool, in *which*
> voice, to *which* audience, for every feature.

---

## 1. The philosophy in one line

**Calcifer is one butler speaking warmly down a hierarchy.** He never shouts, he
tidies instead of piling up, and he answers when asked rather than nagging. Every
message is shaped by two questions:

1. **Who is this for?** (the audience) — a visitor, the guild, the prog team, the
   officers, or Harry himself.
2. **Is this a page or a knock?** (the mode) — does it *sit there to be read* (a
   living board), *tap you on the shoulder* (an alert), or *answer a question you
   asked* (a command reply)?

Get those two right and the voice, the channel, the colour, and the ping policy
all follow.

---

## 2. The two axes

Everything the bot says lands at the intersection of an **audience** and a
**message type**. The rest of this doc is that grid.

```
                 MESSAGE TYPE  ──────────────────────────────────────────►
   AUDIENCE      │ Living board │ Feed alert  │ Digest    │ Command reply │ Mirror
   │             │ (ambient)    │ (push)      │ (roundup) │ (pull)        │ (push)
   ▼             │              │             │           │               │
   Visitor       │   ✓ public   │     —       │    —      │      —        │   —
   Guild (300)   │   ✓ public   │  ✓ opt-in   │  ✓ weekly │   ✓ public    │ ✓ events
   Prog team     │  ✓ team page │  ✓ prog     │    —      │  ✓ team view  │   —
   Officers      │  ✓ sandbox   │  ✓ intel    │    —      │  ✓ mgmt cmds  │   —
   Harry (GM)    │     all      │    all      │   all     │  ✓ + register │  all
```

The cells are filled out concretely in **§4 (the feature map)**.

---

## 3. The five audiences

The access ladder from [vision §5d](vision.md): **Visitor → Guild → Prog team →
Officers/Flamekeepers → GM (Harry)**. Each is a wider grant of trust, and each
gets a different register (vision §2).

### 3a. Visitor (non-member, in the server to look)
- **Sees:** only the read-only public boards — Rules & Info, Guides, News, and the
  recruitment board (this is their front door).
- **Register:** sardonic, warm landlord. Calcifer doesn't know them yet.
- **Goal:** make the server legible and inviting enough that they want in.
- **Channels:** read-only "notice board" channels (deny Send Messages for
  `@everyone`).

### 3b. Guild (all ~300 members)
- **Sees:** everything Visitor sees, plus the community pages (Hot 20, Weekly
  Events, Roster Pulse), feed alerts they opt into, and the public commands
  (`/member`, `/roster`, `/me`, `/loot`, etc.).
- **Register:** the default Calcifer — dry, warm, keeps them warm whether they
  thank him or not.
- **Goal:** the community hub feeling — muted channels become pages people open.

### 3c. Prog team (Team Chemistry — the CE raid team)
- **Defined by:** the `raid_team` member field.
- **Sees:** guild surfaces, plus *more detail* on progression — WCL prog alerts,
  pull-by-pull intel, team-filtered readiness, rival-guild realm progress.
- **Register:** the competitive edge is allowed here — sharp about the raid, in
  team-only channels. Never sharp about a *person*.
- **Goal:** the war room. Detail the wider guild neither needs nor wants.
- **STATUS:** `raid_team` is stored and used for *ranking/filtering*, but there is
  **no channel gating by team yet** (see §6). This tier is plumbed, not enforced.

### 3d. Officers / Flamekeepers (management)
- **Defined by:** the `admin_role_name` role (default "Officer") or Manage-Server
  permission.
- **Sees:** the management tools — recruitment, the identity graph, sync, all the
  `/board` `/feed` `/scout` admin commands, and sandboxed boards before promotion.
- **Register:** a notch more cooperative; the castle's lieutenants who keep the
  flame.
- **Goal:** run the guild without doing housework by hand.

### 3e. Harry (the GM)
- **Defined by:** `GM_DISCORD_ID`.
- **Sees:** everything.
- **Register:** familiar, contract-bound. "*Another job, Harry? The things I do for
  this contract.*"
- **STATUS:** `GM_DISCORD_ID` exists in config but is **not yet used** to switch
  register (see §6). Planned.

---

## 4. The five message types (and the full feature map)

Every post the bot makes is one of these five modes. This is the heart of the
model: each mode has a *fixed contract* (push vs pull, pings or not, who it's for).

### Type 1 — Living board *(ambient page, pull-by-looking)*
A bot-owned message pinned in a channel that renders from the DB and edits itself
in place. **Never pings. Never re-posts.** You read it when you choose to look.

| Board | Audience | Channel kind | Voice context |
| --- | --- | --- | --- |
| **Rules & Info** | Visitor + Guild | read-only notice | neutral (house rules) |
| **Guides & Resources** | Visitor + Guild | read-only notice | neutral (reference) |
| **News Highlights** | Visitor + Guild | read-only notice | neutral (relayed) |
| **Hot 20 (M+)** | Guild | community | `mplus` (fast) |
| **Weekly Events** | Guild | community | neutral |
| **Roster Pulse** | Guild | community | measured |
| **Recruitment** *(planned)* | Visitor + Guild front door; **officer-controlled** | read-only notice | `recruiting` |
| **Prog roster / team board** *(planned)* | Prog team | team-only | measured / competitive |

> Boards are the backbone of Pillar A. The rule of thumb: **if information goes
> stale or piles up as spam today, it should be a board.**

### Type 2 — Feed alert *(push, event-driven, opt-in pings)*
An external event happened; Calcifer taps the relevant audience. Routed by
`feed_rules`; pings are **opt-in only** (`/alerts subscribe`).

| Feed | Natural audience | Severity | Notes |
| --- | --- | --- | --- |
| **Wowhead news / blue post / patch** | Guild | info → red | relayed verbatim |
| **Raider.IO M+ timed key** | Guild | info | celebrates a run |
| **Raider.IO weekly affixes** | Guild | info | once per rotation |
| **WoW Audit raid reminder** | Guild | info → warning if thin | signup nudge |
| **WCL boss kill** | Prog team (+ guild for big ones) | warning | "💀 {boss} down" |
| **WCL best pull / prog** | **Prog team** | warning | pull-by-pull, war-room detail |
| **Raider.IO our progression** | Prog team / officers | warning | new guild kill |
| **Raider.IO rival progression** | Officers (intel) | info | realm rivals |

> The **same feed can serve different audiences at different detail levels** — this
> is the key lever for prog-team-vs-guild. A guild-wide channel gets "💀 Mythic boss
> down, grats!"; the prog channel gets the pull count, the best %, the wipe log.
> Same source, two rules, two channels, two detail levels.

### Type 3 — Digest *(buffered roundup, low frequency)*
A rule with `{"mode": "digest"}` buffers events into `feed_digest_queue` and flushes
as one roundup (weekly by default). Turns a firehose into a newsletter.

| Digest | Audience | Cadence |
| --- | --- | --- |
| **Weekly news roundup** | Guild | weekly |
| *(any feed can be digested instead of realtime)* | per rule | per rule |

> Digest is the antidote to alert fatigue: anything that doesn't need to interrupt
> becomes a once-a-week sweep. "*A week's worth of embers, swept up neatly.*"

### Type 4 — Command reply *(pull, request/response)*
Someone asked; Calcifer answers. **Ephemeral** (private to the asker) or **public**
to the channel. This is all of Pillar B plus the public lookups.

| Command set | Audience | Visibility | Pillar |
| --- | --- | --- | --- |
| `/member` `/roster` `/search` `/alts` `/whois` `/stats` | Guild | public embed | A |
| `/me` | Guild (self) | ephemeral | A |
| `/loot` `/wishlist` `/lootpriority` `/trends` | Guild | public embed | A/B |
| `/gmreport` `/raidcheck` | Guild *(today)* → should be officer/prog | public embed | B |
| `/refreshme` | Officers (self-serve) | ephemeral | B |
| `/scout` | Officers | public embeds | B |
| `/alerts subscribe/unsubscribe/list` | Guild | ephemeral | A |
| `/alerts subscribers` | Officers | ephemeral | B |
| `/sync` `/link` `/setmain` `/note` `/setteam` `/setofficer` | Officers | mostly ephemeral | B |
| `/board *` `/feed *` | Officers | ephemeral | B |
| `/recruit *` *(planned)* | Officers | ephemeral | B |

### Type 5 — Mirror *(external push relayed in)*
A webhook fires and Calcifer mirrors it. Today this is **Raid-Helper** (event
created/updated/deleted → updates the `calendar_events` table → feeds the Weekly
Events board, and optionally posts an alert).

| Mirror | Audience | Surfaces it feeds |
| --- | --- | --- |
| **Raid-Helper webhook** | Guild | Weekly Events board + optional event alert |

---

## 5. The control plane (who governs each mode)

A crucial point the user raised: **most of this should be officer-configurable from
inside Discord, not hard-coded.** Here's who holds the dial for each mode today:

| Mode | Configured by | How |
| --- | --- | --- |
| **Boards** | Officers | `/board create/promote/demote/refresh/remove` |
| **Feed alerts** | Officers | `/feed route/mute/disable/enable/test` |
| **Digests** | Officers | a `/feed route` rule with digest style |
| **Pings** | Members (opt-in) | `/alerts subscribe` |
| **The master switch** | Deploy / officers | `POSTING_MODE` (off / test / live) + per-board promote |
| **Recruitment content** *(planned)* | Officers | `/recruit slots/rule/message/...` |

> The **posting guardrail** (`PostGate`) sits under all of it: nothing reaches a
> real channel until a human promoted it. Off → test → promote. This is the safety
> rail that lets officers configure freely without fear of a misfire to 300 people.

---

## 6. The gap between vision and reality (read this before building)

The exploration surfaced one structural truth that shapes everything the user wants
to build next:

**Today the access model is effectively TWO tiers: _officer_ vs _everyone_.** The
five-tier ladder is *plumbed but not enforced*:

- ✅ **Officer vs everyone** — fully enforced (`_guards.py`).
- ⚠️ **Prog team** — `raid_team` is *stored* and used for ranking/filtering, but
  **no channel or surface is gated by it**. "Prog team sees more detail" is not yet
  real; it needs either (a) team-only channels that feed rules route to, or (b)
  team-filtered command views.
- ⚠️ **GM (Harry)** — `GM_DISCORD_ID` is *stored* but **never checked**; Calcifer
  speaks to Harry in the same voice as everyone.
- ⚠️ **Visitor** — there's no explicit "visitor" gate; it's just "whatever a
  read-only public channel shows." Fine as-is, but worth naming.

**Why this matters for the roadmap:** two of the things the user explicitly wants —
*recruitment as an officer-controlled board* and *prog-team-detail-vs-guild-detail*
— sit right on top of these unenforced tiers. The good news: **neither needs the
tiers fully built.**

- **Recruitment** works with the *existing* officer tier: officers control it via
  commands, the board itself is public-read. No new tier needed. ✅ buildable now.
- **Prog-team detail** can be achieved *today* with **channel placement + feed
  rules**, not code-level gating: put a prog channel that only the team can read,
  route the detailed WCL prog rule there, route the lightweight "boss down, grats"
  to the guild channel. The `raid_team` field already prioritises *who* the feeds
  poll. So this is **mostly a routing/channel-permissions exercise, not a code
  one** — which is exactly the kind of thing `/feed route` already does.

So the model degrades gracefully: **Discord channel permissions + feed routing do
the audience-gating that the code tiers don't yet enforce.** That's the pragmatic
path, and it's worth deciding deliberately: *do we gate by code, or gate by channel?*

---

## 7. What each audience actually experiences (the walkthroughs)

A sanity check — read the model from each person's seat.

**A visitor** lands in the server, sees a tidy `#rules`, `#guides`, `#news`, and a
`#recruitment` board telling them exactly what trials are open and how to apply.
Clean, legible, inviting. No spam, no noise.

**A guild member** opens `#m-plus` and sees the Hot 20 living leaderboard; opens
`#events` and sees the week laid out with their-local-time stamps; gets a ping only
for the one feed they subscribed to; asks `/loot` and gets a private answer. The
community runs itself.

**A prog raider** has all that, plus a `#prog` channel where Calcifer posts
pull-by-pull WCL detail and rival-guild movement — the war room — that the wider
guild never sees.

**An officer** runs `/scout` to find a recruit, `/setteam` to slot them, `/recruit
slots dps 2` to open a trial, `/board promote` to push a board live — all from
Discord, all ephemeral, all gated.

**Harry** does all of the above, and (once the register lands) gets a Calcifer who
talks to him like the bound demon he is.

---

## 8. The alignment principles (the rules that keep it coherent)

1. **One voice, five registers.** Same Calcifer; warmth scales with the tier. Snark
   targets situations, never people. Never an em dash.
2. **Page or knock, never nag.** Ambient info → board (pull). Time-sensitive event →
   alert (push, opt-in ping). Nothing interrupts that doesn't have to.
3. **Detail scales with audience.** The same event can be a one-liner to the guild
   and a war-room readout to the prog team. Route by channel, not by shouting.
4. **Officers hold the dials, in Discord.** Configuration is a slash command, not an
   env var or a code change, wherever it reasonably can be.
5. **Deny by default.** Nothing reaches a real channel until a human promoted it.
   Off → test → live.
6. **Gate by the cheapest mechanism that works.** Channel permissions + feed routing
   first; code-level tiers only when the cheap path can't express it.
7. **Pull for management, push for community — sparingly.** Pillar B is
   answer-when-asked. Pillar A is ambient pages plus a thin, opt-in stream.

---

## 9. Open questions to resolve next (the fleshing-out backlog)

These are the decisions that will sharpen the model. Worth working through
deliberately:

1. **Prog-team gating: channel or code?** Do we lean on a read-restricted `#prog`
   channel + feed routing (cheap, available now), or build real `raid_team`-aware
   command gating (more work, more precise)? *Recommendation: channel + routing
   first.*
2. **Should `/gmreport` and `/raidcheck` stay public?** Today they're open to
   everyone (Type-4, public embed). They're Pillar-B management views — arguably
   they should be officer/prog-gated or at least ephemeral. *Flag for decision.*
3. **Does Harry's register ship now or later?** It's a small change (check
   `GM_DISCORD_ID`, swap a bank) but it's pure charm, not function. *Defer until the
   functional tiers feel right.*
4. **One notice-board channel or several?** Rules + Guides + News + Recruitment in
   one `#info` channel, or split? (See recruitment discussion — leaning a dedicated
   `#recruitment` for the front-door URL.)
5. **What's the canonical channel layout?** This model implies a channel map:
   read-only notice channels, community channels, a prog channel, an officer
   channel. Worth drawing it explicitly and mapping each board/feed to its home.

---

## 10. Where this leaves recruitment + prog (tying back)

- **Recruitment board** = a Type-1 living board, Visitor+Guild audience, **officer
  control plane** via a new `/recruit *` command group. Needs no new access tier —
  rides the existing officer gate. **Green-lit to build.**
- **Prog-team detail** = a routing decision, not a feature: a read-restricted prog
  channel + a `/feed route` rule sending the detailed WCL prog feed there, while the
  guild channel gets the light version. **Achievable today**; the only open question
  is whether we *also* want a Type-1 prog roster board (team page) later.

The next concrete build step is still the **recruitment board + `/recruit`
commands** (designed in [`board-messaging-spec.md`](board-messaging-spec.md) §7 and
the prior discussion). This doc is the frame that confirms it sits in the right
place.
