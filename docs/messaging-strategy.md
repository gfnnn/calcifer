# Calcifer's Messaging Strategy — voice, boards, and interactions

This document describes how Calcifer's voice is structured, where it appears, and how to expand it coherently as the bot grows.

## 1. Three layers of messaging

### Layer 1: Voice banks (`brand.py`)
The raw material — context-keyed lines that express Calcifer's personality. Every surface (boards, commands, feeds) draws from these via the `Voice` class.

**Structure:**
- **Structural lines** — always return something (e.g., "thinking", "not_found", "denied"). These are required.
- **Flavour lines** — optional garnish, tone-gated (off/light/full). These disappear in `off` mode, are 50/50 in `light`, always show in `full`.
- **Domain flavour** — paced to the content: M+ is fast/excited, raids/logs are measured/patient.

**Current banks:**
- `thinking` (structural) — search in progress
- `not_found` (structural) — member/data lookup failed
- `denied` (structural) — permission denied
- `empty` (structural) — no data to show
- `board_signoff` (structural) — footer of all boards
- `mplus` (flavour) — Mythic+ domain voice
- `raid` (flavour) — raid progression domain voice
- `logs` (flavour) — WCL/stats domain voice
- `_default` (fallback for unknown keys)

**Gaps (to add):**
- `recruiting` — recruitment context (scout, open slots, talent)
- `loot` — loot distribution context (vault, priority, bids)
- `attendance` — signups/attendance context (thin turnout, full bench, turnover)
- `alert_warning` — warnings (prog stall, downed bosses, low DPS)
- `alert_critical` — urgent (first kill, patch notes, major changes)
- `success` — celebration (achievement, new rank, milestone)
- `empty_recruiting` — specific flavor when recruit board is empty
- `empty_signups` — specific flavor when event signups are thin
- `error` — something broke (API down, parse failed)

### Layer 2: Board-specific messaging
Each board has its own **header**, **empty state**, and **signoff**, drawing from the voice banks but customized per board type.

**Current boards:**
| Board | Header | Empty state | Signoff source |
| --- | --- | --- | --- |
| **Hot 20** | "🔥 Topping the embers" | Return `None` (no post) | `board_signoff` flavour (M+) |
| **Events** | "📅 This week at the hearth" | Return `None` (no post) | `board_signoff` + manual note |
| **News** | "📰 Highlights" | Return `None` (no post) | `board_signoff` |
| **Pulse** | (TBD) | (TBD) | `board_signoff` |
| **Guides** | (TBD) | (TBD) | `board_signoff` |
| **Rules** | (TBD) | (TBD) | `board_signoff` |

**Planned boards:**
| Board | Purpose | Header pattern | Signoff context |
| --- | --- | --- | --- |
| **Recruitment** | Open slots, talent needed, guild perks | `recruit` flavour | recruiting |
| **Roster** | All members, at-a-glance, swappable views | guild data | none |
| **Loot Queue** | Wishlist, priority, next in line | `loot` flavour | loot |
| **Announcements** | Sticky; GM-posted callouts | situational | none |

### Layer 3: Situational responses
Command replies, errors, and confirmations draw from banks but are composed ad-hoc. Examples:
- Admin command reply: `voice.line("thinking")` + work + result
- Member lookup fails: `voice.line("not_found")` + suggestion
- Recruitment nudge: `voice.flavour("recruiting")` + the ask
- Patch dropped: `voice.flavour("alert_critical")` + feed alert

---

## 2. Voice bank design rules

1. **No em dashes.** Enforced at render time via `brand.sanitize`.
2. **Situations, never people.** Snark targets wipes/patches/the contract, never learners or low parses.
3. **Short and dry.** A voice line is garnish, not the message.
4. **Domain pacing:**
   - M+ lines: **fast, excited, adrenaline** ("Go go go", "roaring", "hot pace")
   - Raid/logs lines: **measured, patient, methodical** ("steady burn", "patient work", "embers remember")
   - Default: **neutral, warm** ("mind the sparks", "crackling")
5. **Memory:** The `Voice` class tracks recent picks per bank, so the same line doesn't repeat immediately (configurable).

---

