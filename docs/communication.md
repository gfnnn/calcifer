# Calcifer's communication palette — every way the bot speaks in Discord

This is the reference for the **full toolbox Calcifer has for communicating in
Discord** — text markdown, dynamic timestamps, mentions, emoji, embeds,
Components V2, and the message limits that bound them all. The bot lives in
Discord (vision [Pillar A](vision.md#4-pillar-a--the-guilds-living-home)), so
knowing the whole palette — and the house rules that constrain it — is what lets
every surface "read like a designed page, not a bot's scratchpad"
([vision §6](vision.md#6-the-design-system--look--voice)).

It pairs with [`vision.md §6`](vision.md#6-the-design-system--look--voice) (the
look-and-voice design system) and the code in
[`ftabot/brand.py`](../ftabot/brand.py) (the single source of truth for colour,
icon, and voice). **`brand.py` owns *what* Calcifer sounds like; this doc is the
catalogue of *how* a message can be shaped.** Where a capability is already wired
into the code it is cited; where it is not yet, it is flagged so the gap is
visible.

> **Source:** Discord's
> [Markdown Text 101](https://support.discord.com/hc/en-us/articles/210298617-Markdown-Text-101-Chat-Formatting-Bold-Italic-Underline)
> plus the [developer docs](https://discord.com/developers/docs) (message
> formatting, timestamps, components). Mirror used for the markdown table:
> [matthewzring's gist](https://gist.github.com/matthewzring/9f7bbfd102003963f9be7dbcf7d40e51).

---

## 0. The house rules — read these first

Every capability below is subordinate to the standing constraints in
[vision §3](vision.md#3-how-we-build-it--architecture--approach) and §6. Reach
for a feature, then check it against these:

| Rule | What it means for a message | Enforced by |
| --- | --- | --- |
| **One voice, never an em dash** | Anything Calcifer *authors* is run through `brand.sanitize`; *relayed* external text (a Wowhead headline) is left untouched. | [`brand.sanitize`](../ftabot/brand.py) + tests; applied in [`publisher.render_embed`](../ftabot/publisher.py) |
| **Boards never ping** | Every board send/edit forces `allowed_mentions=none`. | [`boards/messenger.py`](../ftabot/boards/messenger.py) (`_NO_PINGS`) |
| **Route through the gate** | No surface calls `channel.send` directly; destination resolves via `PostGate` first. | [`posting.py`](../ftabot/posting.py); see [vision §3](vision.md#3-how-we-build-it--architecture--approach) |
| **Times are dynamic, never hardcoded** | Every displayed time is a `<t:…>` stamp on a Paris-time epoch, so it localises per viewer. Never write "20:15 CET". | `Config.raid_timezone`; see [§2](#2-dynamic-timestamps--t) |
| **Mobile-first** | Most of the ~300 are on phones. Favour short lines; remember code-block syntax highlighting does **not** render on mobile. | design choice; see [§1](#1-text-markdown) |
| **Edit, never re-post** | Boards diff by fingerprint and edit in place. | [`boards/engine.py`](../ftabot/boards/engine.py) |
| **Snark targets situations, never people** | A curation guideline for any authored copy. | curation + [vision §2](vision.md#2-calcifer--the-persona-and-the-bond) |

---

## 1. Text markdown

The chat-formatting layer. Works in message content, embed titles/descriptions,
and most component text. Combine freely (e.g. `__***text***__`).

| Style | Syntax | Notes |
| --- | --- | --- |
| Bold | `**text**` | |
| Italic | `*text*` or `_text_` | |
| Bold italic | `***text***` | |
| Underline | `__text__` | |
| Underline + bold + italic | `__***text***__` | styles nest |
| Strikethrough | `~~text~~` | |
| Spoiler | `\|\|text\|\|` | hidden until clicked; also works on attachments |
| Inline code | `` `text` `` | escapes all other markdown inside |
| Code block | ```` ```text``` ```` | monospace, preserves whitespace |
| Syntax-highlighted block | ```` ```python ```` then code then ```` ``` ```` | **desktop/web only — no highlight on mobile.** Langs: `bash`, `python`, `json`, `js`, `cpp`, `cs`, `css`, `diff`, `ini`, `md`, `xml`, `yaml`, `sql`, … (20+) |
| Header 1 / 2 / 3 | `# `, `## `, `### ` at line start | H1–H3 only; needs the trailing space |
| Subtext | `-# text` | small, muted; good for footnotes/sign-offs |
| Block quote (one line) | `> text` | |
| Block quote (rest of message) | `>>> text` | everything after is quoted |
| Masked link | `[display](https://url)` | renders in embed bodies and app/webhook message content; **not** in plain user DMs |
| Unordered list | `- item` or `* item` | indent (2 spaces) to nest |
| Ordered list | `1. item` | numbers needn't be sequential |
| Escape | `\` before the character, or wrap in inline code | e.g. `\*not italic\*` |

**Where we already use it:** the [Hot 20 board](../ftabot/boards/hot20.py) builds
each row with inline code for rank/score and bold for names
(`` `{i:>2}` … **{name}** · {class} · `{score}` ``); the
[events board](../ftabot/boards/events.py) uses masked links for event titles.

**Gotchas**
- Inline code is the reliable way to show a literal `<@id>` or `||` without it
  being interpreted.
- `@everyone`/`@here` and mentions inside code blocks still *render* as text but
  do **not** ping — but don't rely on that; gate pings with `allowed_mentions`
  ([§3](#3-mentions--allowed_mentions)).
- Markdown does **not** work inside embed *field names* the same as bodies in all
  clients; keep field names plain.

---

## 2. Dynamic timestamps — `<t:…>`

The single most important formatting feature for a guild bot, because the server
runs on **Europe/Paris** but the 300 members are scattered across timezones. A
`<t:UNIX:STYLE>` token renders in **each viewer's own local time** automatically.
**Never hardcode a wall-clock time** ([vision §3](vision.md#3-how-we-build-it--architecture--approach)).

Syntax: `<t:UNIX_SECONDS:STYLE>` — the epoch is computed on Paris time via
`Config.raid_timezone`, but the token itself is timezone-agnostic.

| Style | Example output | Use for |
| --- | --- | --- |
| `t` | `16:20` | short time |
| `T` | `16:20:30` | long time |
| `d` | `20/04/2026` | short date |
| `D` | `20 April 2026` | long date |
| `f` *(default)* | `20 April 2026 16:20` | date + time |
| `F` | `Tuesday, 20 April 2026 16:20` | full date + time |
| `R` | `in 3 hours` / `2 days ago` | relative, self-updating |

**Where we already use it:** the events board pairs an absolute stamp with a
relative one — `` <t:{ts}:F> (<t:{ts}:R>) `` — so a member sees both "Tuesday
20:15 *their* time" and "in 3 hours"
([`boards/events.py:38`](../ftabot/boards/events.py)).

> discord.py helper: `discord.utils.format_dt(dt, style="R")` builds the token
> from a `datetime`. We currently hand-build the string from an epoch; either is
> fine as long as the epoch came from Paris time.

---

## 3. Mentions & `allowed_mentions`

Mentions are *rendered* by syntax, but whether they actually **ping** is a
separate decision controlled by `allowed_mentions`. The two are independent — you
can show a name in blue without notifying anyone.

| Mention | Syntax |
| --- | --- |
| User | `<@USER_ID>` |
| Channel | `<#CHANNEL_ID>` |
| Role | `<@&ROLE_ID>` |
| Everyone / here | `@everyone` / `@here` |
| Slash command (clickable) | `</name:COMMAND_ID>` |
| Custom emoji | `<:name:ID>` / animated `<a:name:ID>` |

**The ping policy is deliberate and opt-in** (vision: *"opt-in, never
opt-out"*). Loud notifications are a subscription
([`/alerts` roles, planned](vision.md#4d-personal--toggleable)), not a default.

- **Boards never ping.** [`boards/messenger.py`](../ftabot/boards/messenger.py)
  forces `discord.AllowedMentions.none()` on every send *and* edit.
- **Feed alerts** ping only when a rule's style explicitly opts in (a role-ping
  per source), routed through the gate.
- When in doubt, pass `allowed_mentions=discord.AllowedMentions.none()` and let
  the text mention render without a notification.

```python
# Render a role name without pinging it:
await channel.send(f"Heads up, <@&{role_id}>",
                   allowed_mentions=discord.AllowedMentions.none())
```

---

## 4. Emoji

Three layers, all keyed through [`brand.py`](../ftabot/brand.py) so the icon
vocabulary stays fixed ([vision §6](vision.md#6-the-design-system--look--voice)).

| Layer | What | Resolution |
| --- | --- | --- |
| **Unicode** | The fixed glyph vocabulary (🔑 M+, 💀 kill, 🔥 progress, 📅 events, 🛡️/💚/⚔️ roles) | `brand.icon(key)` / `brand.ICONS` — the always-available fallback |
| **Custom guild emoji** | `:fta_*:` glyphs, `:calcifer_*:` moods, `:class_*:` crests, role crests | `brand.emoji_name(key)` / `mood_emoji_name` / `class_emoji_name` give the `:name:`; the cog/board layer resolves it from the guild at runtime and overrides the unicode fallback |
| **Application emoji** | App-owned emoji (no guild slot needed) | not used yet; an option if guild emoji slots run short |

- Custom emoji are sent as `<:name:id>` (static) or `<a:name:id>` (animated). The
  runtime resolves the id by looking up the name in the guild's emoji list, so
  **nothing blocks on the upload** — an un-uploaded emoji just keeps its unicode
  glyph.
- The mood system (`brand.Mood` + `MOOD_EMOJI`) expresses how Calcifer "feels"
  per message via author-icon + prefix emoji ([vision §6, charm pass](vision.md#6-the-design-system--look--voice)).

---

## 5. Embeds

The workhorse for alerts and command replies: a bordered, coloured panel with
structured fields. **Colour encodes source + urgency** — one accent per panel,
never rainbow ([vision §6](vision.md#6-the-design-system--look--voice)):

| Token | Hex | Means |
| --- | --- | --- |
| `GUILD_EMBER` | `0xCC4422` | our own data (roster, GM/raid/loot, boards about us) |
| `BLUE_FLAME` | `0x2EA3FF` | Calcifer relayed a feed (news, M+, external) |
| `AMBER` | `0xF5A623` | soon / attention (thin signups, prog stalls) |
| `EMBER_RED` | `0xCC2222` | now / big (major patch, blocker, first kill) |
| `class_color()` | per-class | identity (member cards, leaderboard rows) |

Pick the colour with `brand.color_for(severity)` or a named style key (see
[`publisher._STYLE_COLORS`](../ftabot/publisher.py)).

**Posting a class or role.** Embed *text* can't be coloured (only ANSI code
blocks can, and those can't render emoji), so a class carries its colour as its
crest emoji, not as tinted text. Render through `brand.class_label(name, emoji)`
(crest + name, e.g. `:class_dk: Death Knight`) and `brand.role_icon(role, emoji)`
(`:roletank:`/`:roleheal:`/`:roledps:`) so every surface posts a class/role the
same way; both fall back to the bare name / unicode glyph when the `emoji` map is
absent. The `emoji` map is the guild's `name -> markup` lookup from
`boards.guild_emoji_map(bot)`, threaded into `Board.render`. A character's role
glyph resolves through `models.effective_role(role, spec)` first, so a missing or
source-specific role spelling (e.g. WoW Audit's "Heal") still gets the right icon.
The sync also canonicalises the stored role through the same helper, so the
server-side surfaces that read the column (the web role filter, raid grouping,
role counts) agree without re-inferring.

**Mythic+ score colour (Raider.IO scale).** `brand.score_color(score)` returns
the *exact* hex from Raider.IO's ramp (`brand.SCORE_TIERS`) for surfaces that can
show real colour (an embed accent). Discord embed text can't be
coloured per character, so boards leave the score plain rather than approximating
the ramp with emoji.

**Embed anatomy** (and the limit on each — see [§7](#7-message-limits)): title,
url, description, colour, author (name + icon + url), up to 25 fields (each name
+ value, optionally `inline`), thumbnail, image, footer (text + icon),
timestamp.

**Where we already use it:** [`publisher.render_embed`](../ftabot/publisher.py)
builds every feed alert (title/url/description/colour/thumbnail/footer/timestamp,
with `brand.sanitize` on authored copy only); [`hot20.py`](../ftabot/boards/hot20.py)
builds the leaderboard embed with a footer sign-off from `brand.Voice`.

**When to use what:** simple one-line alerts and command replies stay classic
embeds; rich boards are migrating to Components V2 ([§6](#6-components-v2)) board
by board ([vision §6](vision.md#6-the-design-system--look--voice)).

---

## 6. Components V2

The headline look upgrade (`discord.py>=2.6`), used for boards. A V2 message is
built entirely from components instead of `content`/`embeds`:

| Component | What it is | discord.py |
| --- | --- | --- |
| **Container** | a bordered, accent-coloured panel (the V2 replacement for an embed) | `discord.ui.Container` |
| **Section** | text paired with an accessory (thumbnail or a button) | `discord.ui.Section` |
| **Text display** | a markdown text block | `discord.ui.TextDisplay` |
| **Separator** | a spacer / divider line | `discord.ui.Separator` |
| **Media gallery** | a grid of images | `discord.ui.MediaGallery` |
| **Action row** | a row of buttons / selects | `discord.ui.ActionRow` |
| **Link button** | a button that opens a URL | `discord.ui.Button(style=ButtonStyle.link, url=…)` |

**The interaction constraint is binding** ([vision §3](vision.md#3-how-we-build-it--architecture--approach)):
**no stateful interaction handlers.** Interactivity is **link buttons** (open a
URL), **slash commands** (request/response), and **native Discord** (Channels &
Roles / Onboarding for opt-in roles; Raid-Helper for sign-ups). A V2 board may
carry link buttons ("Sign up ➜", "Open in browser") but **never** a callback
button that the bot has to handle. Calcifer does structure and presentation, not
interaction engineering.

> Migration is backwards-compatible and board-by-board; simple alerts stay as
> classic embeds. Not yet wired in code — this is the planned upgrade path for
> the board renderers.

---

## 7. Message limits

Hard caps Calcifer must respect (the rate-limit/length contract in
[vision §6](vision.md#6-the-design-system--look--voice)). Renderers should
budget against these — e.g. the Hot 20 caps at the top 20 rows.

| Surface | Limit |
| --- | --- |
| Message content | 2000 characters |
| Embeds per message | 10 |
| Embed title | 256 |
| Embed description | 4096 |
| Embed fields | 25 (name 256, value 1024 each) |
| Embed footer text | 2048 |
| Embed author name | 256 |
| **Total across all embeds in one message** | **6000 characters** |
| Components V2: total components per message | 40 |
| Attachments per message | 10 |

**Rate-limit hygiene** (vision §6): edit, never re-post; diff before editing;
stagger channels; run cadences hourly/6h (far under the ~30–60s/message floor).

---

## 8. What's wired vs. what's a gap

A quick map so the palette's *current* reach is honest, not aspirational:

| Capability | State in code |
| --- | --- |
| Text markdown | used ad-hoc in renderers (no central helper module) |
| `brand.sanitize` (no em dash) | ✅ wired, tested, applied to authored copy |
| Dynamic `<t:…>` timestamps | ✅ used in the events board (hand-built from a Paris epoch) |
| `allowed_mentions=none` on boards | ✅ enforced in `boards/messenger.py` |
| Emoji (unicode + custom name resolution) | ✅ keys + names in `brand.py`; guild lookup at the cog/board layer |
| Classic embeds + colour system | ✅ `publisher.py`, `hot20.py`, the cogs |
| Components V2 | planned; not yet in code |
| Posting guardrail (`PostGate`) | ✅ every surface routes through it |

> There is deliberately **no markdown helper module** today — renderers write the
> literal `**…**` / `` `…` `` inline. If that ever gets repetitive enough to
> justify it, a small pure `ftabot/format.py` (bold/spoiler/code_block/
> dynamic_timestamp/mention) would sit naturally beside `brand.py` and be
> offline-tested the same way. Until then, this doc is the reference.

---

## Where the rest of the detail lives

- [`vision.md §6`](vision.md#6-the-design-system--look--voice) — the design
  system: colour meaning, iconography, Components V2 plan, the charm/voice pass.
- [`../ftabot/brand.py`](../ftabot/brand.py) — the code source of truth for
  colour, icon, emoji names, moods, and voice banks.
- [`../ftabot/publisher.py`](../ftabot/publisher.py) — how a feed event becomes a
  branded embed (the worked example of most of this doc).
- Discord's [Markdown Text 101](https://support.discord.com/hc/en-us/articles/210298617-Markdown-Text-101-Chat-Formatting-Bold-Italic-Underline)
  and [developer docs](https://discord.com/developers/docs) — the upstream
  authority.
