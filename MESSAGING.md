# Calcifer's Messaging System — quick start guide

This is the entry point for building and maintaining Calcifer's voice across Discord: what he says, how he says it, and where it lives in code.

## One-minute overview

Calcifer speaks through **voice banks** — organized collections of copy that express his personality. Every board, command, alert, and error draws from these banks, so he sounds like one person across the whole bot, not a collection of templates.

```
voice = brand.Voice(tone=config.persona_tone)
```

That's it. A `Voice` instance picks lines from the banks, avoids repeating immediately, and gates flavour on the tone dial.

---

## The system in three files

### 1. `ftabot/brand.py` — the voice banks
Where all Calcifer's lines live: 16 banks for different contexts (thinking, recruiting, loot, success, error, etc.).

**What to do here:**
- Add new lines to banks (always short, no em dashes)
- Create new banks when a new context appears (e.g., "duos" for M+ partners)
- Test that lines are unique, em-dash-free, and make sense per context

**Key classes:**
- `Voice` — picks lines with no-immediate-repeat memory
- `brand.color_for(severity)` — colour system (GUILD_EMBER, BLUE_FLAME, AMBER, EMBER_RED)
- `brand.Mood` + `mood_emoji()` — Calcifer's expressions (thinking, smug, grumpy, delighted, etc.)

### 2. `docs/messaging-strategy.md` — the architecture
How the system is designed: what the banks are, why they're structured this way, phases for building it out.

**Read this if:**
- You're adding a new board or feature
- You want to understand the tone dial or memory system
- You're designing a new surface (command, feed, etc.)

### 3. `docs/board-messaging-spec.md` — the board templates
Detailed specs for every board: what header to use, which voice bank to draw from, how to handle empty states, footer patterns.

**Read this if:**
- You're building a board renderer
- You need to know what copy a board should have
- You're deciding between "show nothing" vs. "show a custom empty state"

**Bonus:** `docs/voice-usage-examples.md` has working code examples for every pattern.

---

## How to use it in code

**Pattern: Board footer**
```python
from ftabot import brand

voice = brand.Voice(tone=config.persona_tone)
signoff = voice.line("board_signoff")  # Always returns something
embed.set_footer(text=signoff)
```

**Pattern: Optional flavour**
```python
# Might return a line, might return None (depends on tone)
flavor = voice.flavour("mplus")  
if flavor:
    content += f" {flavor}"
```

**Pattern: Command thinking state**
```python
await ctx.defer(thinking=voice.line("thinking"))
# ... do work ...
await ctx.respond(f"Done. {voice.flavour('success') or ''}")
```

More examples in `docs/voice-usage-examples.md`.

---

## The voice banks (what exists now)

### Structural (always return something)
- `thinking` — query in progress ("Hold on, stoking the embers...")
- `not_found` — member not found ("No such name in my ashes.")
- `denied` — permission denied ("Officers only.")
- `empty` — no data to show ("Nothing to show. The grate's cold here.")
- `board_signoff` — board footer ("Calcifer, keeping the hearth lit.")
- `error` — something broke ("The flames flicker. Something's not right.")

### Flavour (tone-gated, may return None)

**Domain context** (paced to the content):
- `mplus` — Mythic+ voice (fast, excited)
- `raid` — Raid voice (patient, measured)
- `logs` — WCL/stats voice (analytical)

**Situational:**
- `recruiting` — recruitment/talent ("Come stake your claim.")
- `loot` — loot distribution ("Fair share to those who earn it.")
- `attendance` — signups/turnout ("Every hand at the hearth counts.")
- `alert_warning` — needs attention ("Things are getting thin.")
- `alert_critical` — urgent ("The moment's here.")
- `success` — celebrate a win ("Well earned.")
- `error` — breakage ("My sight's clouded. Try again?")

**Empty states:**
- `empty_recruiting` — no open slots ("The guild's full. For now.")
- `empty_signups` — no takers yet ("The bench is waiting.")

---

## Board specs (what voice to use where)

