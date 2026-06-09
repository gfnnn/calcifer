# Calcifer's Messaging Strategy — voice, boards, and interactions

This document describes how Calcifer's voice is structured, where it appears, and how to expand it coherently as the bot grows.

## 1. Three layers of messaging

### Layer 1: Voice banks (`brand.py`)
The raw material — context-keyed lines that express Calcifer's personality. Every surface (boards, commands, feeds) draws from these via the `Voice` class.

**Structure:**
- **Structural lines** — always return something (e.g., "thinking", "not_found", "denied"). These are required.
- **Flavour lines** — optional garnish, tone-gated (off/light/full). These disappear in `off` mode, are 50/50 in `light`, always show in `full`.
- **Domain flavour** — paced to the content: M+ is fast/excited, raids/logs are measured/patient.

**See [`MESSAGING.md`](MESSAGING.md) for the complete bank reference and how to add new lines.**

### Layer 2: Board-specific messaging
Each board has its own **header**, **empty state**, and **signoff**, drawing from the voice banks but customized per board type. **See [`sticky-posts-spec.md`](sticky-posts-spec.md) for comprehensive board specifications** (live boards, planned boards, detailed copy templates, voice patterns).

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

See **[`MESSAGING.md`](../MESSAGING.md)** for the current bank reference (structural lines, flavour lines, empty-state variants, and examples).

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

## 7. Code examples

See **[`voice-usage-examples.md`](voice-usage-examples.md)** for working patterns:
- Board footers and headers
- Domain-specific flavour
- Empty states and error handling
- Command thinking/success flows
- Testing voice integration

---

## 8. Related references

- **[`MESSAGING.md`](../MESSAGING.md)** — Quick-start guide for working with voice
- **[`sticky-posts-spec.md`](sticky-posts-spec.md)** — Complete board specifications with copy templates
- **[`voice-usage-examples.md`](voice-usage-examples.md)** — Working code patterns for integrating voice into new features
- **[`board-messaging-spec.md`](board-messaging-spec.md)** — Legacy reference (see sticky-posts-spec.md for current specs)

