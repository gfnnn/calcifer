# ROADMAP — Feature Planning & Delivery Phases

> **This is the detailed feature roadmap extracted from [`vision.md`](vision.md).** For the strategic vision, see vision.md. For current progress, see the status badges below.

---

## Overview: Two-Phase Rhythm

The roadmap is organized in **phases 0–5**, with each phase independently shippable and landing a visible win. **Phases 0–1 shipped** (June 2026); Phases 2–5 represent the forward plan. The **data spine** (SavedVariables ingestion) runs as a parallel track.

---

## Phase Status at a Glance

| Phase | Focus | Status | ETA |
|-------|-------|--------|-----|
| **0** | Foundations: branding, board engine, posting guardrail, feed hub, recruit scout | ✅ Shipped | Complete |
| **1** | First board: Hot 20 (M+ leaderboard) + targeted score refresh | ✅ Shipped | Complete |
| **2** | Tidy: Events, News, Rules, Guides, Pulse boards + raid-night roster | 🟡 Partial (4/5) | TBD |
| **3** | Quiet: Digest mode, M+ gating, `/alerts` subscriptions, `/me` card | 📋 Planned | TBD |
| **4** | Charm: First-kill milestones, Raid Progress board, Weekly MVP, mood system | 📋 Planned | TBD |
| **5** | Recruitment: WoWProgress/RIO LFG feeds, recruitment funnel, recruitment board | 📋 Planned | TBD |
| **—** | **Data spine** (parallel) | 🟡 Partial (intake shipped, linking in progress) | TBD |

---

## The Delivery Contract — What "Done" Means

Every surface must clear this gate before deployment:

1. ✅ **A pure renderer with offline tests** — Logic as `(db, config) → payload`, unit-tested against temp-file SQLite with network mocked (e.g., `metrics.py`, `boards/hot20.py`).

2. ✅ **A self-enabling, default-off config flag** — One env flag turns feature on (following the `feeds_enabled` / `boards_enabled` pattern); a `Config` field, `.env.example` entry, and README row as one change.

3. ✅ **Routes through the posting guardrail** — Every byte for Discord resolves destination via `PostGate.resolve(...)`.

4. ✅ **Survives a restart** — All message IDs, dedup keys, baselines persist in DB on persistent disk (the `boards` / `seen_items` / `feed_outbox` pattern).

5. ✅ **Toggle → review → promote** — Validated in `POSTING_MODE=test`, reviewed, then `live` + `/board promote` (or rule moved off test channel for feeds).

6. ✅ **The docs move in step** — README rows updated, status badges flipped to ✅, any open concerns closed, backlog shrinks.

**Two cross-cutting prerequisites:**
- ✅ **Real server timezone** — Shipped. Time-anchored surfaces read wall-clock times through `Config.raid_timezone` (Europe/Paris as IANA zone).
- **Config values** — See "Inputs still needed" in §8 below.

---

## Phase 2 — Tidy the Rooms (Partial)

**Vision:** Replace scattered bots and rotting posts with tidy living boards.

| Feature | Status | Inputs needed | Build | Done when |
|---------|--------|---|---|---|
| **Weekly Events board** | ✅ Shipped | Raid-Helper webhook secret + server ID; events channel ID | Renderer over stored Raid-Helper events + `<t:…>` stamps | Week's events render at a glance (time, signups, comp gaps, jump-links); signups stay in Raid-Helper |
| **News Highlights board** | ✅ Shipped | Feed default channel; high/low tiering on ingest | Two-zone renderer over news cache + Wowhead tiering | 3 news bots retired, duplicates deduped |
| **Rules & Info board** | ✅ Shipped | Rules prose template; live tokens (member_count, active_%, progression) | Managed post renderer + drift nudge | Hand-written prose stays, live tokens refresh, drift is flagged |
| **Guides & Resources board** | ✅ Shipped | Curated link set (content in repo) | Board renderer (masked-link grid) + dead-invite watch | Raw `discord.gg` wall becomes button grid |
| **Raid-night roster post** | 📋 Planned | WoW Audit team key; Wednesday/Sunday schedule | Board over WoW Audit selected roster, auto-rotating | Twice-weekly hand-edit is gone; Team Chemistry sees live roster |

**What's needed for full Phase 2:** Deploy the raid-night roster board (depends on WoW Audit integration).

---

## Phase 3 — Quiet the Firehoses (Planned)

**Vision:** Turn alert overload into curated, opt-in subscriptions and weekly digests.

| Feature | Inputs | Build | Done when |
|---------|--------|-------|-----------|
| **Digest mode** | Weekly flush schedule (Paris time) | `mode`/`digest_window` in routing rules + `feed_digest_queue` table + digest renderer | Any rule is realtime *or* batched; batched posts on schedule |
| **M+ gating & log lane** | Notable-key thresholds (timed high keys); percentile/milestone levels | Gate notable keys to realtime, roll rest into weekly digest; a low-key log lane | Only notable keys ping; chill runs counted in digest + Hot 20 |
| **`/alerts` subscriptions** | Opt-in role IDs (native Channels & Roles or slash toggle) | `/alerts` command (toggle → role add/remove) | Loud pings are opt-in; only subscribers are notified |
| **Per-rule tone/cadence** | (none) | `tone`/`cadence` in routing rules, composed with global `PERSONA_TONE` | Serious channels quiet, `#banter` full Calcifer |
| **`/me` personal card** | (none — existing data) | `/me` command rendering caller's characters (ilvl, M+, vault, wishlist) | Member pulls their own card |

---

## Phase 4 — Celebration & Charm (Planned)

**Vision:** Make victories visible and Calcifer's personality shine.

