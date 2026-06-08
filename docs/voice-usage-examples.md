# Voice System Usage — code examples

How to use `brand.Voice` across the codebase: boards, commands, feeds, errors.

---

## Pattern 1: Board footer (all boards)

**Goal:** Add a signoff to any board embed.

```python
from ftabot import brand

# In your board renderer:
voice = brand.Voice(tone=config.persona_tone)
signoff = voice.line("board_signoff")  # Always returns something

embed = discord.Embed(
    title="Your Board",
    description="Content here",
    color=brand.GUILD_EMBER,
)
embed.set_footer(text=signoff)
```

**Result:**
- Tone off: "Calcifer, keeping the hearth lit."
- Tone light: (random from `board_signoff` bank)
- Tone full: (random from `board_signoff` bank)

---

## Pattern 2: Board with domain flavor (Hot 20 example)

**Goal:** Add context-aware flavor to a leaderboard.

```python
from ftabot import brand

voice = brand.Voice(tone=config.persona_tone)

# Header: top leader + optional M+ flavor
leader = ranked[0]
flavor = voice.flavour("mplus")  # None if tone=off, optional if tone=light, always if tone=full
header_parts = [
    f"🔥 Topping the embers: **{leader.character_name}** at `{score}`."
]
if flavor:
    header_parts.append(flavor)  # "The fire's roaring tonight."
header = " ".join(header_parts)

# Or, if you want to keep it simple:
header = f"🔥 Topping the embers: **{leader.character_name}** at `{score}`."
signoff = voice.line("board_signoff")
```

---

## Pattern 3: Empty state with flavor (Recruitment board)

**Goal:** Show a message when the recruitment board has no open slots.

```python
from ftabot import brand

voice = brand.Voice(tone=config.persona_tone)

open_slots = await db.get_recruitment_slots()
if not open_slots:
    # Use recruitment-specific empty state
    empty_msg = voice.line("empty_recruiting")  # Always returns something
    flavor = voice.flavour("recruiting")  # Optional
    
    payload = f"{empty_msg} {flavor}" if flavor else empty_msg
    embed.description = payload
else:
    # Regular content
    embed.description = format_slots(open_slots)
```

**Result (tone=light):**
- "The guild's full. For now. Come stake your claim." (both lines)
- "The guild's full. For now." (empty only)
- (50/50 chance of each)

---

## Pattern 4: Command thinking state

**Goal:** Show "thinking" while a command runs.

```python
from ftabot import brand

class MyCommand(commands.Cog):
    @commands.slash_command()
    async def scout(self, ctx: discord.ApplicationContext):
        voice = brand.Voice(tone=self.bot.config.persona_tone)
        
        # Show thinking immediately
        await ctx.defer(thinking=voice.line("thinking"))
        
        # Do the work
        result = await scout_members(...)
        
        # Reply with result + optional flavor
        flavor = voice.flavour("recruiting")
        reply = f"Found {len(result)} candidates. {flavor or ''}"
        await ctx.respond(reply)
```

**Result:**
- Defer: "Working. The things I do for this contract."
- Reply: "Found 3 candidates. Come stake your claim." (if tone=full)
- Reply: "Found 3 candidates." (if tone=off)

---

## Pattern 5: Error handling

**Goal:** Show a Calcifer-branded error.

```python
from ftabot import brand
import discord

try:
    data = await fetch_api(...)
except Exception as e:
    voice = brand.Voice(tone=config.persona_tone)
    error_line = voice.line("error")  # Structural, always returns
    embed = discord.Embed(
        title="Something went wrong",
        description=f"{error_line}\n\nDetails: {e}",
        color=brand.EMBER_RED,
    )
    await ctx.respond(embed=embed)
```

**Result:**
- "The flames flicker. Something's not right.\n\nDetails: Connection timeout"

---

## Pattern 6: Success celebration

**Goal:** Celebrate a win (first kill, new rank, etc.).

```python
from ftabot import brand

voice = brand.Voice(tone=config.persona_tone)

# Combat log: new first kill
kill_flavor = voice.flavour("success")  # Optional
if kill_flavor:
    message = f"💀 **[Boss]** falls! {kill_flavor}"
else:
    message = f"💀 **[Boss]** falls!"

# Or loot distribution
flavor = voice.flavour("loot")
await announce(f"Loot awarded. {flavor or 'Fair share to those who earn it.'}")
```

**Result:**
- "💀 **Heroic Broodkeeper** falls! Well earned." (if tone=full)
- "💀 **Heroic Broodkeeper** falls!" (if tone=off/light with no luck)

---

## Pattern 7: Recruitment push (situational)

**Goal:** In the recruitment board, include a CTA with flavor.

```python
from ftabot import brand

voice = brand.Voice(tone=config.persona_tone)

# Build recruitment board
open_slots = format_open_slots(...)
culture = load_guild_culture()  # from content/recruitment.json

cta = voice.flavour("recruiting")  # Optional
cta_text = cta or voice.line("board_signoff")  # Fallback

embed.description = f"""
{open_slots}

**Who we are:**
{culture}

{cta_text}
"""
```

