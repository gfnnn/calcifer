# Board Messaging Specification — headers, footers, empty states

This document specifies the voice + copy for each board. Each board follows the same pattern: **header** → **content** → **footer (signoff)** + **timestamp**.

---

## Pattern: Data-driven boards

```
[icon] **Title**: header text or summary
═════════════════════════════════════════
[rows/items with formatting]
═════════════════════════════════════════
[optional: flavor line from voice bank]
*[Signoff]. [Updated timestamp]*
```

- **Color:** `GUILD_EMBER` (our own data)
- **Header:** Icon + bold title + optional flavor
- **Content:** Rows, items, or structured data
- **Signoff:** Always `voice.line("board_signoff")` for these
- **Timestamp:** Relative + absolute, if space allows

---

## 1. Hot 20 — Mythic+ Leaderboard

**Type:** Data-driven (member roster scores)

**Current state:** ✅ implemented

**Voice context:** M+ (fast, excited)

**Specifications:**

| Element | Copy / Rule |
| --- | --- |
| **Icon** | 🔥 or `:fta_flame:` |
| **Title** | "Topping the embers" |
| **Header flavor** | M+ domain optional, e.g. "**[leader]** at `[score]`, the fire roars tonight." |
| **Rank movement** | `▲3 NEW ▼2` (markup already in place) |
| **Row format** | `` `[rank]` [glyph] **[name]** · [class] · `[score]` [movement]`` |
| **Empty state** | Return `None` (no post) — a fresh guild has no scores yet |
| **Signoff** | `voice.line("board_signoff")` |

**Copy examples (already in code):**
- "🔥 Topping the embers: **[leader]** at `[score]`."
- Climb callout: "**[name]** climbing hard, now `[movement]`." (only if ≥2 rank jump)

---

## 2. Weekly Events — Raid-Helper Calendar

**Type:** Data-driven (calendar + signup counts)

**Current state:** ✅ implemented

**Voice context:** Neutral (community events, not domain-specific)

**Specifications:**

| Element | Copy / Rule |
| --- | --- |
| **Icon** | 📅 or `:fta_calendar:` |
| **Title** | "This week at the hearth" |
| **Event line** | `📅 [title] · <t:UNIX:F> (<t:UNIX:R>) · [N] signed up · [Sign up ➜]` |
| **Empty state** | Return `None` (no post) — no events scheduled |
| **Signoff** | `voice.line("board_signoff")` + "Sign-ups live in Raid-Helper." |
| **Timestamp** | Embed timestamp (when board updates) |

**Voice usage:**
- No domain flavor (neutral, just coordination)
- Signoff is standard

**Implementation note:**
- Event titles are relayed from Raid-Helper (not Calcifer-authored), so no sanitize needed
- But the footer callout ("Sign-ups live in Raid-Helper") should be sanitized

---

## 3. News Highlights — Wowhead + Blue Post Feed

**Type:** Data-driven (two-zone: Highlights + Latest)

**Current state:** ✅ implemented (partial)

**Voice context:** Neutral (external news, relayed)

**Specifications:**

| Element | Copy / Rule |
| --- | --- |
| **Icon** | 📰 or `:fta_scroll:` |
| **Title** | "News Highlights" |
| **Highlights zone** | Top 3 highest-tier items (patch notes, blue posts, PTR), with glyphs + masked links |
| **Glyph per type** | Patch notes 🛠️, Blue posts 🔵, News 📰 |
| **Latest zone** | Next 7 items, cleanest list |
| **Empty state** | Return `None` (no post) — no news items yet |
| **Signoff** | `voice.line("board_signoff")` |
| **Timestamp** | Embed timestamp |