| Feature | Inputs | Build | Done when |
|---------|--------|-------|-----------|
| **First-kill milestones** | WCL prog creds; stored "already killed" set | Detect new boss kill in prog feed; celebratory render (`EMBER_RED`) | First kill fires exactly once, idempotent across restarts |
| **Raid Progress board** | WCL creds; prog channel ID | Board rendering N/H/M kills + best pull on current boss | Current progression renders ambiently (edits in place) |
| **Weekly MVP / Ashes Awards** | Ranking metric; weekly schedule; winners history | Weekly award renderer with anti-repeat memory | Weekly MVP posts without repeating trivially |
| **Achievement shoutouts** | Achievement source (Blizzard/WCL) | Detect + render new achievements | Shoutout fires on notable achievement |
| **Concierge helper cards** | Per-channel CTA copy (content in repo) | Board (one sticky per channel) | Each channel has a "here's what you can do" card |
| **Charm pass** | `:calcifer_*:` / `:fta_*:` emoji uploaded; expanded line banks | Grow `brand.py` banks + mood system + anti-repeat memory | 6–10 lines per mood, anti-repeat, em-dash rule enforced by tests |

---

## Phase 5 — Recruitment Funnel (Planned)

**Vision:** Turn cold outreach into a warm, tracked funnel.

| Feature | Inputs | Build | Done when |
|---------|--------|-------|-----------|
| **LFG intent feeds** | WoWProgress + Raider.IO recruitment endpoints | Two pull `FeedSource`s for recruitment intent (names vetted by scorer); `scout_leads` lifecycle table | Raised hands ingested, vetted, cached |
| **Recruitment board** | `RECRUIT_ROLES_OPEN` + ilvl floor (config); mgmt-only channel | Board: open slots × fresh, un-contacted leads (ranked above cold); officer slash commands mark state | Listings dump becomes one curated board |
| **`/recruit` commands** | (scorer + `scout_leads`) | `/recruit` command over lead lifecycle | Officers act on leads from Discord |

---

## Data Spine — Parallel Track (Partially Shipped)

**Vision:** Link every character → account → Discord user, so "who is this person?" is answerable.

| Component | Status | Build | Done when |
|-----------|--------|-------|-----------|
| **SavedVariables parser** | ✅ Shipped | Dependency-free Lua reader | Parses WoW addon dumps (`savings.lua`) |
| **RCLootCouncil ingestion** | ✅ Shipped | Parser → `loot_history` table | Loot records are permanent, analyzable |
| **GRM identity ingestion** | ✅ Shipped | Parser → `identity_members` table | Alt↔main grouping from GRM ground truth |
| **Account graph** | 🟡 In progress | `identity.py` account grouping + `db.apply_accounts` overlay | Alt↔main mappings overlay onto `members` table |
| **Character↔Discord linking** | 📋 Planned | Manual `/link` command + GRM hints | Every character is linked to a Discord user |

---

## Config Values Still Needed

These inputs block feature rollout. Check them against your `.env.example` and `Config`:

**Required for boards:**
- Raid-Helper API key + server ID (Weekly Events)
- WoW Audit API key (Raid-night roster)
- WCL API credentials (Raid Progress, prog alerts)
- Blizzard API credentials (achievements, real-time roster)

**Required for routing:**
- Feed default channel ID (catch-all for unrouted alerts)
- Per-board channel IDs (each board pinned in its home channel)
- Officer-only channel IDs (recruitment intel, board drafts, admin log)

**Required for gating:**
- GM Discord ID (Harry-specific register, planned)
- Flamekeeper role IDs (management tiers, planned)
- Raid-Helper webhook secret (event push verification)

**Optional tuning:**
- M+ notable-key thresholds (Phase 3)
- Weekly schedule (cron-like for digest flush, weekly MVP, etc.)
- Recruitment open roles + ilvl minimums (Phase 5)

---

## Open Decisions (See DECISIONS.md)

Several design questions remain open and tracked in [`DECISIONS.md`](DECISIONS.md):
- Prog-team gating: channel-based or code-level?
- Should `/gmreport` and `/raidcheck` stay public?
- Does Harry's register ship now or later?
- Single notice-board channel or split?
- Canonical channel layout?

See DECISIONS.md for details and recommendations.

---

## Dependency Chain (What Blocks What)

```
Server timezone (✅ shipped)
    ↓
Phase 0: Foundations (✅ shipped)
    ↓
Phase 1: Hot 20 (✅ shipped)
    ├─ Phase 2: Tidy (partial)
    │  ├─ Events board (✅)
    │  ├─ News board (✅)
    │  ├─ Rules/Guides/Pulse boards (✅)
    │  └─ Raid-night roster (WoW Audit key needed)
    ├─ Phase 3: Quiet (digest mode, M+ gating, /alerts)
    ├─ Phase 4: Charm (milestones, prog board, mood system)
    └─ Phase 5: Recruitment (LFG feeds, funnel, /recruit)
    
Data spine (parallel): SavedVariables → loot + identity (partial)
```

---

## How to Use This Doc

- **Planning a feature?** Check its row in the phase table; see what inputs it needs and what the done condition is.
- **Blocked on config?** Check "Config Values Still Needed" and add them to `.env.example`.
- **Deciding between approaches?** See DECISIONS.md for open questions + recommendations.
- **Shipping a phase?** Check the delivery contract (§2) and mark the status badge as complete.

---

## Where the Rest Lives

- **Vision & strategy:** [`vision.md`](vision.md)
- **Open questions & decisions:** [`DECISIONS.md`](DECISIONS.md)
- **Communication model:** [`communication-model.md`](communication-model.md)
- **Sticky post specs:** [`sticky-posts-spec.md`](sticky-posts-spec.md)
- **Channel layout:** [`channel-architecture.md`](channel-architecture.md)
