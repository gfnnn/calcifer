# Data sources — what each API gives us

This is the reference for every external API the bot can pull from, what
character data each exposes, how auth works, and how we use it. It backs the
connectors in [`ftabot/services/`](../ftabot/services).

> **Verification note:** several vendor doc sites (wowaudit.com, warcraftlogs.com,
> icy-veins, the AoC-style wikis) sit behind Cloudflare and block automated
> fetches, so the field lists below are compiled from each platform's official
> API docs + hands-on knowledge of their response shapes. The authoritative
> source for each is linked per-section. Our connectors are written
> **defensively** (every field is optional and missing keys are tolerated), and
> each sync stores the raw payload so we can map new fields without a code
> change. Validate the exact WoW Audit JSON keys against a real response from
> *your* team key the first time you run it. Live-API verification status for
> each connector is tracked in [`testing-status.md`](testing-status.md).

---

## Summary: pick the right tool for the job

| Capability | WoW Audit | Blizzard | Raider.IO | Warcraft Logs |
| --- | :---: | :---: | :---: | :---: |
| Auth | Team API key (paid) | OAuth client creds (free) | none (free) | OAuth client creds (free) |
| Full guild roster | ✅ | ✅ | ✅ | via attendance |
| Equipped + average item level | ✅ | ✅ | ✅ (equipped) | — |
| Per-slot gear, enchants, gems | ✅ | ✅ | ✅ | — |
| Missing enchant/gem flags | ✅ | derive | — | — |
| Great Vault / weekly options | ✅ | — | — | — |
| Mythic+ score & runs | ✅ | ✅ | ✅ (best) | — |
| Raid progression | ✅ | ✅ (encounters) | ✅ | ✅ |
| Best parses / rankings | ✅ (from WCL) | — | — | ✅ (source) |
| Wishlist / BiS (Droptimizer) | ✅ | — | — | — |
| Raid attendance | ✅ | — | — | ✅ |
| Professions / recipes | partial | ✅ | — | — |
| Talents / loadouts | ✅ | ✅ | ✅ | per-fight |

**Our strategy:** WoW Audit is the *source of truth* when a team key is present
(it already aggregates the other three plus Droptimizer). The open APIs are used
as supplements and as a zero-cost fallback when no key is configured.

---

## 1. WoW Audit  — *primary source of truth*

- **Docs:** <https://wowaudit.com/api-docs>  (also reachable from your team's
  `…/main/api` settings page)
- **Base URL:** `https://wowaudit.com/v1`
- **Auth:** `Authorization: <team_api_key>` header. The key is per-team and only
  visible to team admins under team **Settings → API**. The API is a paid
  (Premium) feature.
- **Why primary:** WoW Audit already crawls Blizzard, Warcraft Logs, Raider.IO
  and Droptimizer on a schedule and reconciles them, so one call gives a richer,
  pre-joined view than assembling the four ourselves.

**Endpoints we care about**

| Endpoint | Returns |
| --- | --- |
| `GET /v1/characters` | The full tracked roster, one rich object per character. |
| `GET /v1/period/{id}` | A historical snapshot for a reporting period. |
| `GET /v1/wishlists` | Droptimizer/Raidbots best-in-slot wishlists per character. |
| `GET /v1/raids` / `/v1/raids/{id}` | Planned raids + sign-up / attendance. |
| `GET /v1/team` | Team metadata (name, realm, region, raid tiers tracked). |

**Verified against the live API (June 2026):** the endpoints behave like this —

- **`/characters`** is *lean*: `id, blizzard_id, name, realm, class, role, rank,
  note, status, tracking_since`. No item level / gear / M+ / vault here.
- **`/historical_data`** → per character `data.vault_options.{raids,dungeons,
  world}.option_1..3` (non-null = an unlocked Great Vault slot) plus M+ dungeons
  done. This is our **vault** source.
- **`/wishlists`** → `characters[] → instances[] → difficulties[] → wishlist`
  whose encounter items carry `id, name, slot, score_by_spec, wishes`. This is
  our **BiS / wishlist** source (Droptimizer/QELive backed).
- **`/raids`** → calendar with per-night `present_size` / `total_size`.
  **`/raids/{id}`** → `signups[]` with `{character:{name,realm,class,role},
  status: Present/Absent/Tentative}` — our **attendance / sign-up** source. Note
  this team's `/raids` is forward-looking, so we ingest the most recent raids
  (past if present, otherwise soonest upcoming) — i.e. current raid sign-ups.
