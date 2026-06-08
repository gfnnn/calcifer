# Sticky Posts Design System — Calcifer's Living Dashboards

> This document defines Calcifer's **permanent sticky posts** (the boards) as a unified, handcrafted visual and voice system. It **separates sticky posts from command replies entirely** — commands are pull-based utilities; sticky posts are ambient, always-on, branded pages.
>
> **This is the design spec for the first type of post you want to polish:** the permanent dashboards that sit in channels and dynamically update based on data changes or commands.

---

## Part 1: What a Sticky Post Is

A **sticky post** is:
- ✅ **Permanent** — lives in a channel, pinned or clearly visible
- ✅ **Self-updating** — edits itself in place when the underlying data changes (never re-posts)
- ✅ **Ambient** — you read it by looking, not by asking (pull, not push)
- ✅ **Branded** — looks, feels, sounds like Calcifer speaking
- ✅ **Never pings** — even if content changes dramatically, no notifications

**Not a sticky post:**
- ❌ Command responses (`/member name` → card for that one member)
- ❌ Feed alerts (event happened; we're telling you)
- ❌ Ephemeral confirmations (`/board promote` → "done" that disappears)

---

## Part 2: The Sticky Post Design Contract

Every sticky post follows the same **visual and voice grammar**:

### **A. Structure**

```
[HEADER]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[CONTENT SECTIONS]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[CONTEXT LINE (optional)]
[SIGNOFF]
[TIMESTAMP]
```

**In Discord embed terms:**
- **Title**: Header icon + title + optional subtitle
- **Description**: Content (sections separated by newlines)
- **Fields**: (optional, for complex layouts like Pulse)
- **Footer**: Signoff (always present)
- **Timestamp**: When the board last refreshed (Discord-rendered, per-timezone)
- **Color**: Per-board meaning (almost always `GUILD_EMBER` except News which is `BLUE_FLAME`)

### **B. Voice Grammar**

Every sticky post has:
1. **Header tone** — Sets the vibe. "Hot and heavy" vs. "Measured" vs. "Inviting"
2. **Optional flavor line** — A one-liner from a voice bank, tone-gated
3. **Content** — Just the facts, clearly formatted
4. **Signoff** — Always includes `board_signoff` line (e.g., "Tended by Calcifer. Mind the sparks.")
5. **Context line** (when useful) — A clarification or call-to-action (e.g., "Sign-ups live in Raid-Helper")

### **C. Visual Grammar**

- **Icons**: Consistent per-category (🔥 M+, 📅 events, 📰 news, etc.)
- **Text styling**: Bold for names, inline code for numbers, markdown for links
- **Separators**: Newlines between sections; emoji dividers where scanability matters
- **Color encoding**: `GUILD_EMBER` for our data, `BLUE_FLAME` for relayed external
- **Mood**: Optional emoji in title or footer to express Calcifer's feeling (✅ for success, ⚠️ for warning, etc.)

### **D. Frequency & Triggers**

- **Ambient refresh** — Updates on a timer (hourly, daily) regardless of activity
- **Event-driven** — Also updates when the underlying data changes (new event added, member scores update, etc.)
- **Fingerprint** — Only edits if the *material* content changed; ignores timestamp drift

---

## Part 3: Sticky Post Inventory — Current & Planned

| # | Name | Audience | Brand | Status | Next |
|---|------|----------|-------|--------|------|
| 1 | **Hot 20** | Guild | Fast, excited | ✅ Live | Polish: add mood |
| 2 | **Weekly Events** | Guild | Warm, coordinating | ✅ Live | Polish: warning on thin signups |
| 3 | **News Highlights** | Guild | Relayed, curated | ✅ Live | Polish: visual hierarchy |
| 4 | **Guides & Resources** | Guild | Reference, warm | ✅ Live | Polish: headers, emoji per link |
| 5 | **Rules & Info** | Guild + Visitor | House values, live | ✅ Live | Warm up intro, section callouts |
| 6 | **Roster Pulse** | Guild | Health check | ✅ Live | Add sparkline, flavor callouts |
| 7 | **Recruitment** | Guild + Visitor | Direct, inviting | 🚧 Planned | Build renderer, detailed spec below |
| 8 | **Roster** | Officers + Raid Leads | Reference | 🚧 Planned | Design pagination, access gates |
| 9 | **Announcements** | Situational | Situational tone | 🚧 Planned | Design `/announce` command system |

---

## Part 4: The Handcrafted Board Specifications

**These are delivered one by one below, with exact copy, visual markup, voice patterns, and update triggers. Each is designed to feel like Calcifer is actively running that corner of the server.**

### **BOARD 1: HOT 20 — Calcifer's Mythic+ Leaderboard**

**Audience:** Guild members, leaderboard enthusiasts, and the competitive  
**Purpose:** Answer "Who's pushing keys?" at a glance. Celebrate climbers. Build friendly rivalry.  
**Update trigger:** Daily baseline roll + whenever a member's score changes

---

#### **Visual Design**

**Header:**
```
🔥 Calcifer's Hot 20
```
*(Just the icon and title; the subtitle lives in the opening callout)*

**Opening callout** (if a big climber exists this week):
```
⚡ **[Name]** climbing hard — now rank [position], up [delta] this week.
```
*(Only shows if there's a real climber ≥2 spots)*

**The rows:**
```
 1. 🔥 **Thrall** · Fire Mage · `3200` ▲2
 2. 💜 **Jaina** · Frost Mage · `3150` –
 3. ⚔️ **Gul'dan** · Rogue · `3100` ▼1
 4. 🛡️ **Arthas** · Warrior · `3050` ▲5
 …
20. 🛡️ **Uther** · Paladin · `2200` NEW
```

**Styling rules:**
- Rank in `` `backticks` `` for visual separation
- Role emoji (🔥 = tank shield, 💜 = healer, ⚔️ = dps, 🛡️ = shield) — use `brand.icon()` per role
- Name in **bold**
- Class plain text (no emoji; the role icon handles it)
- Score in `` `backticks` ``
- Movement: `` ▲3 `` (climbed 3), `` ▼2 `` (dropped 2), `` – `` (no change), `` NEW `` (entered top 20)

**Footer:**
```
Tended by Calcifer. Mind the sparks. | The fire's roaring tonight.
```
*(First part is `board_signoff`; second part is `voice.flavour("mplus")` — optional, tone-gated)*

**Color:** `GUILD_EMBER` (0xCC4422)

**Mood emoji (optional, not yet wired):** 🔥 (delighted when top scorer is way ahead), 🤔 (thinking if close race)

---

#### **Copy Design Notes**

- **Header is minimal** — the leaderboard *is* the story
- **Climber callout is optional** — only shows if there's real movement (≥2 spots)
- **Emotion in the flavor line** — "The fire's roaring" is pure M+ adrenaline
- **Sign-off ties back to the guild** — "Tended by Calcifer. Mind the sparks" reminds the guild this is a shared space

---

#### **Current Implementation**

✅ **Status:** Live, works well  
✅ **Voice:** Uses `voice.flavour("mplus")`  
✅ **Updates:** Daily baseline roll, on-demand member score changes

⚠️ **Improvements needed:**
- Add climb callout threshold logic (≥2 spots)
- Add optional mood emoji (🔥 for top scorer way ahead)
- Ensure role icons render correctly (use `brand.icon()`)

---

### **BOARD 2: WEEKLY EVENTS — This Week at the Hearth**

**Audience:** Whole guild, anyone wondering "what's on?"  
**Purpose:** Replace scattered Raid-Helper pings with one tidy calendar. Show times in local TZ.  
**Update trigger:** Event created/updated/deleted via webhook, or on refresh timer

---

#### **Visual Design**

**Header:**
```
📅 This week at the hearth
```

**Event rows:**
```
🌙 Monday 20:15 CET · **Mythic Broodkeeper** · 12 signed up · [Sign up ➜](link)
📋 Tuesday evening · **Casual keys** · Open sign-ups · [Sign up ➜](link)
🌙 Thursday 20:15 CET · **Mythic Broodkeeper** · 3 signed up ⚠️ thin · [Sign up ➜](link)
📋 Sunday 19:15 CET · **Social hangout** · Always room · [Sign up ➜](link)
```

**Styling rules:**
- Day emoji (🌙 if raid time, 📋 if casual/social) — consistent icon system
- Time in absolute + relative: `<t:UNIX:F>` renders as "Tuesday, June 10 2026 20:15" in viewer's timezone
- Event title as masked link: `[title](url)` — points to Raid-Helper
- Signup count: `N signed up` or `Open sign-ups`
- Warning emoji (⚠️) if signup rate is low relative to raid size (e.g., <50% for a 20-man)
- CTA button (always): `[Sign up ➜](url)` — inline masked link

**Warning logic** (optional flavor):
```python
if signup_count / expected_roster_size < 0.5:
    # Add ⚠️ and warn line
    context = voice.flavour("attendance")  # "Every hand at the hearth counts."
```

**Footer:**
```
Sign-ups live in Raid-Helper. See you there.
```
*(No `board_signoff` line; this is pure coordination, not about Calcifer)*

**Color:** `GUILD_EMBER`

**Empty state:** No board posted if no events within 7 days.

---

#### **Copy Design Notes**

- **Emoji + time** = instantly scannable
- **Masked link titles** = click-through to sign up (no manual copying)
- **Local timezone rendering** = no "wait, is that CET or my time?" confusion (Discord does it)
- **Warning on thin signups** = nudge people to show up without pinging
- **Sign-up CTA** = always clear next action

---

#### **Current Implementation**

✅ **Status:** Live  
✅ **Dynamic times:** Uses `<t:UNIX:F>` (Discord-rendered per timezone)  
✅ **Webhook-driven:** Updates on Raid-Helper events

⚠️ **Improvements needed:**
- Add thin-signup warning emoji + flavor line
- Add day-of-week emoji (🌙 for evening raids, 📋 for casual)
- Test relative time rendering on mobile

---

### **BOARD 3: NEWS HIGHLIGHTS — News from the Wider World**

**Audience:** Guild members who like to know what's happening in WoW  
**Purpose:** Replace three duplicate news bots with one curated page. High-tier items stand out; latest items are a quick list.  
**Update trigger:** New news items archived (async from Wowhead feed)

---

#### **Visual Design**

**Header:**
```
📰 News Highlights
```

**Intro:**
```
The wider world in brief. Calcifer keeps these tidy so you don't have to.
```

**Highlights section** (top 3 items, high-tier only):
```
🛠️ [Patch 10.1.5 Notes Released](link) · 2 hours ago
🔵 [Blue post: Loot system changes](link) · 8 hours ago
📄 [PTR updated with new dungeons](link) · 1 day ago
```

**Styling rules:**
- Tier-based glyph: 🛠️ (patch notes), 🔵 (blue post), 📄 (PTR/major news), 📰 (general)
- Title as masked link: `[headline](url)`
- Relative time: `· [time ago]` using `<t:UNIX:R>` (e.g., "2 hours ago")

**Latest section** (bullet list, up to 7 items, any tier):
```
• [Hotfix: Mythic+ scaling](link) — 30m ago
• [Raid schedule extended to July](link) — 4h ago
• [Class balance changes incoming](link) — 12h ago
…
```

**Footer:**
```
Fresh off the embers. Read at your leisure.
```

**Color:** `BLUE_FLAME` (0x2EA3FF) — external/relayed news, not guild data

**Mood:** None (we're relaying Wowhead's words verbatim)

**Empty state:** No board if no news archived yet.

**House style:** Headlines are Wowhead's words, shown verbatim. Only Calcifer's framing (header, section labels, signoff) obeys the no-em-dash rule.

---

#### **Copy Design Notes**

- **Two-zone layout** = high-tier items stand out (patch notes, blues), but you can skim latest if in a hurry
- **Relative time** = "2 hours ago" is more useful than "June 8 14:32"
- **Masked links** = click through, no copy-paste
- **Blue flame color** = signals "this is relayed, not our data"
- **Clean intro** = sets expectation (Calcifer curates, doesn't clutter)

---

#### **Current Implementation**

✅ **Status:** Live  
✅ **Two-zone layout:** Highlights + Latest  
✅ **Dedup across feeds:** Archives once from any of three Wowhead sources

⚠️ **Improvements needed:**
- Verify tier glyph consistency (🛠️ 🔵 📄 📰)
- Test headline truncation (long patch notes titles)
- Ensure masked links render on mobile

---

### **BOARD 4: GUIDES & RESOURCES — The Links Worth Keeping**

**Audience:** Guild members learning specs, routes, strategies  
**Purpose:** One curated grid of the best resources. Never rots (Calcifer owns it).  
**Update trigger:** `guides.json` file edit

---

#### **Visual Design**

**Header:**
```
📚 Guides & Resources
```

**Intro:**
```
The links worth keeping, kept tidy. Calcifer owns this page, so it never rots.
```

**Sections:**

```
═══ RAIDING ═══
🗂️ Warcraft Logs — parse analysis
🌐 Wowhead raid guides — strategies per boss
📊 Raidbots — gear optimization

═══ MYTHIC+ ═══
📈 Raider.IO — push leaderboards & affixes
🗺️ Keystone.guru — route maps
📋 Weekly affixes breakdown

═══ THEORYCRAFT & TOOLS ═══
⚙️ Raidbots SimC — DPS sims
📖 Icy Veins — class guides & builds
```

**Styling rules:**
- Section header: `═══ SECTION NAME ═══` (visual break)
- Per-link: emoji + **name** as masked link
- Description: plain text under link (e.g., "parse analysis")
- Simple, scannable, one per line

**Footer:**
```
Kept warm by Calcifer.
```

**Color:** `GUILD_EMBER`

**Update frequency:** Only when `guides.json` file changes (static content)

**Empty state:** No board if no sections in JSON.

---

#### **Copy Design Notes**

- **Emoji dividers** = visual breathing room
- **Descriptions** = why these links (not just a list of blue underlines)
- **Intro explains the promise** = "Calcifer owns it, so it never rots" — signals maintenance, not abandonment
- **Simple grid layout** = easy to scan on mobile

---

#### **Current Implementation**

✅ **Status:** Live  
✅ **Static content:** Pulls from `guides.json`

⚠️ **Improvements needed:**
- Add emoji dividers between sections (the `═══` rule)
- Ensure descriptions are clear (not just link names)
- Test link rendering and truncation

---

### **BOARD 5: RULES & INFO — The House, In Short**

**Audience:** Guild members (daily), new applicants (first visit)  
**Purpose:** One tidy page of guild values + live facts. Never drifts.  
**Update trigger:** Live token changes (member count, active %, progression)

---

#### **Visual Design**

**Header:**
```
📜 Rules & Info
```

**Intro:**
```
The guild, in short. The live bits below always refresh, so they never drift stale.
```

**Sections** (from `rules.json` template + live tokens):

```
═══ WHO WE ARE ═══
~300 members strong. ~45% active in the last 14 days.
Founded in 2008. Zero pressure. Community first.
Everyone is welcome: raiders, casuals, alts, visitors.

═══ HOW WE RAID ═══
Team Chemistry: Mythic prog (Cutting Edge every tier).
Open raid: Heroic signup, anyone who's ready.
Raid times: Tuesday & Thursday 20:15 CET.

═══ LOOT & ROLES ═══
Loot Council distributes. Raiders first. Fairness always.
Need tank dps healer (currently recruiting {roles}).

═══ HOUSE RULES ═══
→ Be kind. Patience with learners.
→ Communicate. Say if you can't make it.
→ Leave drama at the door.
```

**Live tokens** (refresh on fact change):
- `{member_count}` → Current roster size
- `{active_pct}` → Activity % (last 14 days)
- `{progression}` → Current tier progression
- `{roles}` → Currently recruiting roles (tank/healer/dps)
- `{raid_times}` → Raid schedule

**Footer:**
```
Kept warm by Calcifer.
```

**Color:** `GUILD_EMBER`

**Update frequency:** Whenever a live fact changes (member count drifts, new tier, recruitment opens/closes).

---

#### **Copy Design Notes**

- **Live tokens** = no outdated info (member count always current, recruitment status auto-updates)
- **Emoji dividers** = separates sections visually
- **Intro sets tone** = warm, owned by Calcifer, maintained
- **House rules are inviting** = "be kind" not "violations result in kick"
- **Progression + raid times** = info visitors need most

---

#### **Current Implementation**

✅ **Status:** Live  
✅ **Live tokens:** Implemented, refreshes when facts change  
✅ **GM-editable:** JSON is the source

⚠️ **Improvements needed:**
- Warm up intro (currently fine; could be more conversational)
- Add emoji to rule headers (→ is OK but 📌 or similar might feel more branded)
- Ensure role names render correctly (case-sensitive from config)

---

### **BOARD 6: ROSTER PULSE — The Guild's Heartbeat**

**Audience:** Officers and guild strategists wanting a health snapshot  
**Purpose:** Answer "How's the guild doing?" with 7 key metrics.  
**Update trigger:** Whenever metrics shift (rounded; ignores flicker)

---

#### **Visual Design**

**Header:**
```
🔥 Guild Heartbeat
```

**Metrics** (as embed fields, inline where space allows):

```
Members: 287
Active (14d): 156 (54%)
Health Score: 78/100 ✅
───────────────────────────────────
Avg ilvl: 486
Avg M+ Score: 2847
───────────────────────────────────
Role Split
🛡️ 12 tanks · 💚 18 healers · ⚔️ 51 dps
───────────────────────────────────
Progression
Mythic: 8/8 ✅ · Raid Team: 20 active
```

**Styling rules:**
- Metrics split into two sections: overall health (members, active %, score) and detail (ilvl, M+, role, prog)
- Health score: color-coded or emoji (✅ >70, ⚠️ 50-70, 🔥 crisis <50)
- Role split: emoji + count, scannable
- Progression: "8/8" format with emoji signoff

**Health score logic:**
```python
if health_score > 70:
    emoji = "✅"
    flavor = voice.flavour("success")  # "Well earned."
elif health_score > 50:
    emoji = "⚠️"
    flavor = voice.flavour("alert_warning")  # "Things are getting thin."
else:
    emoji = "🔥"
    flavor = voice.flavour("alert_critical")  # "This one needs eyes on it."
```

**Footer:**
```
The guild's vital signs. Tended by Calcifer.
```
*(Add flavor line if health is remarkable)*

**Color:** `GUILD_EMBER`

**Empty state:** No board if no members.

---

#### **Copy Design Notes**

- **7 metrics = not overwhelming, complete picture** — covers health (members, active %), power (ilvl, M+), structure (roles), goal (progression)
- **Health score emoji** = at-a-glance feeling (✅ vs. ⚠️ vs. 🔥)
- **Flavor line when needed** = celebrate health or warn about slump
- **Sparkline (future)** = attendance trend last 4 weeks as emoji: 📈 / 📊 / 📉 — too advanced for now, add later

---

#### **Current Implementation**

✅ **Status:** Live  
✅ **Metrics calculation:** Implemented in `metrics.py`  
✅ **Rounded values:** Avoids edit-flicker

⚠️ **Improvements needed:**
- Add health score flavor logic (success/warning/critical)
- Add health score emoji (✅ / ⚠️ / 🔥)
- Add role emoji (🛡️ 💚 ⚔️) — may already work
- Consider sparkline for attendance trend (low-priority, later)

---

### **BOARD 7: RECRUITMENT — We're Looking**

**Audience:** Guild members, new applicants, talent market  
**Purpose:** Open the guild's front door. Show why joining is worth it.  
**Update trigger:** Officer-configured via `/recruit` commands (slots, rules)

---

#### **Visual Design** *(This is the new one; designed in this spec)*

**Header:**
```
🔎 We're Looking
```

**Open Slots** (officer-configured):
```
🛡️  TANK: 1 slot available · ilvl 485+ · CE-capable
💚 HEALER: 2 slots available · ilvl 480+ · flexible build
⚔️  DPS: 3 slots available · ilvl 475+ · any spec welcome
```

**Styling rules:**
- Role emoji + role name + slot count
- Minimum ilvl hint (officer-set, hidden if not configured)
- Brief note per role (officer-set; optional)

**What We Value In Trials** (static cultural text from `recruitment.json` template):
```
✓ You show up on time, prepared.
✓ You help guildmates.
✓ You're curious: ask questions, give feedback.
✓ You play your class well (no parsing contest required).
✗ We don't care about: drama, ego, silent disappears.
```

**Trial Expectations** (officer-configured rules):
```
→ First 4 raids are a trial (typically 2-week window).
→ Raid times: <t:RAID_EPOCH:F> weekly (Paris time).
→ Discord required during raids (comms + callouts).
→ Loot: raiders > trial > social (fair, transparent).
→ If it's not working, we talk about it early.
```

**About Us** (from `recruitment.json` + live tokens):
```
From the Ashes is a community-first guild on Hellfire EU (EU).
Founded 2008. ~300 members. Mythic prog (Cutting Edge every tier).
But also? Casuals, alts, students, new parents—zero pressure.
We're here to have fun and support each other.
```

**Call to Action:**
```
Interested? DM <@Officer1> or <@Officer2> or <@Officer3>,
or say hello in #recruitment first—we'd love to hear from you.

```
*(Optional flavor line below CTA):*
```
Come stake your claim. — Calcifer
```

**Footer:**
```
Kept warm by Calcifer.
```

**Color:** `GUILD_EMBER` (our guild values, not external recruitment)

**Empty state:** If no slots open, board shows:
```
The guild's full for now. But always happy to hear from you.
DM an officer if you're interested; we rotate trials seasonally.
```

**Update trigger:** Officer runs `/recruit slots tank 1` or `/recruit rule add "..."`; board re-renders on next tick.

---

#### **Copy Design Notes**

- **"We're Looking" not "Join Us"** = direct, action-oriented
- **What We Value before "How to Apply"** = culture first, not just "need DPS"
- **Trial expectations clear** = no surprises ("first 4 raids," "loot priority," "Discord comms")
- **About Us is warm** = "here to have fun and support each other" echoes the guild's real values
- **Officer @ mentions** = make it easy to find who to DM (no "contact leadership" vagueness)
- **Flavor line "Come stake your claim"** = voice bank (`recruiting`), tone-gated
- **Empty state is warm** = "always happy to hear" not "closed for applications"

---

#### **Future: `/recruit` Command System**

Officers control everything via slash commands:

```
/recruit slots tank 1               # Set TANK to 1 open
/recruit slots dps 0                # DPS full
/recruit ilvl tank 485              # TANK minimum
/recruit rule add "Show up prepared"
/recruit rule list
/recruit message "Custom pitch"     # Override template
/recruit show                        # Preview board
/recruit reset                        # Restore defaults
```

Each command updates the DB; the board re-renders on next tick.

---

#### **Current Implementation**

🚧 **Status:** Designed, not yet coded  
🚧 **Missing:** Renderer file (`boards/recruitment.py`), `/recruit` command group

**What needs to be built:**
1. `boards/recruitment.py` renderer
2. `cogs/recruit.py` expansion for `/recruit slots/rule/message/...` commands
3. `recruitment_config` table (or extend existing schema)
4. Tests for the renderer and commands

---

### **BOARD 8: ROSTER (MENTION, FUTURE)**

**Status:** Planned, not detailed in this spec yet.  
**Purpose:** Member directory (officers/raid leads view).  
**Challenge:** 300 members can't fit one embed; needs pagination or modal.  
**Defer to next phase.**

---

### **BOARD 9: ANNOUNCEMENTS (MENTION, FUTURE)**

**Status:** Planned, not detailed in this spec yet.  
**Purpose:** GM-posted sticky callouts (first kills, raids off, recruitment push).  
**Design needed:** `/announce` command system + situational voice context.  
**Defer to next phase.**

---

## Part 5: The Unified Voice & Visual System

All sticky posts speak with **one voice through different contexts:**

### **A. The Header Tone Gradient**

| Board | Tone | Header | Flavor |
|-------|------|--------|--------|
| **Hot 20** | Fast, excited | "Calcifer's Hot 20" | "The fire's roaring" |
| **Events** | Warm, coordinating | "This week at the hearth" | (none; coordination) |
| **News** | Curious, informed | "News from the wider world" | (none; relaying) |
| **Guides** | Helpful, warm | "Guides & Resources" | (none; reference) |
| **Rules** | Inviting, clear | "Rules & Info" → "The House, In Short" | (none; values) |
| **Pulse** | Health-conscious | "Guild Heartbeat" | "Well earned" / ⚠️ "Getting thin" |
| **Recruitment** | Inviting, direct | "We're Looking" | "Come stake your claim" |

### **B. The Color System**

- **`GUILD_EMBER` (0xCC4422)** — Our guild's data and values. Hot 20, Events, Guides, Rules, Pulse, Recruitment.
- **`BLUE_FLAME` (0x2EA3FF)** — Calcifer relayed external information. News (Wowhead headlines).
- *Amber & Red reserved for alerts, not boards.*

### **C. The Emoji System**

**Board headers:**
- 🔥 Hot 20 (M+, fire = competitive)
- 📅 Events (calendar)
- 📰 News (scroll/newspaper)
- 📚 Guides (book)
- 📜 Rules (scroll/parchment)
- 🔥 Pulse (heartbeat? → could be 💗)
- 🔎 Recruitment (magnifying glass)

**Inline icons:**
- 🛡️ Tank, 💚 Healer, ⚔️ DPS (roles)
- 🌙 Evening raids, 📋 Casual events (event type)
- ✅ Health OK, ⚠️ Warning, 🔥 Critical (status)
- 📈 Climbing, ▲/▼/– (rank movement)

**Mood emoji (not yet wired, but ready):**
- 🔥 Delighted (celebration, hot competition, success)
- 🤔 Thinking (analyzing, waiting for data)
- 😏 Smug (we're crushing it)
- ⚠️ Alarmed (thin signups, low health)
- 💀 Dismal (crisis, all hands needed)

### **D. The Voice Bank Integration**

Every sticky post uses voice banks for optional flavor:

```python
voice = brand.Voice(tone=config.persona_tone)

# Structural lines (always return):
signoff = voice.line("board_signoff")  # "Tended by Calcifer. Mind the sparks."

# Flavor lines (tone-gated, may return None):
flavor = voice.flavour("mplus")  # M+ domain (Hot 20)
flavor = voice.flavour("recruiting")  # Recruitment (Recruitment board)
flavor = voice.flavour("success")  # Celebrate (Pulse if health >70)
flavor = voice.flavour("attendance")  # Warn (Events or Pulse if thin)
```

---

## Part 6: Delivery Roadmap — One by One, Handcrafted

### **This specification is the complete design for:**

✅ **DONE (review the spec above)**
- Board 1: Hot 20 — Polish to add mood, ensure role icons
- Board 2: Weekly Events — Polish to add thin-signup warning
- Board 3: News Highlights — Polish visual hierarchy, dividers
- Board 4: Guides & Resources — Polish headers, emoji per link
- Board 5: Rules & Info — Warm up copy, ensure live tokens work
- Board 6: Roster Pulse — Add health score flavor, emoji, sparkline future

🚧 **NEXT (ready to implement)**
- Board 7: Recruitment — Build renderer + `/recruit` commands
  - Fully specified above with exact copy, voice patterns, update triggers
  - Officer control system designed
  - Empty state copy ready
  - Brand tone set (direct, inviting, culture-first)

📋 **PLANNED (defer to next phase)**
- Board 8: Roster directory
- Board 9: Announcements

---

## Part 7: Differentiation: Sticky Posts vs. Command Replies

**This document is ONLY about sticky posts** — the permanent, always-on dashboards.

**Separate concern (commands):** Draft coming next.

| Aspect | Sticky Posts | Command Replies |
|--------|---|---|
| **What** | Permanent dashboard living in a channel | Response to a user's slash command |
| **When you see it** | Always there; you look when you want | One-time reply (may disappear or stay in history) |
| **Who controls it** | Officers via `/board` and data commands | Bot automatically, or officer `/feed`/`/recruit` config |
| **Voice** | Full Calcifer personality (signoff, flavor, mood) | (Separate spec; mostly unvoiced today) |
| **Pings?** | Never | (Separate spec; opt-in or officer-routed) |
| **Edits or new?** | Edits in place (same message, diffed content) | Fresh reply each time (or none if config) |
| **Timestamp** | Embedded, Discord-rendered per viewer's TZ | (Separate spec) |

---

## Appendix: Implementation Checklist

### **For each existing board — Polish phase (quick wins)**

- [ ] Hot 20: Add climb-callout threshold, role emoji verification, optional mood
- [ ] Events: Add thin-signup warning + flavor callout
- [ ] News: Add emoji dividers, verify link rendering on mobile
- [ ] Guides: Add section dividers (`═══`), test description clarity
- [ ] Rules: Verify live tokens update correctly, warm up intro copy
- [ ] Pulse: Add health score emoji + flavor logic, test role emoji

### **For Recruitment board — Build phase (new feature)**

- [ ] Design `/recruit` command group (slots, rules, message, show, reset)
- [ ] Create `boards/recruitment.py` renderer
- [ ] Create `recruitment_config` schema/table if needed
- [ ] Wire voice banks: `recruiting`, `empty_recruiting`
- [ ] Test empty state, full state, partial slots
- [ ] Test officer commands and board re-renders
- [ ] Verify Discord link mentions render correctly (@Officer tags)

### **Files to modify/create**

| File | Change | Priority |
|------|--------|----------|
| `ftabot/boards/hot20.py` | Add climb callout, mood emoji | Medium |
| `ftabot/boards/events.py` | Add thin-signup warning | Medium |
| `ftabot/boards/news.py` | Add emoji dividers | Low |
| `ftabot/boards/guides.py` | Add dividers, test links | Low |
| `ftabot/boards/rules.py` | Warm copy, verify tokens | Low |
| `ftabot/boards/pulse.py` | Add health flavor, emoji | Medium |
| `ftabot/boards/recruitment.py` | **NEW** | High |
| `ftabot/cogs/recruit.py` | Expand with `/recruit` group | High |
| `ftabot/content/recruitment.json` | **NEW** GM-editable | High |
| `ftabot/db_schema.py` | Add recruitment config fields if needed | High |
| `tests/test_boards.py` | Add recruitment board tests | High |

---

## Summary

**You now have:**

1. ✅ **A clear definition** of what a sticky post is (not a command reply)
2. ✅ **A design contract** that every sticky post follows (header, content, footer, flavor, color)
3. ✅ **Handcrafted specs for each board** — exact copy, voice patterns, visual rules
4. ✅ **A unified voice system** that ties all sticky posts together
5. ✅ **A delivery roadmap** — polish existing, build Recruitment next
6. ✅ **Complete spec for Recruitment board** — ready to implement

**Next step:** Build the Recruitment board as the first "from this spec" sticky post, demonstrating the design system in action.

