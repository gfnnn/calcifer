# DECISIONS — Locked Decisions & Open Questions

> A tracking doc for decisions already made and decisions still open. Updated as consensus solidifies.

---

## Locked Decisions (What We've Decided)

These are load-bearing architectural choices. Revisit only if the assumption changes.

### 1. One Voice, Never Em Dashes

**Decision:** Calcifer always sounds like one person. No em dashes in anything he authors.

**Why:** Brands the bot; prevents the machine-written feeling. Enforced by `brand.sanitize()` at render time.

**Status:** ✅ Locked. Tested in CI.

**Where it matters:** Every board header, footer, and feed alert Calcifer writes.

---

### 2. Living Boards, Not Re-Posts

**Decision:** Boards edit in place (diff by fingerprint, never re-post). Never ping unless explicitly routed.

**Why:** Keeps channels tidy. Reduces rate-limit pressure. Edits feel like maintenance, not spam.

**Status:** ✅ Locked. Implemented in `boards/engine.py`.

**Where it matters:** All board updates, feed alert routing.

---

### 3. Deny By Default (The Posting Guardrail)

**Decision:** Nothing reaches a real channel until a human promoted it. `POSTING_MODE=off|test|live`.

**Why:** Prevents accidental server-wide blasts during development.

**Status:** ✅ Locked. Enforced in `posting.py`.

**Where it matters:** Every surface that posts to Discord.

---

### 4. Async Throughout, No Blocking I/O

**Decision:** All data access is async (`aiosqlite`). Services are async (`aiohttp`). No threads.

**Why:** Simple, composable, scales to 300 members without resource overhead.

**Status:** ✅ Locked. Codebase pattern.

**Where it matters:** All services, database, bot, web server.

---

### 5. One Database, Many Surfaces

**Decision:** All three surfaces (Discord bot, web, sync CLI) read/write the same SQLite file.

**Why:** Single source of truth. Sync is write-only; bot and web are read-only (+ officer writes).

**Status:** ✅ Locked. Implemented in `db.py`.

**Where it matters:** Data schema, migrations, sync atomicity.

---

### 6. Config, Not Code

**Decision:** Every feature toggles via `Config` (read from environment). No hardcoded values beyond defaults.

**Why:** Deploys work the same way everywhere. Feature flags don't require code changes.

**Status:** ✅ Locked. Enforced by `config.py` design.

**Where it matters:** All features, all integrations.

---

### 7. Offline-Testable Renderers

**Decision:** Every renderer (board, feed, report) is pure `(db, config) → payload`. No I/O. Tested offline with temp SQLite.

**Why:** Catches logic bugs before they hit Discord. Renderers are reusable.

**Status:** ✅ Locked. Pattern in `boards/*`, `services/`

**Where it matters:** Board specs, feed publishers, command replies.

---

### 8. Serverless-Ready for Future

**Decision:** The codebase is structured so it could run on serverless (Cloud Functions, Lambda) with minimal changes. Long-running loops are deliberate and documented.

**Why:** Future deployment flexibility.

**Status:** ✅ Locked. Conscious design. (`serve.py` lists the loops; web is stateless.)

**Where it matters:** Task lifecycle, database connections, webhook handling.

---

### 9. Identity Graph: Character → Account → Discord User

**Decision:** The data spine answers "who is this person?" by linking character (WoW) → account (RIO/Audit) → Discord user.

**Why:** Enables member-aware features (alt detection, preferences, roles).

**Status:** 🟡 Partial. Intake (SavedVariables, GRM, RCLootCouncil) shipped. Character↔Discord linking planned.

**Where it matters:** `identity.py`, alt grouping, roster views.

---

### 10. Modular Feeds, Not Monolithic Integrations

**Decision:** Each external data source (Wowhead, Raider.IO, WCL, Raid-Helper, etc.) is a separate `FeedSource`. Routing is generic (source + event type + rule).

**Why:** Adding a new feed doesn't require a new cog; just a new source + routing rules.

**Status:** ✅ Locked. Implemented in `feeds/`, `router.py`.

**Where it matters:** Feed scalability, operator control.

---

## Open Questions (Decisions Pending)

These are on the table. Once consensus solidifies, they move to "Locked."

### Q1: Prog-Team Gating — Channel-Based or Code-Level?

**Question:** How should the prog team (Team Chemistry) see more detail than the wider guild?

**Option A: Channel + Routing (Recommended)**
- Set up a read-restricted `#prog` channel (only Team Chemistry can read)
- Route the detailed WCL prog feed to `#prog`; route the light version ("💀 Boss down!") to `#events`
- Cheap, works today, no code tier needed

**Option B: Code-Level Gating**
- Check `raid_team` field in commands and boards
- Filter views based on membership
- More precise, more work

**Recommendation:** Channel + routing first. Simple, immediate, works with existing features.