---

## Pattern 8: Feed alert with tone (Feed example)

**Goal:** Add flavor to a feed alert (e.g., Wowhead news).

```python
from ftabot import brand

# In publisher.render_embed() or similar:
voice = brand.Voice(tone=config.persona_tone)

# For a Wowhead patch notes event:
flavor = voice.flavour("alert_critical")  # "The moment's here."

embed = discord.Embed(
    title="Patch [X.Y] Released",
    description="Patch notes now live.",
    color=brand.EMBER_RED if flavor else brand.BLUE_FLAME,
)

if flavor:
    embed.add_field(name=flavor, value="Check the details below.", inline=False)
```

---

## Pattern 9: Attendance warning (Pulse board)

**Goal:** Warn when signups are thin.

```python
from ftabot import brand

voice = brand.Voice(tone=config.persona_tone)

attendance_pct = calculate_attendance(...)
if attendance_pct < 70:
    warning = voice.flavour("attendance")  # Optional
    if warning:
        embed.add_field(
            name="Heads up",
            value=f"{warning} Attendance: {attendance_pct:.0f}%",
            inline=False
        )
```

**Result:**
- "Every hand at the hearth counts. Attendance: 65%"
- (Or no field at all if tone=off)

---

## Pattern 10: Multiple calls in one render (Hot 20 example)

**Goal:** Use voice multiple times in the same board without repeating lines.

```python
from ftabot import brand

voice = brand.Voice(tone=config.persona_tone, memory=3)

# Throughout the render:
header = voice.flavour("mplus")  # Pick 1
biggest_climber_callout = voice.flavour("mplus")  # Pick 2 (won't repeat pick 1)
footer = voice.line("board_signoff")  # Different bank, always returns
```

**Key:** `Voice.memory=N` (default 2) means the last N picks per bank are excluded from the next pick. This keeps the same bank from repeating immediately across one render.

---

## Pattern 11: Test example

```python
import pytest
from ftabot import brand

def test_voice_no_repeats():
    """Voice tracks recent picks and avoids immediate repeats."""
    voice = brand.Voice(
        banks={"test": ["A", "B", "C"]},
        rng=_rng,
        memory=2,
    )
    
    # Force a sequence
    _rng.choice = lambda x: x[0]  # Always pick first
    
    line1 = voice._pick("test")  # "A"
    line2 = voice._pick("test")  # "B" (A is in memory)
    line3 = voice._pick("test")  # "C" (A, B are in memory)
    line4 = voice._pick("test")  # "A" (B, C are in memory)
    
    assert [line1, line2, line3, line4] == ["A", "B", "C", "A"]


def test_no_em_dashes():
    """Brand lines never contain em dashes."""
    for bank, lines in brand._BANKS.items():
        for line in lines:
            assert "—" not in line, f"Em dash in {bank}: {line!r}"
```

---

## Pattern 12: Config-driven tone

```python
# In config.py (or environment):
class Config:
    persona_tone: str = "light"  # off, light, or full

# In a cog:
voice = brand.Voice(tone=self.bot.config.persona_tone)
```

**Why it matters:**
- Server admin can dial the personality up or down
- During crunch/serious times: set to `off` (no flavor)
- During fun peaks: set to `full` (maximum flavor)
- Default `light` hits the balance

---

## Quick reference: what returns what

| Call | Returns (tone=light) | Returns (tone=off) | Returns (tone=full) |
| --- | --- | --- | --- |
| `voice.line("thinking")` | Random from bank | Random from bank | Random from bank |
| `voice.flavour("mplus")` | Random or `None` (50/50) | `None` always | Random always |
| `voice.line("board_signoff")` | Random from bank | Random from bank | Random from bank |
| `voice.flavour("_default")` | Random or `None` (50/50) | `None` always | Random always |

---

## Checklist: using voice in new code

- [ ] Create a `Voice` instance: `voice = brand.Voice(tone=config.persona_tone)`
- [ ] Identify which voice bank to use (structural vs. flavour)
- [ ] Call `voice.line(key)` for structural, `voice.flavour(key)` for optional
- [ ] Check the result is not `None` before using (flavour can be `None`)
- [ ] No em dashes in the result (sanitize handles this, already applied in brand._pick)
- [ ] Test doesn't assume a specific line (lines are random)

---

## When NOT to use voice

- **Relayed external text** (Wowhead headlines, WCL titles) — show as-is, don't run through voice
- **User-generated content** (guild notes, alt names) — show as-is
- **Data values** (scores, dates, times) — plain numbers
- **Error stack traces** — raw, for debugging

Use voice for:
- Structural responses (thinking, denied, not found)
- Board headers/footers
- Command confirmations
- Alerts and callouts
- The "glue" that makes the bot feel like a person