| Board | Signoff bank | Optional flavor | Notes |
| --- | --- | --- | --- |
| **Hot 20** | `board_signoff` | `mplus` in header | M+ leaderboard, fast context |
| **Events** | `board_signoff` | none | Raid-Helper calendar, neutral |
| **News** | `board_signoff` | none | Wowhead relayed text, neutral |
| **Guides** | `board_signoff` | none | Hand-curated reference, neutral |
| **Rules** | `board_signoff` | none | House rules, no flavor |
| **Pulse** | `board_signoff` | `success` if healthy | Guild health, measured |
| **Recruitment** | recruiting flavor | always | Direct CTA, exciting |
| **Announcements** | situational | always | Varied per announcement type |

---

## How to add a new board

1. **Identify the voice context.** Does it fit an existing bank (mplus, raid, recruiting, loot)? Or do you need a new one?

2. **Add to board-messaging-spec.md.** Define the header, empty state, and signoff pattern in the spec.

3. **In your renderer:**
   ```python
   from ftabot import brand
   
   voice = brand.Voice(tone=config.persona_tone)
   embed.title = "🔎 We're looking"  # Icon + title
   flavor = voice.flavour("recruiting")  # Context-specific
   embed.set_footer(text=flavor or voice.line("board_signoff"))
   ```

4. **Test it.** Check tone=off and tone=full to see the span from silent to chatty.

---

## How to add a new voice bank

When a new context appears (e.g., "mythic_dungeons_2v2" for a new feature):

1. **Add to `brand.py`:**
   ```python
   "mythic_duos": [
       "Two's a team.",
       "Double trouble, double glory.",
   ],
   ```

2. **Classify it:** Is it structural (always return) or flavour (tone-gated)?
   - Structural: it's answering a question (thinking, not_found, etc.)
   - Flavour: it's optional garnish (mplus, recruiting, etc.)

3. **Write 3–5 short lines.** Keep each under 60 characters. No em dashes.

4. **Test:** `pytest tests/test_brand.py` (ensures no em dashes, lines are unique)

5. **Document in this file + messaging-strategy.md.** Add the bank to the reference table.

---

## The tone dial

Every Calcifer instance respects `Config.persona_tone`:

- `off` — no flavour lines ever (serious mode)
- `light` — 50/50 on flavour (the default, balanced)
- `full` — all flavour lines shown (chatty mode)

Structural lines always return, even in `off` mode. It's just flavour that gates.

**Set it via environment:** `PERSONA_TONE=off` (or light/full)

---

## Testing voice lines

```python
def test_brand_no_em_dashes():
    """All lines are em-dash-free."""
    for bank, lines in brand._BANKS.items():
        for line in lines:
            assert "—" not in line, f"Em dash in {bank}: {line!r}"

def test_voice_picks_from_banks():
    """Voice picks lines from the banks."""
    voice = brand.Voice()
    line = voice.line("thinking")
    assert line in brand._BANKS["thinking"]
```

See `tests/test_brand.py` for the full suite.

---

## What's not in voice

- **Relayed external text** (Wowhead headlines, WCL titles) — shown as-is
- **User-generated content** (guild notes, member bios) — shown as-is
- **Data values** (scores, dates, ilvl) — plain numbers
- **Error stack traces** — for debugging, raw

---

## Next steps

Once the voice system is solid:

1. **Flesh out existing boards** — update hot20, events, news to use the new banks
2. **Build new boards** — recruitment, roster, loot queue, announcements
3. **Command messaging** — standardize how slash commands use voice (thinking → work → result)
4. **Mood system** — add author-icon expressions to key boards (Calcifer thinking, smug, delighted, etc.)
5. **Digest modes** — weekly roundup flavour for feeds

---

## Quick reference — what's where

| Need | File | What's there |
| --- | --- | --- |
| Voice banks | `ftabot/brand.py` | 16 banks + `Voice` class + colour system |
| Board specs | `docs/board-messaging-spec.md` | Header/footer/empty patterns for each board |
| Strategy | `docs/messaging-strategy.md` | Architecture, phases, design rules |
| Code examples | `docs/voice-usage-examples.md` | Worked examples for every pattern |
| Tests | `tests/test_brand.py` | Ensure lines are unique, no em dashes |

---

## One more thing

**Read the vision.** If you haven't, read `docs/vision.md` §2 (Calcifer the persona) and §6 (the design system). That's where the rules live:
- Snark targets situations, never people
- He never writes em dashes
- M+ is fast/excited, raid/logs are measured
- He runs the castle in Harry's name, grumbling but proud

Everything in this system flows from those anchors.