- **`/team`** → team name/guild/url (used to tag raiders' `raid_team`).
- **Loot history is *not* exposed** in this API version (the `/loot*` paths
  return the website, not data); RCLootCouncil loot is instead ingested straight
  from the addon's SavedVariables through the data-spine upload (§5).

**How we use it:** WoW Audit only tracks the **raid team** (~24), so Raider.IO
remains the full-guild roster + stats source and WoW Audit **overlays** the
raiders with their team tag, Great Vault progress and wishlist/BiS. See
[`ftabot/sync.py`](../ftabot/sync.py).

→ Connector: [`ftabot/services/wowaudit.py`](../ftabot/services/wowaudit.py)

---

## 2. Blizzard (Battle.net) Game Data + Profile API — *free, authoritative*

- **Docs:** <https://develop.battle.net/documentation/world-of-warcraft>
- **Hosts:** `https://{region}.api.blizzard.com` (`us`/`eu`/`kr`/`tw`)
- **Auth — two OAuth flows:**
  - **Client-credentials** *(what we use today)* — `POST https://oauth.battle.net/token`,
    HTTP Basic with client id/secret. Tokens last ~24h. Reads *public* data:
    rosters, character profiles, all Game Data.
  - **Authorization-code (user login)** — the member signs in with Battle.net and
    grants the `wow.profile` scope; the resulting *user* access token (valid
    ~30 days) unlocks the **Account / Protected Profile** endpoints. This is the
    only way to learn the characters a person *actually owns* — see §2a.
- **Namespaces:** `profile-{region}`, `static-{region}`, `dynamic-{region}`. The
  namespace may be sent as a `Battlenet-Namespace` header instead of `?namespace=`.
- **Rate limit:** ~36,000 requests/hour and 100 requests/second per client. A full
  ~300-member enrich is one sequential pass, far under this; widening what we pull
  should still chunk + back off like the Raider.IO path.

**Endpoints of interest** (all under `/profile/wow/character/{realm}/{name}` unless noted):

| Endpoint | Returns |
| --- | --- |
| Character **Profile Summary** | class, spec, race, faction, level, guild, **average & equipped item level**, titles, last login. |
| Character **Equipment** | every equipped item: id, name, quality, **item level, enchantments, sockets/gems, stats, set bonuses**. |
| Character **Specializations** | talent loadouts + PvP talents. |
| Character **Mythic Keystone Profile** (+ `/season/{id}`) | current M+ rating, best runs per dungeon. |
| Character **Professions** | primary/secondary professions, tiers, known recipes. |
| Character **Encounters/Raids** | which raid bosses are killed (progression). |
| Character **Media** | avatar / inset / full render image URLs. |
| Character **Achievements, Statistics, Reputations, Collections, PvP** | as named. |
| **Guild Roster** `/data/wow/guild/{realm}/{name}/roster` | members with character ref, **guild rank index**, level. |
| **Guild Activity / Achievements** `/data/wow/guild/{realm}/{name}/activity`, `…/achievements` | recent guild-level events (member achievements, encounters) — a native, no-scrape feed source. |
| **WoW Token** `/data/wow/token/index` (`dynamic-{region}`) | current token gold price — the one endpoint on the `dynamic` namespace. |
| **Game Data** (`/data/wow/...`) | realms, items, item media, journal/encounters, playable class/race/spec, M+ affixes/dungeons/seasons, M+/raid leaderboards. |

**Best for:** authoritative guild rank, exact level/race/faction, and detailed
equipment/professions that Raider.IO doesn't expose.

**§2a — User-authorization: owned characters (the identity signal).** Behind the
authorization-code flow, `GET /profile/user/wow` (and
`/profile/user/wow/protected-character/{realm-id}-{character-id}`) return the
**characters the signed-in account owns** — an *authoritative* character→account
binding no public endpoint exposes. Paired with the Discord identity we already
hold, a member completing a Battle.net login is the leading candidate for the
data-spine's still-open **character↔Discord** link (today a manual officer link;
[`vision.md`](vision.md) §5c). It would need an OAuth callback route on the
ingest listener (`ingest.py`), the bot's only HTTP surface.

**What we wire today:** only **Guild Roster** (rank + level) and the **Profile
Summary** (race/faction/class/spec/level/ilvl). Everything else above is
documented-but-unwired — the catalog lives here so a new pull maps without
rediscovery, and the hardening to pick it up is tracked in [`vision.md`](vision.md)
§9 / §7b.

→ Connector: [`ftabot/services/blizzard.py`](../ftabot/services/blizzard.py)

---

## 3. Raider.IO — *free, no key, Mythic+ & progression*

