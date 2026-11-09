# Getting the most out of WoW 12.1's Discord integration (with Calcifer)

WoW Patch **12.1, "The Curse of Ula'tek,"** ships two *first-party* Discord
features, configured **in-game by Blizzard** — not through any bot or public API:

1. **Guild chat ↔ Discord channel bridge.** Guild (and officer) chat mirrors
   both ways between the game and a chosen Discord channel.
2. **Battle.net ↔ Discord account linking.** Members connect their Battle.net
   account to their Discord account, which is what powers the cross-platform chat.

There is **no public API and no bot hook** for either feature. Calcifer can't
read or post into the native bridge programmatically, and the posting guardrail
(`ftabot/posting.py`) should never be pointed at the bridged channel. So the
value isn't an integration to *build* — it's **how you arrange the server and the
in-game settings so the native bridge and Calcifer's existing surfaces reinforce
each other**, plus two forward-looking Calcifer enhancements that ride the
account-linking wave.

> **Status of this doc.** The **Bridge coexistence layout** section is config you
> can adopt today. The **Self-service account linking** and **Linked Roles**
> sections are *recommendations / "how it would work"* — **not yet built**. They
> describe future work; nothing in Calcifer reads Battle.net connections or
> writes Discord role metadata today.

---

## What 12.1 actually ships

### The guild-chat bridge

- **Setup (guild leader / officer with the permission):** open *Guild &
  Communities* (`J`) → **Roster** tab → **Guild Settings** → **Guild Control**
  dropdown → **Discord Settings**. From there you pick one of the Discord servers
  you belong to and a channel that has linking permissions.
- **Combined or split:** guild chat can flow into the regular `/guild` channel
  (all-in-one) **or** into a new dedicated `/discord` channel so in-game players
  can see Discord traffic separately.
- **Display name for Discord-origin messages:** each member chooses how their
  name appears in-game when they type from Discord — the game-defined default,
  their last-online character, or their Discord global name.
- **Two-way:** messages typed in either place appear in the other.

### Battle.net ↔ Discord linking

- Members link their **Battle.net** account to their **Discord** account; this is
  the identity glue the chat bridge rides on. 12.1 nudges your whole roster to do
  this linking.

The takeaway: 12.1 makes "everyone has linked Battle.net ↔ Discord" a realistic
assumption for the first time. The two Calcifer recommendations below are about
*cashing in* on that assumption.

---

## 1. Bridge coexistence layout (adopt now — config only)

The native bridge introduces a **live, two-way chat channel** into a server that
[`channel-architecture.md`](channel-architecture.md) deliberately built around
**read-only notice boards** and **ambient community walls**. Drop the bridge in
carelessly and it scrolls your polished boards off-screen and risks Calcifer
echoing into in-game chat. Place it deliberately and it becomes the social
heartbeat *next to* the boards.

**Where the bridge channel lives:** give it **its own channel in the COMMUNITY
HUB category** — e.g. `#guild-hall` or `#in-game-chat` — sitting alongside
`#mythic-plus`, `#events`, and `#guild-pulse`. This matches the existing
principle that COMMUNITY HUB channels are *ambient walls members chat around the
boards*. The bridged channel **is** that chat, now shared with people who never
leave the game.

**Rules of coexistence:**

- **Keep it out of the NOTICE BOARD category.** Rules & Info, Guides, News and
  Recruitment are read-only, GM-curated sticky surfaces. A live two-way chat
  channel does not belong among them — it would bury the boards and break the
  "front door, polished" feel.
- **Calcifer stays *out* of the bridged channel.** Never `/board promote` a board
  or `/feed route` an alert into it. Two reasons: (a) boards/feeds would echo
  into in-game `/guild` chat as bot spam, and (b) although boards force
  `allowed_mentions=none`, you don't want Calcifer's output crossing the bridge
  at all. The posting guardrail is deny-by-default, so this is the *absence* of a
  route, not a new setting — just don't point anything there.
- **Prefer the dedicated `/discord` channel split in-game.** Routing the bridge
  to a separate `/discord` channel (rather than merging into `/guild`) keeps
  in-game guild chat readable and makes the Discord-origin traffic opt-in to
  watch. It also keeps a clean seam if you later want officer-only vs. member
  bridges.
- **Position for "chat around the boards."** Put `#guild-hall` adjacent to
  `#events` and `#mythic-plus` so a member reads the Weekly Events board, then
  chats about signups in the bridge channel — exactly the ambient-info +
  community pattern the architecture already describes.
- **Officer/prog bridges, if any, mirror the existing tiers.** If you bridge
  officer chat, it belongs next to the OFFICER HUB, not the public hub; a prog
  bridge belongs with PROGRESSION TEAM. The bridge inherits the same audience
  tiers Calcifer's channels already use.

This section *extends* the canonical map; it does not replace it. See
[`channel-architecture.md`](channel-architecture.md) for the full layout, the
feed routing cheat-sheet, and the per-channel ping policy.

---

## 2. Self-service account linking (recommendation — not yet built)