**Copy rules:**
- **Headlines are relayed** (Wowhead's words) — shown verbatim, never sanitized
- **Our framing** (zone headers, signoff) — sanitized

**Voice usage:**
- No domain flavor (we're relaying external info)
- Signoff is standard

---

## 4. Guides & Resources

**Type:** Community board (curated, hand-edited)

**Current state:** 🚧 not fully detailed

**Voice context:** Neutral (reference material)

**Specifications:**

| Element | Copy / Rule |
| --- | --- |
| **Icon** | 📖 or `:fta_book:` |
| **Title** | "Guides & Resources" |
| **Content** | Curated links/guides from `content/guides.json` |
| **Structure** | Sections: Raiding, Mythic+, Community, External |
| **Empty state** | Show a minimal "Loading..." or use a static fallback |
| **Signoff** | `voice.line("board_signoff")` |

**Implementation:**
- Content from `content/guides.json` (GM-editable, JSON file)
- Render as nested markdown list with masked links
- If JSON missing/malformed, fall back to static text
- No voice flavor (it's reference material)

---

## 5. Rules & Info

**Type:** Community board (curated, hand-edited)

**Current state:** 🚧 not fully detailed

**Voice context:** Neutral (house rules, no flavor)

**Specifications:**

| Element | Copy / Rule |
| --- | --- |
| **Icon** | ⚖️ or `:fta_scales:` |
| **Title** | "Rules & Info" |
| **Content** | Sections from `content/rules.json`: Code of Conduct, Loot Rules, Raid Expectations, Discord Channels |
| **Structure** | Numbered sections with clear formatting |
| **Empty state** | Show fallback rules text |
| **Signoff** | `voice.line("board_signoff")` |

**Voice usage:**
- No flavor (rules are serious)
- Signoff is standard

---

## 6. Pulse — Roster Health Snapshot

**Type:** Data-driven (summary metrics)

**Current state:** 🚧 planned

**Voice context:** Neutral (health report, measured)

**Specifications:**

| Element | Copy / Rule |
| --- | --- |
| **Icon** | 💗 or `:fta_pulse:` |
| **Title** | "Roster Pulse" |
| **Metrics** | Total active members, active raiders, MW average ilvl, attendance % last 4 weeks, recent alts |
| **Format** | Key-value pairs or bar charts (if space) |
| **Sparkline** | Optional: attendance trend (ascii or emoji) |
| **Empty state** | Return `None` (needs at least 7 days of data) |
| **Signoff** | `voice.line("board_signoff")` |
| **Timestamp** | When pulse was calculated |

**Copy:**
- Header: "The heart beats steady." or "How the guild feels."
- If health is good: `voice.flavour("success")` in footer
- If thin on attendance: `voice.flavour("attendance")` warning

---

## 7. Recruitment — Open Slots & Guild Culture

**Type:** Community board (curated + status-driven)

**Current state:** 🚧 not yet implemented

**Voice context:** Recruitment (exciting, direct)

**Specifications:**

| Element | Copy / Rule |
| --- | --- |
| **Icon** | 🔎 or `:fta_magnify:` |
| **Title** | "We're Looking" |
| **Section 1: Open Slots** | Tank/Healer/DPS roles with counts; recruitment priority |
| **Section 2: What We Value** | Curated from CLAUDE.md + vision: community-first, patience, zero-pressure, Cutting Edge in TC |
| **Section 3: How to Apply** | "DM [officer], apply via [link], or say hello in #recruitment" |
| **CTA** | `voice.flavour("recruiting")` callout: "Come stake your claim." |
| **Empty state** | `voice.line("empty_recruiting")` + "But always happy to hear from you." |
| **Signoff** | Signed "Calcifer" (recruiting context, not standard signoff) |

**Copy rules:**
- **Tone:** Direct, excited, inviting
- **No sugar-coating:** Be honest about what we value
- **Culture first:** Who we are > what you need to know

**Implementation:**
- Slots data from `Config.recruitment_slots` (JSON or DB field)
- Culture text from `content/recruitment.json` (editable)
- CTA is dynamic: `voice.flavour("recruiting")` or fallback to standard signoff

---

## 8. Roster — Member Directory (Planned)

**Type:** Data-driven (all members)

**Current state:** 🚧 not yet implemented

**Voice context:** None (reference, no flavor)

**Specifications:**

| Element | Copy / Rule |
| --- | --- |
| **Icon** | 👥 or `:fta_roster:` |
| **Title** | "Guild Roster" |
| **View modes** | (swappable, Raid Leader view only) |
| **Columns** | Name, Class, Role, Main/Alt, Activity, ilvl |
| **Sorting** | By activity, by role, by ilvl (configurable per view) |
| **Empty state** | N/A (roster never truly empty) |
| **Signoff** | None (roster is reference) |

**Implementation:**
- Will need pagination or a second render (can't fit 300 members in one embed)
- May split into Raid view + Officer view (different access gates)
- No voice flavor (it's data)

---

## 9. Announcements — Sticky Posts (Planned)

**Type:** Hand-posted (GM-controlled, situational)

**Current state:** 🚧 design phase

**Voice context:** Situational (varies by announcement type)

**Specifications:**

**Announcement types & voice patterns:**

| Type | Header icon | Voice context | Example |
| --- | --- | --- | --- |
| **Patch notes alert** | 🛠️ | `alert_critical` | "The moment's here: [patch]" |
| **First kill** | 💀 | `success` | "The fire blazes tonight: [boss] down." |
| **Raid cancelled** | ⚠️ | `alert_warning` | "Tonight's raid: off. Embers go dark." |
| **Recruitment push** | 🔎 | `recruiting` | "We're looking: [roles]. Come in?" |
| **Event reminder** | 📅 | neutral | "[event] starts in 30m. See you there?" |

**General format:**
```
[icon] **[type]**: [main announcement]
───────────────────────────────────────
[details: when, why, what to do]
───────────────────────────────────────
[CTA or callout from voice bank]
— Calcifer
```

**Rules:**
- Announcements are GM-posted (not auto-generated)
- Each uses a context-appropriate voice bank
- Avoid tone overload: one announcement at a time, pinned
- Include a clear CTA (what do I do about this?)

---

## 10. Implementation Checklist

- [ ] **Hot 20**: Already done ✅
- [ ] **Events**: Already done ✅
- [ ] **News**: Extend with clarified specs above
- [ ] **Guides**: Build from `content/guides.json`
- [ ] **Rules**: Build from `content/rules.json`
- [ ] **Pulse**: Create renderer + metric calc
- [ ] **Recruitment**: Design board + create renderer
- [ ] **Roster**: Design (paginated?) + gate by role (RL+)
- [ ] **Announcements**: Define `/announce` command + template system

---

## 11. Copy examples (ready to use)

### Recruitment board headers
- "We're looking." (minimal)
- "Come strengthen the hearth." (warm)
- "New blood wanted." (direct)

### Thin attendance warnings
- "The bench is waiting." (`empty_signups`)
- "Every hand counts." (`attendance`)
- "Fresh faces keep it burning." (`attendance`)

### Success callouts
- "Well earned." (generic)
- "The fire blazes tonight." (high-energy, raid-context)
- "That's the stuff legends are made of." (loot/achievement)

### Error messages
- "The flames flicker. Something's not right." (generic)
- "My sight's clouded. Try again?" (transient, invite retry)
- "The embers won't tell me." (network/API issue)

---

## 12. Voice usage across boards (quick reference)

| Board | Default signoff | Optional flavor | Tone context |
| --- | --- | --- | --- |
| Hot 20 | `board_signoff` | `mplus` in header | M+ (fast) |
| Events | `board_signoff` + manual note | none | neutral |
| News | `board_signoff` | none | neutral (relayed) |
| Guides | `board_signoff` | none | neutral (reference) |
| Rules | `board_signoff` | none | neutral (house rules) |
| Pulse | `board_signoff` | `success` if healthy, `attendance` if thin | measured |
| Recruitment | recruiting flavor + manual signoff | always include | recruiting (excited) |
| Roster | none | none | reference |
| Announcements | situational | always include | situational |

---

## Next: Command messaging

Once boards are locked in, the next phase is **command replies**:
- How `/recruit scout` uses voice
- How `/admin sync` reports progress
- How `/loot bid` confirms a bid
- How errors are communicated

Pattern: `voice.line("thinking")` → work → `result + voice.flavour(...)`

