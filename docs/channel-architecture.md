# The Guild's Channel Architecture — Calcifer's home

This document defines the **canonical Discord channel layout** and where each of
Calcifer's surfaces lives. It's the blueprint that every board, feed rule, and new
feature anchors to.

---

## Design principles

1. **Read-only notice channels** — Information architecture: a `@everyone` deny on
   Send Messages. Pages, not dumps.
2. **Public community channels** — Ambient walls: updates, events, leaderboards.
   Members can chat *around* the boards.
3. **Team channels** — Prog-team-only (if it exists) and officer channels. Higher
   detail, team-only context.
4. **One voice, tidy channels** — Calcifer posts in multiple places, but each
   channel feels designed, not cluttered.

---

## The canonical layout (categories and channels)

### **NOTICE BOARD** *(read-only; `@everyone` deny Send Messages)*
The front door. Visitors and members see this first. Sticky, polished, inviting.

- **`#rules-and-info`** — House rules, raid times, loot rules, attendance expectations
  - 📌 Board: Rules & Info (static, GM-curated)
- **`#guides-and-resources`** — Guides, links, setups, macros, guides for every class/role
  - 📌 Board: Guides & Resources (static, GM-curated)
- **`#news`** — WoW news, patch notes, blue posts
  - 📌 Board: News Highlights (live, Wowhead feed)
- **`#recruitment`** — Open trials, what we look for, how to apply
  - 📌 Board: Recruitment (live, officer-configured)

> Together: the guild's front door. A visitor reads these four, knows what you
> stand for, and either wants in or doesn't.

---

### **COMMUNITY HUB** *(public, anyone can chat)*
The daily pulse. Where the guild converges.

- **`#mythic-plus`** — M+ keys, runs, sign-ups
  - 📌 Board: Hot 20 (live, M+ leaderboard)
  - 📡 Feeds: Raider.IO M+ keys (routed here, opt-in ping via `/alerts`)
- **`#events`** — Weekly raid times, socials, calendar
  - 📌 Board: Weekly Events (live, Raid-Helper calendar)
  - 📡 Feeds: Raid-Helper events (webhook-sourced)
  - 📡 Feeds: WoW Audit raid reminders (optional, warning if thin)
- **`#guild-pulse`** — Health snapshot, roster stats, trends
  - 📌 Board: Roster Pulse (live, weekly health)
- **`#general`** — Free chat, hang out (not board-specific)

> Together: ambient information + community. Members check what's happening,
> join events, see the leaderboard. No shouting, just available.

---

### **INTEGRATION HUB** *(optional; for feeds that don't fit elsewhere)*
For feeds that don't need their own channel — a "everything else" bucket.

- **`#calcifer-alerts`** *(or name per guild)*
  - 📡 Feeds with no specific home: realm-wide news, macro changes, external guild
    callouts

> Used only if a feed source doesn't fit into one of the main channels. Keeps
> clutter centralized instead of scattered.

---

### **PROGRESSION TEAM** *(read-restricted; @team-chemistry role+)*
The war room. Team Chemistry (prog team) only.

- **`#progression`** — Raid logs, pull analysis, strategy
  - 📡 Feeds: **WCL prog alerts** (pull-by-pull, detailed)
    - *Guild `#events` sees*: "💀 Mythic Broodkeeper down, grats!"
    - *Prog sees*: "🔥 Mythic Broodkeeper · 8 pulls · best {X}% · [log link]"
  - 📡 Feeds: **WCL best-pull** (real-time progress tracking)
  - 📡 Feeds: **Raider.IO our progression** (new kills, ranked)
- **`#team-roster`** *(planned)*
  - 📌 Board: Team Chemistry roster (if a team-specific roster page makes sense)

> The thin details the wider guild doesn't need. Rival guild progress? Minute-by-minute
> pull count? Belongs here. The guild sees headlines.

---

### **OFFICER HUB** *(read-restricted; officer role+)*
The back office. Officers only.

- **`#recruitment-intel`** — Scouted candidates, open slots, applicants
  - Command: `/scout` replies (deferred, ephemeral or here)
  - Command: `/recruit` status and previews
  - ⚠️ No board yet (recruitment board is public in notice); this is the officer's
    scratch space
- **`#guild-admin`** — Sync results, changes, operational notes
  - Command: `/sync` output (or ephemeral equivalent)
  - Command: `/link` `/setmain` `/note` `/setteam` confirmations
  - ⚠️ Mostly for async handoff when officers batch work
- **`#board-drafts`** *(optional but recommended)*
  - 📌 Drafts of boards in `POSTING_MODE=test` before promotion to live
  - Allows officers to review rendering before the board hits production

> Officers have command replies private (ephemeral) by default; any that justify
> a channel post go here for async audit.

---

## The current state (if you want to retrofit)

