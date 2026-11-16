# Boards contract

The technical contract for Calcifer's sticky posts (living boards and sticky
pages): the shared design grammar, each shipped board's markup, the voice and
visual hooks in `ftabot/brand.py`, and where each surface lives in the server.
Build new boards from this. The renderers in `ftabot/boards/` are the source of
truth; when this doc and the code disagree, fix whichever is wrong.

Related: [`communication.md`](communication.md) (the full Discord formatting
palette and limits), [`../MESSAGING.md`](../MESSAGING.md) (the voice banks) and
[`../CLAUDE.md`](../CLAUDE.md) (architecture).

---

## 1. What a sticky post is

A sticky post is **permanent** (lives in a channel), **self-updating** (edits in
place, never re-posts), **ambient** (read by looking, not by asking),
**branded** (Calcifer's colours and voice) and **never pings**.

Not sticky posts: command replies, feed alerts, ephemeral confirmations.

| Aspect | Sticky posts | Command replies |
| --- | --- | --- |
| What | a permanent page in a channel | a response to one slash command |
| Controlled by | officers via `/board`, `/page`, `/recruit` and the data | the bot, per request |
| Voice | sign-off + optional flavour | `thinking` → result, mostly unvoiced |
| Pings | never (`allowed_mentions=none`) | opt-in or officer-routed only |
| Edits or new | edits in place, diffed by fingerprint | fresh reply each time |
| Timestamp | embed timestamp, rendered per viewer | n/a |

Two kinds:

- **Living boards** (`ftabot/boards/`): one embed, rendered from the DB by a pure
  renderer, registered in `REGISTRY`, driven by `/board` and the board loop.
- **Sticky pages** (`ftabot/pages.py`): multi-message GM-authored markdown
  (Rules & Info, Guides & Resources), copy in `ftabot/content/page_*.json`,
  driven by `/page`.

---

## 2. The design contract

### A. Structure

```
[TITLE: icon + name]
[HEADER LINES: optional callouts, sanitised]
[CONTENT: rows / sections / fields]
[FOOTER: board_signoff + a fixed per-board context line]
[TIMESTAMP: when the board last refreshed]
```

In embed terms: **title** = icon + name; **description** = header + content
(sections separated by blank lines); **fields** only where a grid reads better
(Roster Pulse); **footer** = sign-off; **timestamp** = last refresh; **colour** =
per-board meaning.

### B. Voice grammar

1. **Sign-off:** always `brand.Voice(tone=config.persona_tone).line("board_signoff")`,
   falling back to `"Kept warm by Calcifer."`, followed by the board's fixed
   context line.
2. **Flavour:** optional, tone-gated (`voice.flavour(...)`), may return `None`.
   Flavour is garnish: take the fingerprint **before** appending it, so a random
   line never forces an edit.
3. **Content:** just the facts.
4. **Sanitise what Calcifer authors** (`brand.sanitize`, which strips em dashes);
   show **relayed** text (headlines, Raid-Helper titles) verbatim.

### C. Visual grammar

- Names in **bold**; numbers and ranks in `` `backticks` ``; links as masked
  links `[title](url)` with `]` in titles replaced by `)`.
- Icons from `brand.ICONS` / `brand.icon(key)`; role glyphs via
  `brand.role_icon(role, emoji)`; class via `brand.class_label(class_name, emoji)`.
  Custom guild emoji override the unicode fallback by name (`brand.EMOJI_NAMES`).
- One accent colour per board (section 4).
- Times are Discord dynamic stamps (`<t:UNIX:F>`, `<t:UNIX:R>`), never a
  hard-coded wall-clock string.

### D. Refresh and fingerprint

- **Ambient refresh:** the board loop (`BOARD_REFRESH_MINUTES`) re-renders every
  board on a timer.
- **Event-driven nudges:** the feed loop nudges News after each news poll; the
  score loop and `/refreshme` nudge the score boards; Raid-Helper webhooks nudge
  Events; `/recruit` changes show on the next tick.
- **Fingerprint:** each renderer hashes only its *material* content (rounded
  values, displayed rows), so a refresh that changes nothing edits nothing.
  `boards/engine.py:refresh_board` sends on first run, no-ops on an unchanged
  fingerprint, edits in place on change, and self-heals if the message vanished.
- **Empty state:** a renderer returns `None` to post nothing (e.g. no scores yet).
- **Weekly movement:** boards that show ▲/▼ share `_weekly.weekly_baseline`
  (seeded on first render, compared, rolled at the EU reset, Wednesday 07:00
  Europe/Paris) over `board_baselines`.
- **Limits:** stay inside Discord's 4096-character description; truncate
  officer-entered or variable-length text rather than fail the edit.

---

## 3. Shipped boards

| Board (`board_type`) | Renderer | Colour | Updates | Empty state |
| --- | --- | --- | --- | --- |
| Calcifer's Hot 20 (`hot20`) | `boards/hot20.py` | `GUILD_EMBER` | tick + score refresh; baseline rolls at reset | no scored members |
| Weekly Events (`weekly_events`) | `boards/events.py` | `GUILD_EMBER` | tick + Raid-Helper webhook | no events in window |
| News Highlights (`news_highlights`) | `boards/news.py` | `BLUE_FLAME` | tick + after each news poll | nothing archived |
| Roster Pulse (`roster_pulse`) | `boards/pulse.py` | `GUILD_EMBER` | tick (rounded values) | no members |
| Raid Progress (`raid_progress`) | `boards/raidprog.py` | `GUILD_EMBER` | tick (kills + standings) | no `guild_raids` rows |
| The Armoury (`armoury`) | `boards/armoury.py` | `GUILD_EMBER` | tick; weekly baseline | no recently seen geared characters |
| The Climb (`the_climb`) | `boards/climb.py` | `GUILD_EMBER` | tick; weekly baseline | n/a (renders an "everyone is capped" line) |
| We're Looking (`recruitment`) | `boards/recruitment.py` | `GUILD_EMBER` | tick + `/recruit` | renders the warm "full for now" copy |

### Calcifer's Hot 20

- **Title:** `🔥 Calcifer's Hot 20`
- **Header:** `🔥 Topping the embers: **{leader}** at `{score}`.` then, only for a
  real move of **≥2 ranks** this week,
  `📈 Flying up the ranks: **{name}** (▲{delta} this week).`
- **Rows** (top 20 with score > 0):
  `` `{rank:>2}` {role_icon} **{name}** · {class_label} · `{score}` {move} ``
- **Movement:** `▲n` climbed, `▼n` dropped, blank unchanged, `NEW` entered.
  First-ever render seeds the baseline so the opening post has no wall of `NEW`.
- **Footer:** `{signoff} The guild's fiercest keys.`

### Weekly Events

- **Title:** `📅 This week at the hearth`
- **Rows** (next 7 days, up to 10; `within_days`/`limit` in board config),
  separated by blank lines:
  `📅 [{title}]({url}) · <t:{ts}:F> (<t:{ts}:R>) · {n} signed up · [Sign up ➜]({url})`
  (title bold instead of linked when there is no URL; missing parts omitted).
- **Footer:** `{signoff} Sign-ups live in Raid-Helper.` + timestamp.
- Titles are relayed from Raid-Helper; sign-ups stay in Raid-Helper (read-only board).
- Fingerprint: displayed set + start time + sign-up count.

### News Highlights

- **Title:** `📰 News from the wider world`
- **Zones:** `**🔥 Top stories**` (high tier, up to 12) then
  `**🧪 PTR & Blizzard**` (low tier, up to 6); `highlights`/`latest` in board config.
- **Rows:** `{glyph} [{headline}]({url}) · <t:{ts}:R>`
- **Glyphs:** `🛠️` patch notes, `🔵` blue post, `📰` news (default), `🧪` PTR,
  `❄️` other Blizzard games, `📣` Blizzard official news.
- **Footer:** `{signoff} Read at your leisure.` + timestamp.
- Headlines are shown verbatim; only Calcifer's framing is sanitised.

### Roster Pulse

- **Title:** `🔥 Roster pulse`
- **Fields:** `Members`, `Active` (`{n} ({pct}%)`), `Health` (`{score}/100`),
  `Avg ilvl`, `Avg M+` (averages over active characters, falling back to
  everyone), `Roles` (`🛡️ n · 💚 n · ⚔️ n`, full width), `Progression` (full width).
- **Footer:** `{signoff} The guild's vital signs.` + timestamp.
- Fingerprint uses rounded values to avoid edit flicker.

### Raid Progress

- **Title:** `🏰 Raid progress`
- **Headline:** `🔥 {guild} are **{k}/{n} {Difficulty}** in **{raid}**.`;
  `🏆 **{raid}** is cleared on {Difficulty} ({k}/{n}).`; or
  `🏰 **{raid}** awaits. No bosses down yet.`
- **Per current raid:** `**{raid}**`, then one bar per difficulty
  `` `{N|H|M}` {bar} `{k}/{n}` `` and an italic standing line.
- **Recap:** `*Earlier this expansion: {raid} {summary} · ...*`
- **Footer:** `{signoff} Standings by Raider.IO.` + timestamp.
- "Current" comes from Raider.IO's raid catalog (`raids.py`), not dict order.

### The Armoury

- **Title:** `🛡️ The Armoury`
- **Header:** `🛡️ Best dressed: **{leader}** at `{ilvl}`.` and
  `Guild average across {n} active characters: `{avg}`.`
- **Rows:** top 20 recently seen characters by equipped ilvl, same row shape and
  movement markers as the Hot 20.
- **Footer:** `{signoff} Equipped item level.`

### The Climb

- **Title:** `🌱 The Climb`
- **Header:** `🌱 **{n}** adventurers are on the road to {MAX}.` (or the
  "everyone is at level {MAX}" line).
- **Rows** (top 15): `` `{i:>2}` **{name}** · {class_label} · `Lv {level}` {gain} ``,
  then `*…and {n} more on the way.*` when truncated.
- **Fresh caps:** `🎉 Fresh at {MAX} this week: **a**, **b** and {n} more.`
- **Footer:** `{signoff} Levels gained since reset.`
- Needs Blizzard-sourced `level`.

### We're Looking (recruitment)

- **Title:** `{brand.icon('recruit')} {title}` (default `🔎 We're Looking`;
  title overridable in `recruitment.json`).
- **With open slots**, blocks in order:
  1. slot rows `{role_icon} **{ROLE}**: {n} slot(s) open · ilvl {x}+ · {note}`
     in tank, healer, dps order (ilvl and note omitted when unset);
  2. `**What we value**` + the values list;
  3. `**Trial expectations**` + `→ {rule}` lines (officer rules win over the
     bundled expectations);
  4. `**About us**` + the pitch (officer `message` wins over the bundled `about`);
  5. the CTA.
- **No open slots:** the `empty` copy, then About us.
- **Flavour:** `*{recruiting}*` or `*{empty_recruiting}*`, appended after the
  fingerprint.
- **Footer:** `{signoff}`.
- Prose from `ftabot/content/recruitment.json` with live `{tokens}`
  (`prose.safe_format`); officer state in `recruitment_config`. Officer `<@id>`
  mentions render but never ping.
- Officer controls: `/recruit slots|ilvl|note|rule|message|show|reset`.

### Sticky pages: Rules & Info, Guides & Resources

- Copy in `ftabot/content/page_rules_info.json` and
  `page_guides_resources.json` (or a `/page import` override in `content_docs`),
  validated by `validate_page_doc` against Discord's limits.
- Rendered by `pages.render_page`: live `{tokens}` from `prose.live_tokens`
  (member count, progression, recruiting) substituted with `safe_format`;
  the GM's words are posted verbatim (no voice sanitise).
- Reconciled by `pages.refresh_page`: first run posts; a channel change deletes
  the old copy and re-posts; same channel + same copy is a no-op; a copy edit
  edits each message in place. Mentions are always disabled.

### Welcome card (journey; not a board)

A staff surface, so it is plain: no sign-off bank, no flavour.

- **Renderer:** `journey_cards.render_card(status, config, match, picked)`,
  pure, returning the embed and a list of button specs.
- **Title:** `👋 New member`, or `👋 Back again` for a rejoin.
- **Description:** `<@id> · **{display name}**`, `Account created <t:…:R>`,
  `Joined <t:…:F> (<t:…:R>)`.
- **Fields:** `Came via` (invite label, bare code or `unknown`), `Picked`
  (interest roles or `nothing yet`), `Roster match`
  (`**{character}** · {rank} · {exact|likely|possible}` or `no match`),
  `Previous join` (rejoins only), `Status` (full width):
  `Said hi: <@actor> <t:…:R>` / `Not greeted yet`,
  `Placed: **{role}** by <@actor> <t:…:R>` / `Not placed yet`, and
  `**Left** <t:…:R> before anyone got to them` when it applies.
- **Buttons:** `Said hi`, then one `Set {role}` per placement role. Done
  actions are greyed out; all of them once the member has left.
- **Footer:** `Calcifer keeps the notes. You do the welcoming.`
- **Colour:** `GUILD_EMBER`.
- **Lifecycle:** posted once per join, then edited in place on a click, a
  relevant role or nickname change, or a leave. A deleted card is forgotten,
  not re-posted. Its message id lives in `journey_cards`.
- Mentions render so staff can click through; `allowed_mentions=none` on every
  send and edit, so nobody is pinged.

---

## 4. Voice and visual system

### Colours (`brand.py`)

| Token | Hex | Use on boards |
| --- | --- | --- |
| `GUILD_EMBER` | `0xCC4422` | our own data and values: every board except News |
| `BLUE_FLAME` | `0x2EA3FF` | relayed external information: News |
| `AMBER` | `0xF5A623` | alerts only ("soon / attention"), not boards |
| `EMBER_RED` | `0xCC2222` | alerts only ("now / big"), not boards |

### Icons (`brand.ICONS`)

`mplus 🔑` · `kill 💀` · `progress 🔥` · `events 📅` · `news 📰` ·
`bluepost 🔵` · `vault 🎁` · `recruit 🔎` · `tank 🛡️` · `healer 💚` · `dps ⚔️` ·
`flame 🔥` · `up ▲` · `down ▼`. Board titles: 🔥 Hot 20, 📅 Events, 📰 News,
🔥 Pulse, 🏰 Raid Progress, 🛡️ Armoury, 🌱 Climb, 🔎 Recruitment.

### Voice hooks

```python
voice = brand.Voice(tone=config.persona_tone)
signoff = voice.line("board_signoff")          # structural, always returns
flavour = voice.flavour("recruiting")          # tone-gated, may be None
text = brand.sanitize(f"{signoff} The guild's fiercest keys.")
```

| Board | Sign-off | Flavour |
| --- | --- | --- |
| Hot 20 | `board_signoff` | none today (`mplus` reserved) |
| Events | `board_signoff` | none |
| News | `board_signoff` | none (relayed) |
| Pulse | `board_signoff` | none today (`success` / `alert_warning` / `alert_critical` reserved) |
| Raid Progress / Armoury / Climb | `board_signoff` | none |
| Recruitment | `board_signoff` | `recruiting` / `empty_recruiting` |
| Sticky pages | none (GM copy verbatim) | none |

---

## 5. Channel layout

The canonical layout every board, page and feed rule anchors to.

### NOTICE BOARD (read-only: `@everyone` deny Send Messages)

| Channel | Surface |
| --- | --- |
| `#rules-and-info` | sticky page: Rules & Info |
| `#guides-and-resources` | sticky page: Guides & Resources |
| `#news` | board: News Highlights |
| `#recruitment` | board: We're Looking (officer-configured) |

### COMMUNITY HUB (public chat)

| Channel | Surfaces |
| --- | --- |
| `#mythic-plus` | board: Hot 20 · feed: Raider.IO M+ (opt-in ping) |
| `#events` | board: Weekly Events · feeds: Raid-Helper, WoW Audit raid reminders, light WCL kill |
| `#guild-pulse` | board: Roster Pulse (Raid Progress, Armoury, Climb also fit here) |
| `#general` | no boards |
| `#guild-general`, `#blonde-moments` | WoW 12.1 in-game ↔ Discord chat bridges. **Calcifer never posts here**: no board, page, card or feed route (it would echo into in-game `/guild` chat). List their ids in `GAME_LINKED_CHANNEL_IDS` and the posting guardrail refuses them in every mode. |

### INTEGRATION HUB (optional)

`#calcifer-alerts`: feeds with no better home.

### PROGRESSION TEAM (read-restricted to the prog team role)

`#progression`: detailed WCL prog (kills with pull count, best pull), Raider.IO
our progression.

### FRONT DOOR (read-restricted to Flamekeepers, Officers and the GM)

| Channel | Surfaces |
| --- | --- |
| `#front-door` | welcome cards (one per join, with staff buttons). The daily nudge and the Front door board join them here as they ship. |

Calcifer needs View Channel, Send Messages, Embed Links and Read Message
History here, as in every channel he posts to.

### OFFICER HUB (read-restricted to the officer role)

| Channel | Use |
| --- | --- |
| `#recruitment-intel` | `/scout` and `/recruit lead` work |
| `#guild-admin` | sync and admin confirmations; realm-progression intel |
| `#board-drafts` | the `/posting test-channel` sandbox for boards before promotion |

### Board parking

| Surface | Category | Channel |
| --- | --- | --- |
| Rules & Info (page) | Notice | `#rules-and-info` |
| Guides & Resources (page) | Notice | `#guides-and-resources` |
| News Highlights | Notice | `#news` |
| We're Looking | Notice | `#recruitment` |
| Hot 20 | Community | `#mythic-plus` |
| Weekly Events | Community | `#events` |
| Roster Pulse | Community | `#guild-pulse` |
| Raid Progress, The Armoury, The Climb | Community | `#guild-pulse` (or a channel of their own) |
| Welcome cards | Front door | `#front-door` (promote with `/posting front-door`) |
| Drafts | Officer | `#board-drafts` (test mode) |

---

## 6. Feed routing map

Feed sources (`/feed route source:`): `Wowhead news` (`wowhead`), `Raider.IO
Mythic+` (`raiderio_mplus`), `Raid-Helper events` (`raidhelper`), `WoW Audit raid
reminders` (`wowaudit_raids`), `Warcraft Logs live prog` (`wcl_prog`), `Guild
milestones` (`milestones`). Event types for `event_type:` filters include
`wowhead.news`, `wowhead.patch-notes`, `wowhead.blue-post`, `wowhead.ptr`,
`raiderio.mplus-run`, `raiderio.affixes-changed`, `raiderio.our-progression`,
`raiderio.realm-progression`, `wowaudit.raid-reminder`, `wcl.kill`,
`wcl.best-pull`, `milestone.raid-kill`, `milestone.mplus`, `milestone.level-cap`.

```
/feed route source:Wowhead news channel:#news
/feed route source:Raider.IO Mythic+ channel:#mythic-plus
/feed route source:Raid-Helper events channel:#events
/feed route source:WoW Audit raid reminders channel:#events color:Amber (warning)
/feed route source:Warcraft Logs live prog channel:#events event_type:wcl.kill
/feed route source:Warcraft Logs live prog channel:#progression
/feed route source:Guild milestones channel:#guild-pulse
```

The same source can have two rules: the guild channel gets the headline, the
prog channel gets the full readout. Anything unmatched falls back to the
default feed channel (`/posting feed-channel`, boot default
`FEED_DEFAULT_CHANNEL_ID`).

---

## 7. Ping policy

- **Boards and pages: never ping.** `boards/messenger.py` forces
  `allowed_mentions=none` on every send and edit.
- **Journey surfaces: never ping.** Welcome cards and their button replies
  show members as mentions with `allowed_mentions=none`. Calcifer never DMs a
  member.
- **Feed alerts: opt-in only.** Members subscribe with `/alerts`; a rule's
  `ping:` role is set deliberately by an officer. Suggested opt-ins: M+ in
  `#mythic-plus`, event sign-ups in `#events`, the prog role in `#progression`,
  officers in `#guild-admin`.

---

## 8. Checklist for a new board

1. A pure renderer `(db, config, board_config, emoji) → BoardPayload | None` in
   `ftabot/boards/<name>.py`, registered into `REGISTRY` on import.
2. Title icon + name; footer `board_signoff` + a fixed context line; one colour
   token; `embed.timestamp` set.
3. Everything Calcifer authors passes through `brand.sanitize`; relayed text does not.
4. Fingerprint only material content; append flavour after hashing.
5. Return `None` for a genuinely empty state; stay under 4096 description chars.
6. Weekly movement via `_weekly.weekly_baseline`, not a bespoke baseline.
7. Offline tests against the `db` fixture: empty, typical and overflow states,
   and an unchanged fingerprint across two renders.
8. It posts through the `PostGate` (the board engine already does); review in
   `test` mode, then `/board promote`.
9. Add it to the tables in this doc and to the README board list.