**Today**, the `members.discord_id` column is populated **only** by the
officer-only `/link` command (`ftabot/cogs/admin.py` → `db.set_discord_link`).
That was fine when linking was rare officer bookkeeping. 12.1 changes the
economics: it pushes *every* member to link Battle.net ↔ Discord, so officers
shouldn't be the bottleneck for Calcifer knowing who's who.

**Proposed: a self-service Discord OAuth2 flow.**

> The dashboard and its web app have since been removed; the bot's only HTTP
> surface is the ingest listener (`ftabot/ingest.py`). This proposal would add
> an OAuth callback route there.

- Add a "Link your character" flow (callback on `ftabot/ingest.py`) using Discord **OAuth2**
  with the **`connections`** scope. That scope lets Calcifer read the member's
  linked **Battle.net** connection.
- Match the Battle.net handle (or a character the member confirms) against the
  roster in `members`, then set `discord_id` automatically.
- `discord_id` is a **MANUAL-owned** column (`db_schema.py` `ColumnOwner.MANUAL`),
  so a roster sync will **never** clobber a self-linked id — the existing column
  ownership invariant already protects this. No migration needed; the column
  exists.
- Keep all OAuth in the **ingest listener** — the bot stays stateless. Officer `/link`
  remains as an override/fallback for edge cases.

**Prerequisites / open questions to resolve before building:**

- New `Config` fields for the Discord **application client id + secret** (read
  only in `config.py`, threaded through as today).
- A redirect/callback endpoint and **constant-time state** validation (reuse the
  constant-time secret check already in `ingest.py`).
- **Does the Discord connection reliably expose the Battle.net handle in a form
  we can map to a character/realm?** If not, fall back to "member confirms which
  roster character is theirs" after OAuth — still self-service, just one extra
  click.

**Non-goal:** reading guild-chat content (Message Content intent). That conflicts
with the deliberately stateless-bot design and adds a privileged intent for no
roster benefit. Excluded on purpose.

---

## 3. Linked Roles (recommendation — not yet built; build #2 first)

Discord **Linked Roles** let a server gate a role badge on *third-party
metadata* a connected app vouches for. Calcifer is the perfect voucher: it
already holds verified WoW data per member. Surfacing that as **role-connection
metadata** lets the server auto-badge members from real data instead of officers
hand-assigning roles.

**Candidate metadata (all already in `members` / derivable):**

- **Raider** — `is_raider` / `raid_team`.
- **M+ tier** — bucketed Mythic+ score from `ftabot/metrics.py` (e.g.
  "+M 2500", "+M 3000").
- **Main** — `is_main` (so alts don't double-badge).
- **Attendance tier** — from the attendance metrics.

**Mechanics:**

- Needs the **`role_connections.write`** scope plus a one-time
  **app-metadata registration** (declaring the metadata fields to Discord), and a
  small web endpoint that pushes each linked member's current metadata.
- It rides **directly on the OAuth flow from section 2** — same consent, one more
  scope — so **do self-service linking first**, then layer this on.
- No privileged gateway intents required: this is entirely a web/OAuth concern,
  not a bot-gateway one. The bot's intents stay default.
- Refresh metadata when sync changes the underlying values (e.g. after the
  bounded M+ score refresh in `scores.py`), so badges track reality.

**Value:** "Raider", "+M 3000", "Main" badges that are *always true* because
Calcifer owns the data — and they reinforce 12.1's linking push (members link
once and get both cross-platform chat *and* their auto-badges).

---

## Recommended rollout order

1. **Now (config only):** adopt the bridge-coexistence layout — add the
   `#guild-hall` bridge channel in COMMUNITY HUB, split to `/discord` in-game,
   keep Calcifer out of it. No code.
2. **Next (future work):** build self-service Discord OAuth linking → auto-set
   `discord_id`. Track in [`ROADMAP.md`](ROADMAP.md).
3. **Then (future work):** layer Linked Roles on the same OAuth flow → auto-badge
   raiders, M+ tiers, mains.

Steps 2–3 are *proposals* in this doc; they ship as their own PRs with `Config`
fields, web endpoints, and tests, not as part of adopting the layout.

---

## Sources

- [Connect Your Guild Discord and In-Game Chats in Patch 12.1 — Wowhead](https://www.wowhead.com/news/connect-your-guild-discord-and-in-game-chats-in-patch-12-1-381895)
- [Link Your Battle.net Account to Discord in the Curse of Ula'tek Content Update — Blizzard](https://news.blizzard.com/en-us/article/24286284/link-your-battle-net-account-to-discord-in-the-curse-of-ulatek-content-update)
- [Chat Across Both Discord and In-Game Chats with Patch 12.1 Integration — Icy Veins](https://www.icy-veins.com/wow/news/chat-across-both-discord-and-in-game-chats-with-patch-12-1-integration/)
- [WoW players will never have to leave the MMO once Blizzard adds Discord chat for guilds — PC Gamer](https://www.pcgamer.com/games/world-of-warcraft/world-of-warcraft-players-will-never-have-to-leave-the-mmo-once-blizzard-adds-discord-chat-for-guilds-in-the-next-update/)
- [Discord Developer Docs — OAuth2 scopes (`connections`, `role_connections.write`) and Linked Roles](https://discord.com/developers/docs/topics/oauth2)