Today From the Ashes *probably* has:
- `#general` / `#raids` / `#mythic-plus` (community channels)
- Maybe a `#news` or a broadcast channel
- Maybe an `#officers` channel
- Maybe a `#recruitment` or application channel

The map above is aspirational; retrofit as follows:

- **Migrate** existing boards into the notice-board channels and designate them
  read-only (via Discord perms)
- **Gather** all the boards into one category labeled "NOTICE BOARD"
- **Designate** a read-restricted channel for team-Chemistry (prog team) — route
  the detailed WCL prog alerts there
- **Designate** an officer-only channel for recruitment intel and admin work
- **Set up Raid-Helper** webhook → `#events` board (if not already)

The boards and feeds themselves are the same; it's the **channel home** that
clarifies purpose.

---

## Feeds routing map (the `/feed route` cheat sheet)

Once the layout is set, every feed rule is a one-liner. Here's the routing:

```
/feed route wowhead.news          #news       (Wowhead news)
/feed route wowhead.patch-notes   #news       (Patch notes → news)
/feed route wowhead.blue-post     #news       (Blue posts → news)

/feed route raiderio.mplus-run    #mythic-plus tone=mplus (timed keys)
/feed route raiderio.affixes      #guild-pulse            (weekly affixes)

/feed route raidhelper.created    #events                 (Raid-Helper events → board)
/feed route wowaudit.raid-reminder #events     tone=warning (signup reminder)

/feed route wcl.kill              #events     severity=warning (guild: light version)
/feed route wcl.kill              #progression severity=warning (prog: detailed version)
/feed route wcl.best-pull         #progression severity=warning (prog only)

/feed route raiderio.our-progression    #progression severity=warning (team sees new kills)
/feed route raiderio.realm-progression  #guild-admin              (officer intel only)
```

The **same feed** has two routes: the guild channel gets the headline, the prog
channel (if it exists) gets the full readout. This is the lever that lets detail
scale by audience.

---

## Board parking assignments

Which board lives where:

| Board | Category | Channel | Audience |
| --- | --- | --- | --- |
| **Rules & Info** | Notice | `#rules-and-info` | Visitor + Guild |
| **Guides** | Notice | `#guides-and-resources` | Visitor + Guild |
| **News** | Notice | `#news` | Visitor + Guild |
| **Recruitment** | Notice | `#recruitment` | Visitor + Guild (public front) + Officers (control) |
| **Hot 20** | Community | `#mythic-plus` | Guild |
| **Weekly Events** | Community | `#events` | Guild |
| **Roster Pulse** | Community | `#guild-pulse` | Guild |
| **Team Roster** *(planned)* | Progression | `#team-roster` | Prog team |
| *(Board drafts)* | Officer | `#board-drafts` | Officers (testing only) |

---

## Ping policy per channel

All boards: **never ping** (forced `allowed_mentions=none`).

Feed alerts: **opt-in pings** via `/alerts subscribe`:
- `#mythic-plus`: optional role ping for M+ enthusiasts
- `#events`: optional role ping for event signups
- `#progression`: optional role ping for prog team (via `@team-chemistry`)
- `#guild-admin`: optional role ping for officers

---

## Messages this enables

Once the channels and feeds are routed this way, every audience self-organizes:

**A visitor** opens Discord, sees four tidy notice boards, knows what to do.

**A guild member** checks `#mythic-plus` for the leaderboard, `#events` for the
week's raids, maybe pings in. Gets opt-in alerts only. Feels tidy, not spammy.

**A prog raider** has all that, plus a `#progression` channel where the detailed
WCL logs and rival-guild movement live. The context is crisp: this channel is for
the war, the other is for the hearth.

**An officer** uses the notice boards to set policy (Rules, Guides, Recruitment),
reads `#recruitment-intel` to track applicants, checks `#board-drafts` for upcoming
changes.

---

## One more thing: the footer feel

Because Calcifer stamps every board with a signoff and a mood, the channels feel
like he runs them:

- **Notice boards**: professional warmth ("Kept warm by Calcifer")
- **Community hubs**: ambient ("The fire's roaring tonight" for M+, "Patient work"
  for raid)
- **Prog**: competitive ("The logs don't lie")
- **Officer**: terse ("Calcifer, keeping the hearth lit")

The *same butler* keeping all these fires, different rooms.

---

## The open question: does your guild exist at this scale?

This architecture assumes ~300 members, 10-20 active raiders, a single CE prog
team. If your structure is different:
- **Smaller guild** (20-50): collapse Notice + Community into one category
- **Multiple prog teams**: one `#progression-tc` for Team Chemistry, one
  `#progression-casual` for a second team (with different routing)
- **Larger guild** (600+): add a substructure (by region, language, playstyle)

The **principle** stays the same: notice channels (read-only), community (ambient),
team (detail), officers (scratch). The *shape* adapts.