## 3. Board messaging patterns

### Pattern A: Data-driven board (Hot 20, Events, News)
```
HEADER (static or summary-based)
────────────────────────────────
[rows or items]
────────────────────────────────
SIGNOFF: "Tended by Calcifer. Mind the sparks."
TIMESTAMP: now (relative)
```

**What changes per board:**
- Header icon + text
- Row formatting
- Empty state (return `None` to post nothing, or return a payload with custom empty message)
- Signoff tone (M+ lines for leaderboard, neutral for news, etc.)

### Pattern B: Community board (Guides, Rules, Announcements)
```
HEADER (branded, fixed or GM-curated)
────────────────────────────────────
[content: markdown, links, sections]
────────────────────────────────────
SIGNOFF: "Kept warm by Calcifer."
```

**What changes:**
- Content is often hand-curated (JSON in `content/` directory)
- Render is mostly a pass-through with light formatting
- Signoff is neutral (not domain-specific)

### Pattern C: Recruitment board
```
HEADER: "🔎 We're looking"
────────────────────────────
OPEN SLOTS: [role summaries]
WHAT WE VALUE: [copy]
────────────────────────────
SIGNOFF: voice.flavour("recruiting") or "Come stake your claim, <@...>"
```

**Rules:**
- Signoff from `recruiting` bank (excited, direct)
- Content emphasizes guild culture (from CLAUDE.md)
- Call-to-action is clear (Discord link or DM officer)

---

## 4. Quick reference — what lines exist where

**Structural (always return):**
- `thinking` — query in progress
- `not_found` — member lookup failed
- `denied` — permission denied
- `empty` — no data to show
- `board_signoff` — board footer

**Flavour (tone-gated):**
- `mplus` — Mythic+ domain
- `raid` — raid progression
- `logs` — WCL analysis
- `recruiting` (new) — recruitment context
- `loot` (new) — loot context
- `attendance` (new) — signup context
- `alert_warning` (new) — warning/attention needed
- `alert_critical` (new) — urgent action
- `success` (new) — celebrate
- `error` (new) — something broke

**Empty-state variants:**
- `empty_recruiting` (new) — no open slots
- `empty_signups` (new) — events with thin signup

---

## 5. Tone dial and config

`Config.persona_tone` controls all Voice instances:
- `off`: no flavour lines ever
- `light`: 50/50 chance on flavour (current default)
- `full`: all flavour lines shown

This is a single dial for the whole server, not per-user. Keep it simple.

---

## 6. Checklist for adding a new line bank

When you define a new bank (e.g., `recruiting`):

1. [ ] Add the key + list to `_BANKS` in `brand.py`
2. [ ] Ensure no em dashes (linting in CI)
3. [ ] Lines are short (< 60 chars typically)
4. [ ] Lines are unique per bank (no duplication across keys)
5. [ ] Classify as structural or flavour (comment in code)
6. [ ] Write a test in `test_brand.py` if it's new
7. [ ] Document the key here and link it to where it's used

---

## 7. Examples — how surfaces use the banks

### Board footer (all boards)
```python
signoff = brand.Voice(tone=config.persona_tone).line("board_signoff")
embed.set_footer(text=signoff)
```

### M+ leaderboard (hot20.py)
```python
# Already uses: header = "🔥 Topping the embers", climb callout
# Could add: voice.flavour("mplus") in footer or header
```

### Recruitment board (planned)
```python
voice = brand.Voice(tone=config.persona_tone)
recruitment_callout = voice.flavour("recruiting")  # "Come stake your claim"
# Format: "🔎 [open slots] ... [callout] ... Calcifer"
```

### Admin command (e.g., /admin sync)
```python
await ctx.defer(thinking=voice.line("thinking"))
# ... do work ...
return f"{result}. {voice.flavour('success') or ''}"
```

---

## 8. What's next (after this round)

Once the voice banks are built:
- [ ] Board messaging spec per board (headers, empty, signoff patterns)
- [ ] Recruitment board design + render
- [ ] Command messaging guidelines (how cogs use voice)
- [ ] Mood-per-message system (author icon expressions for boards)
- [ ] Digest modes for feeds (weekly roundup flavor)