- **Docs:** <https://raider.io/api>
- **Base URL:** `https://raider.io/api/v1`
- **Auth:** none. Be polite with rate (free tier ≈ 300 req/min); enrichment
  runs at a low concurrency (default 2, set by `ENRICH_CONCURRENCY`) in chunks,
  with exponential backoff on 429/5xx, so a large-guild sync stays under the
  limit.

**Endpoints**

| Endpoint | Useful `fields` |
| --- | --- |
| `GET /guilds/profile` | `members` (roster w/ rank, class, spec, role), `raid_progression`, `raid_rankings`. |
| `GET /characters/profile` | `gear` (equipped/total ilvl, per-slot items, enchants, gems, tier), `mythic_plus_scores_by_season:current`, `mythic_plus_ranks`, `mythic_plus_best_runs` / `recent_runs` / `weekly_highest_level_runs`, `raid_progression`, `talents`. |

**Per-character base fields:** name, race, class, active spec + role, faction,
achievement points, thumbnail/render URLs, region, realm, `last_crawled_at`,
`profile_url`.

**Best for:** zero-config roster + Mythic+ scores and raid progression without
any credentials. This is the default pipeline when no WoW Audit key is set.

→ Connector: [`ftabot/services/raiderio.py`](../ftabot/services/raiderio.py)

---

## 4. Warcraft Logs API v2 — *free, parses & attendance*

- **Docs:** <https://www.warcraftlogs.com/api/docs> ·
  schema browser <https://www.warcraftlogs.com/v2-api-docs/warcraft/>
- **Token:** `POST https://www.warcraftlogs.com/oauth/token` (client-credentials).
- **GraphQL:** `POST https://www.warcraftlogs.com/api/v2/client`. Rate limited by
  an hourly *points* budget rather than request count.

**What we can query**

| GraphQL path | Returns |
| --- | --- |
| `characterData.character(name, serverSlug, serverRegion)` | id, classID, faction, server, **guildRank**, hidden, level. |
| `…character.zoneRankings(zoneID)` | **bestPerformanceAverage**, median, `allStars` (per-spec points/rank/region/server rank + `rankPercent`), and per-encounter `rankPercent` / `totalKills` / `fastestKill` / `bestAmount`. |
| `…character.encounterRankings(encounterID)` | detailed per-boss ranking history. |
| `guildData.guild(...).attendance` | **raid attendance** — per raid night, which players were present. |
| `worldData` / `reportData` | zones, encounters, and full combat-log report parsing. |