**Status:** 📋 Pending team decision. Implement Option A in Phase 2 or 3.

**Owner:** (TBD)

---

### Q2: Management Views — Public or Officer-Gated?

**Question:** Should `/gmreport` and `/raidcheck` stay public to the whole guild, or be officer-only?

**Option A: Keep Public**
- Transparency: anyone can see guild health
- Downside: not really a "management" tool if everyone pulls it

**Option B: Officer-Gate + Ephemeral**
- Respects Pillar B (management is pull-when-needed, not ambient)
- More private workflow

**Recommendation:** Officer-gate them. They're Pillar B tools; access should match intent.

**Status:** 📋 Pending decision. Add `require_admin()` check.

**Owner:** (TBD)

---

### Q3: Harry's Register — Ship Now or Later?

**Question:** Should Calcifer recognize the GM (`GM_DISCORD_ID`) and use a special register (familiar, contract-bound)?

**Option A: Ship Now**
- Add the check: `if user.id == config.gm_discord_id`, use a special `harry_voice_context`
- Expand `brand.py` with a `harry_*` line bank
- Small change, high charm

**Option B: Defer**
- It's pure flavor, no function
- Wait until foundational tiers are solid

**Recommendation:** Defer to Phase 4 (Charm). Functional features first.

**Status:** 📋 Deferred to Phase 4.

**Owner:** (TBD)

---

### Q4: Notice-Board Channel(s) — One or Many?

**Question:** Should Rules, Guides, News, Recruitment live in one `#info` channel or split?

**Option A: One `#info` Channel**
- Tidy, consolidated
- Downside: less visible individually, smaller landing zone for visitors

**Option B: Split by Purpose**
- `#rules` (house values)
- `#guides` (resources)
- `#news` (external info)
- `#recruitment` (hiring + culture, the guild's front door)
- Clearer purpose, but more channels

**Recommendation:** Lean towards split, with `#recruitment` as a dedicated front door (visitors land here first).

**Status:** 📋 Pending team decision on channel structure. See `channel-architecture.md`.

**Owner:** (TBD)

---

### Q5: Canonical Channel Layout — Write It Down?

**Question:** Should we formalize the channel structure (categories, permissions, board homes)?

**Current state:** `channel-architecture.md` describes it; not all channels are set up yet.

**What we need:** A diagram or explicit checklist that maps each board/feed to its home channel and explains the permission model.

**Recommendation:** Yes. Create a checklist in the next consolidation pass.

**Status:** 📋 Pending. Spec exists, implementation needs validation.

**Owner:** (TBD)

---

### Q6: Flamekeeper Role — Single Admin Role or Tiered?

**Question:** Do we stick with a single `admin_role_name` ("Officer") or add tiers (Officer, Flamekeeper, etc.)?

**Option A: Single Role**
- Simple, works today
- No differentiation in what officers can do

**Option B: Tiered Access**
- `Officer` = basic admin (link, sync, board manage)
- `Flamekeeper` = advanced (recruitment, feeds, server config)
- Aligns with vision §2 (Flamekeepers are the castle's lieutenants)
- More work

**Recommendation:** Single role for now (simpler). Tier it later if needed.

**Status:** 📋 Pending. Simple default works; upgrade later if needed.

**Owner:** (TBD)

---

### Q7: Character↔Discord Linking — Auto or Manual?

**Question:** How should characters be linked to Discord users?

**Option A: Manual `/link` Command**
- Officer or user runs `/link character @user`
- Simple, explicit, no guessing
- Downside: requires manual work at scale

**Option B: Auto from GRM**
- GRM includes Discord handles in guild notes; parse and auto-link
- Faster, less manual work
- Downside: depends on guild keeping GRM notes accurate

**Recommendation:** Start with `/link` command (works today). Consider GRM hints later (Phase 2/3).

**Status:** 📋 Pending implementation. `/link` works; GRM auto-link is a nice-to-have.

**Owner:** (TBD)

---

## How to Update This Doc

1. **When a decision solidifies:** Move it from "Open Questions" to "Locked Decisions" with status ✅.
2. **When a question arises:** Add it to "Open Questions" with the context, options, recommendation, and status.
3. **When blocked:** Flag the owner so it's clear who decides.

---

## Where This Fits

- **Vision & strategy:** [`vision.md`](vision.md)
- **Feature planning:** [`ROADMAP.md`](ROADMAP.md)
- **Communication model:** [`communication-model.md`](communication-model.md)
- **Channel layout:** [`channel-architecture.md`](channel-architecture.md)

---

## Review Schedule

These decisions should be revisited:
- **Q1–Q3:** At the start of each phase (before building)
- **Q4–Q7:** When the feature set feels solid (mid-Phase 2 or Phase 3)
- **All:** If assumptions change (e.g., guild size, feature priority, deployment constraints)