**Best for:** the canonical parse numbers (the same ones WoW Audit surfaces) and
raid attendance straight from logs. Needs a per-tier `zoneID`
(<https://www.warcraftlogs.com/zones/list>).

→ Connector: [`ftabot/services/warcraftlogs.py`](../ftabot/services/warcraftlogs.py)

---

## 5. Local desktop addon databases — *the identity & loot ground truth*

The four sources above are all **online APIs about characters**. The piece they
*cannot* give us is **who is who** — which characters belong to one person, and
which Discord user that is. That ground truth lives on the GM's PC, in two WoW
addons' saved data, and ingesting it is **the core long-term goal of this
project** (see [`vision.md`](vision.md#5c-the-data-spine--the-long-term-core)).

- **Storage:** WoW addons persist to **SavedVariables `.lua`** files
  (`WTF/Account/<acct>/SavedVariables/<Addon>.lua`) — Lua table literals, parsed
  offline with **no dependency** by `services/savedvars.py` (a strict reader of
  the data subset: nil/bool/number/string/long-bracket/table), fitting the
  offline-first pipeline.
- **Ingestion *(shipped)*:** the GM runs the stdlib-only companion uploader
  (`scripts/upload_savedvars.py`) on their PC, which POSTs the `.lua` files to
  the secret-protected `POST /api/upload/savedvars` route (`SAVEDVARS_UPLOAD_SECRET`).
  The endpoint auto-detects which addon each file carries and routes it through
  `ftabot/spine.py`: RCLootCouncil → the `loot_history` ledger; GRM → the
  `identity_members` ground-truth table **and** an alt↔main overlay onto the
  roster.

| Addon | Provides | Status | Why it's authoritative |
| --- | --- | --- | --- |
| **RCLootCouncil** | full **loot history** (item, boss, difficulty, response, votes, date) per character | ✅ parsed → `loot_history` | the raw council ledger — richer and more reliable than any API (WoW Audit's API version doesn't expose loot, §1) |
| **Guild Roster Manager (GRM)** | **alt ↔ main** grouping, join dates, ranks, officer notes | ✅ parsed → `identity_members` + roster overlay | officer-maintained **identity** mapping — the human truth no public API holds |

**What the merge unlocks — the centralised identity graph.** Cross-referencing
GRM's alt/main groupings + officer notes, RCLootCouncil's per-character activity,
and the API character data lets us **intimately confirm character → account →
Discord user** with high confidence — the link the rest of the bot's
member-personal features depend on, sourced from data the GM *already collects*
rather than from asking users to self-register (see [`vision.md`](vision.md#5c-the-data-spine--the-long-term-core)).

> **Scope note:** the intake and parsers have **shipped** — the loot ledger and
> the alt↔main account overlay are live. What remains of the *full* vision is the
> last hop: confidently attaching **Discord** to each account (the character →
> account half is done; character → Discord stays a manual/officer link until a
> reliable signal exists). Confident member-personalisation deepens as that lands.
>
> ⚠️ **Validation gap:** the parsers are fully unit-tested but only against
> *synthetic* fixtures, not a real guild dump — the GRM field names/nesting are
> inferred. See [`data-spine-validation.md`](data-spine-validation.md) for the
> exact unverified assumptions and the plan to confirm them on live `.lua`.

→ Connectors: [`services/savedvars.py`](../ftabot/services/savedvars.py) (Lua
parser), [`services/rclootcouncil.py`](../ftabot/services/rclootcouncil.py) and
[`services/grm.py`](../ftabot/services/grm.py) (mappers),
[`identity.py`](../ftabot/identity.py) (account grouping), and
[`spine.py`](../ftabot/spine.py) (the upload ingestion entry point).

---

## 6. Content feeds (RSS/Atom) + WoWProgress ranking

Beyond the character APIs, the Calcifer feed hub consumes keyless content feeds,
each wrapped as a [`FeedSource`](../ftabot/feeds/base.py) (fetch → pure parse →
`FeedEvent`). All default-off and self-enable from config.

| Source | Feed | Transport | Notes |
| --- | --- | :---: | --- |
| **Wowhead news** | `https://www.wowhead.com/news/rss/all` | RSS 2.0 | Patch notes / blue posts / news; archives to the News board. |
| **Blizzard WoW news** | `https://news.blizzard.com/en-us/world-of-warcraft/rss` | RSS 2.0 | Official news via the generic `rss_news` source; archives beside Wowhead (deduped). |
| **YouTube uploads** | `https://www.youtube.com/feeds/videos.xml?channel_id=<UC…>` | Atom | New videos from configured channel ids; keyless, one feed per channel. |

**Reliability note:** several of these sites (Wowhead, Blizzard, WoWProgress,
Reddit) sit behind Cloudflare and 403 a default fetcher, so a poller should send
a real `User-Agent`; a feed returning nothing is usually a blocked request.

### WoWProgress — guild ranking (JSON Data Export; no RSS)

- **Docs:** <https://www.wowprogress.com/post/37_Data_Export>. WoWProgress has
  **no RSS feed**; ranking is JSON: append `/json_rank` to a guild URL →
  `{"score","world_rank","area_rank","realm_rank"}` (a `/rating.{rating}` variant
  pins a tier). A gzip **batch** export (`/export/ranks/`) covers whole realms;
  their terms require it when updating many guilds.
- **Terms (enforced in our surfaces):** free, but every page showing the data
  must link to and credit WoWProgress.com — our alert sets the embed link to the
  guild's WoWProgress page and appends "Data from WoWProgress.com".
- **How we use it:** [`feeds/wowprogress.py`](../ftabot/feeds/wowprogress.py)
  polls our guild's `json_rank` and fires on rank movement (single-guild by
  design). Connector: [`services/wowprogress.py`](../ftabot/services/wowprogress.py).

### Recruitment/LFG — why there is no inbound feed

Neither Raider.IO nor WoWProgress exposes a *listing/search* API for LFG players:
Raider.IO gives recruitment only as a per-character field (`recruitmentProfiles`,
read by the scout as `lfg_active`), and WoWProgress has no recruitment API. So
the Phase 5 funnel does not ingest an inbound feed; instead the `scout_leads`
table is fed by the existing WCL+Raider.IO scout (`/recruit lead capture`),
turning vetted, surfaced candidates into trackable leads. See [`ROADMAP.md`](ROADMAP.md).

---

## How this maps into our database

The `members` table keeps frequently-queried values as first-class columns
(class, spec, role, item level, M+ score, raid progression, parse average) and
stores the richer nested data (full gear, Great Vault, wishlist, per-boss
parses) as a JSON blob in `details_json`. This keeps queries fast while letting
the Discord views surface deep detail and letting us absorb new source fields
without a migration. See [`ftabot/db.py`](../ftabot/db.py).
