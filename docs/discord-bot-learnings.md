# Learnings from the popular Discord bots — what to borrow for Calcifer

> **In one line:** the giants of Discord (MEE6, Dyno, Carl-bot, Ticket Tool,
> Raid-Helper, Apollo) got popular by doing a handful of things well; this doc
> distils those lessons and maps each to a **keep / borrow / skip** verdict for a
> *single-guild, self-hosted* bot like Calcifer — so we copy what scales down and
> ignore what only matters at a million servers.

This is a research/reference doc, not a plan. The plan is [`vision.md`](vision.md);
where a finding *validates* a locked decision there, this doc says so and cites the
code; where it surfaces a genuine **gap**, it lands a prioritised recommendation in
[§4](#4-recommendations-prioritised). Nothing here proposes fighting the standing
constraints in [vision §3](vision.md#3-how-we-build-it--architecture--approach)
(default intents only, no stateful interaction handlers, deny-by-default posting).

> **Method & honesty note.** Findings come from five parallel web-research sweeps
> (June 2026) across Discord's official developer docs, the discord.py/discord.js
> guides, and the bots' own sites. Claims tied to **official Discord docs** are
> high-confidence; **self-reported marketing figures** (server counts, "99.99%
> uptime", premium prices) are directional only. One sweep surfaced a postmortem
> from a likely-fabricated source; its specific citation was dropped, but the
> mechanism it described (a no-backoff retry loop tripping a Cloudflare ban) is
> independently documented and is kept on those grounds. Sources are listed in
> [§7](#7-sources).

---

## 1. Why the giants win (the five repeatable moves)

The dominant general-purpose bots are deployed at enormous scale — MEE6 (~12M+
servers, self-reported), Carl-bot (~14M), Dyno (4M+), Ticket Tool (~13M). Strip
away the scale and the *reasons* they stuck are five repeatable moves:

1. **Low-friction onboarding via a web dashboard.** MEE6, Dyno, and Carl-bot all
   pair in-Discord commands with a **browser dashboard + plugin/module toggles** —
   pick a server, flip on "Welcome / Levels / Moderation", done. Setup never
   requires memorising command syntax.
2. **One sticky engagement loop each.** MEE6 = **leveling/XP** (15–25 XP per
   message, capped to once a minute to fight spam, with role rewards). Carl-bot =
   **reaction roles**. Dyno = **moderation reliability**. Ticket Tool =
   **support tickets**. Each bot is *known for one thing* and does it well.
3. **Modern interaction UX.** Slash commands with **autocomplete**, **buttons**
   (colour-coded by intent: blurple = primary, green = confirm, red = destructive),
   **modals**, **select menus**, and **ephemeral replies** to cut channel clutter.
   The hard rule everyone respects: an interaction token is valid for only
   **3 seconds** — defer or lose it.
4. **Reliability as a marketed feature.** Dyno leads with "99.99% uptime" and
   "stable and reliable"; comparison roundups cite *stability* as the top reason a
   bot is trusted at scale. Status is communicated through dedicated status pages
   and trackers — and the trust damage from an outage comes less from the downtime
   than from **silent, unacknowledged** downtime.
5. **A free core that earns goodwill.** The trusted monetisation shape is
   Carl-bot's: **free core, paid *limits/extras*.** The clearest trust-destroyer in
   the ecosystem is the opposite move — **MEE6 taking features that were free and
   paywalling them** (role rewards, then moderation), which generated lasting
   community backlash (compounded by a since-deleted NFT launch).

---

## 2. Borrow / skip for a single-guild bot

Most "best practices" for public bots are *scale* features that don't apply to one
guild. The verdict table — **BORROW** = adopt; **SKIP** = scale-only; **ADAPT** =
the guild-native version of the idea:

| Big-bot pattern | Verdict for Calcifer | Why |
| --- | --- | --- |
| Guild-scoped instant command sync | **BORROW** ✅ *(done)* | Global commands take **up to 1h** to propagate; guild-scoped is instant. Already done in [`bot.py`](../ftabot/bot.py) via `copy_global_to` + `DISCORD_GUILD_ID`. |
| Defer long commands within 3s; ephemeral for personal replies | **BORROW** ✅ *(done)* | The 3-second interaction-token rule; ephemeral keeps channels tidy. Cogs already use `defer`/`ephemeral` widely. |
| Safe `allowed_mentions` default + explicit opt-in to ping | **BORROW** ✅ *(done)* | If unset, Discord parses `@everyone`/role/user pings from bot content. Boards force `AllowedMentions.none()`; `/alerts` is the opt-in. |
| Least-privilege intents (no Message Content) | **BORROW** ✅ *(done)* | A slash-command bot rarely needs Message Content; Discord won't approve it where Interactions suffice. [`bot.py`](../ftabot/bot.py) uses `Intents.default()`. |
| Least-privilege OAuth scopes; never Administrator | **BORROW** | Smaller blast radius if the token leaks; owners distrust Admin-asking bots. Invite with the minimum permission integer. |
| SQLite + `aiosqlite` + **WAL** + single-writer | **BORROW** ✅ *(done)* | The exact "one sync writes, bot+web read" model SQLite+WAL is built for. WAL + `busy_timeout` set in [`db.py:69`](../ftabot/db.py). |
| Supervised single-process auto-restart | **BORROW** ✅ *(done)* | `serve.py`'s "a dying long-running task brings the process down so the host restarts cleanly" is the endorsed pattern (systemd `Restart=on-failure`). |
| Web dashboard as the *onboarding* surface | **ADAPT** | The giants' dashboards exist to configure *per-guild settings*. We have one guild, so config is env-vars; but the **discoverability** win (a "here's what this does" surface) is real → concierge cards + `/me` ([vision §4d](vision.md#4d-personal--toggleable)). |
| Chat-message XP / leveling loop | **ADAPT → SKIP generic** | MEE6's signature loop needs message events (Message Content/Members) and rewards *posting volume* — it fights "default intents only" and the "less, not more" rule. The guild-native equivalent already exists: **Hot 20**, first-kill milestones, Ashes Awards reward *real achievement*, not message spam. |
| Reaction-role bot (Carl-bot's niche) | **ADAPT** | Replaced by **native Channels & Roles / Onboarding** + `/alerts` — no stateful handler, which is the Discord-blessed modern path anyway ([vision §3](vision.md#3-how-we-build-it--architecture--approach)). |
| Status page / outage transparency | **BORROW (lightweight)** | See [§4](#4-recommendations-prioritised). Acknowledged downtime preserves trust; silent recovery erodes it. |
| Components V2 (containers/sections/link buttons) | **BORROW** | Already the planned board look-upgrade ([communication.md §6](communication.md#6-components-v2)). Note: the `IS_COMPONENTS_V2` flag **disables** legacy `content`/`embeds` on that message — migrate board-by-board, keep simple alerts as classic embeds. |
| **Sharding** | **SKIP** | Not needed until ~2,400 guilds (mandatory by 2,500). At one guild it's pure overhead and can *slow* the bot. |
| **Multi-tenant per-guild config tables / dashboard** | **SKIP** | One guild → per-guild config collapses to `Config.from_env()`. A settings-keyed-by-`guild_id` table would be anti-pattern here. |
| **Per-guild premium / billing tiers** | **SKIP** | N/A to a self-hosted single-guild bot. |
| Bot-verification + privileged-intent approval flow | **SKIP (for now)** | The 75-server (apply) / 100-server (required) gates never bite a single-guild bot. Only relevant if we ever monetise (see [§6](#6-monetisation-trust--the-legal-floor)). |

---

## 3. What Calcifer already gets right

The research mostly *validates* the architecture — worth stating plainly so we
don't "fix" what's already correct:

- **Default intents, slash-first.** No Message Content intent, so none of the
  2022-era privileged-intent breakage (see [§5](#5-pitfalls-that-kill-bots))
  applies. ([`bot.py:24`](../ftabot/bot.py))
- **WAL + single-writer SQLite** is the textbook fit for "sync writes, bot/web
  read." ([`db.py:63-70`](../ftabot/db.py))
- **Deny-by-default posting + boards never ping** is the same least-privilege
  philosophy the docs preach for `allowed_mentions`, applied to *destinations*.
  ([`posting.py`](../ftabot/posting.py), [`boards/messenger.py`](../ftabot/boards/messenger.py))
- **Self-hosted + open + own SQLite** is, per the research, *the strongest
  available trust posture* — auditable code, data stays put, no third-party data
  sale. It's a feature, not just an implementation detail.
- **`BaseHTTPClient` Retry-After handling + bounded M+ poll-sets** directly
  address the rate-limit failure mode, and respect the real API ceilings
  (Raider.IO 300 req/min keyless; Warcraft Logs 3,600 points/hr via GraphQL+OAuth;
  Blizzard 24h OAuth tokens). ([`services/_http.py`](../ftabot/services/_http.py))
- **"Less, not more" / curation-is-the-service** ([vision §8](vision.md#8-guardrails-decisions-and-whats-still-needed))
  is, in effect, the anti-bloat and anti-paywall lesson already internalised.

---

## 4. Recommendations, prioritised

Only the genuine **gaps** — each a small, self-contained borrow, sized to land
without disturbing the running bot.

### P0 — Privacy policy + a data-deletion path *(trust + policy floor)*

**The gap.** Calcifer ingests personal data — `discord_id`, GRM identity
(`identity_members`), and a real loot ledger from uploaded SavedVariables. Under
**Discord's Developer Policy** an app that stores user data must publish a privacy
policy describing what is collected, how it's used, and **how a user can request
deletion** (actionable via a command, email, or support server). Under **GDPR**
(an EU guild), operating this data makes the host a **data controller**; merely
receiving API data is "processing." There is no privacy surface today.

**The borrow.**
1. A short **privacy policy** — a repo template rendered at a `GET /privacy` route
   in [`web.py`](../ftabot/web.py) (sits naturally beside the existing HTML pages),
   covering: what's stored (roster, identity links, loot), why (guild management),
   that it's **never sold or shared** (Discord's Developer Policy flatly bans
   disclosing API data to data brokers / commercialising it anyway), and a
   deletion path.
2. A **deletion path** — even an officer-run process documented in the policy is
   enough to satisfy "actionable"; a `/forget <member>` officer command over the
   existing manual-write layer would be the clean version.

**Why P0.** It's the one finding that's a *policy obligation*, not a nicety, and it
costs little: one route + one template + a documented process. Maps to the
identity-graph work in [vision §5c](vision.md#5c-the-data-spine--the-long-term-core).

### P1 — Make `/healthz` reflect gateway liveness, not just the DB

**The gap.** [`web.py:120`](../ftabot/web.py) returns `{"status": "ok"}` after a DB
count. The research's clearest reliability lesson is the **"process up but gateway
dead"** failure mode — the web server answers 200 while the bot's Discord
connection is silently dead, so an external uptime check sees green during an
outage.

**The borrow.** Give the FastAPI app a reference to the bot (already co-located in
[`serve.py`](../ftabot/serve.py)) and have `/healthz` also report
`bot.is_ready()` / `not bot.is_closed()` and `bot.latency`. An external monitor
hitting that endpoint then catches a dead gateway, not just a dead process. Low
cost, directly closes the gap, and it's the natural place to surface a status
signal.

### P1 — Confirm backoff on the loops; never tight-retry on error

**The gap (preventive).** The canonical way community bots *die* is a **no-backoff
retry loop**: transient API failures → hammer retries → trip Discord's
invalid-request threshold (**~10,000 invalid responses per 10 min → a 24-hour
Cloudflare ban**, where an "invalid" response is a 401/403/429). discord.py's
gateway reconnect already backs off; the risk lives in *our* loops.

**The borrow.** Audit the feed/score/sync loops in [`serve.py`](../ftabot/serve.py)
and the `services/` clients to confirm that on repeated failure they **back off
exponentially** (and honour `Retry-After`) rather than retry on a fixed tight
interval. The loops already swallow-and-continue to stay alive; the addition is a
growing sleep on consecutive failures so a degraded upstream can't turn into a
self-inflicted ban.

### P2 — A lightweight, honest status signal

**The borrow.** Not a status page — just a small **"Calcifer's heartbeat"** signal:
e.g. a `/status` command (last sync time, gateway latency, feed/board loop health)
and/or a one-line footer the GM can glance at. The lesson is *acknowledged*
downtime beats silent recovery; a single honest indicator is enough for a guild.
Fits the boards pattern and the GM register in [vision §2](vision.md#2-calcifer--the-persona-and-the-bond).

### P2 — Keep leaning on discoverability as the "onboarding dashboard"

**The borrow.** The giants' adoption hinges on *frictionless discovery of what the
bot does*. Calcifer's single-guild equivalent is **concierge helper cards** + `/me`
([vision §4d](vision.md#4d-personal--toggleable), §4a) — a per-channel "here's what
you can do here." This is already on the roadmap; the research is a reason to keep
it prioritised rather than letting it slip behind more boards.

---

## 5. Pitfalls that kill bots

The failure modes that most often cripple or end a bot — and where Calcifer stands
on each:

| Failure mode | What happens | Calcifer's exposure |
| --- | --- | --- |
| **No-backoff retry → Cloudflare ban** | Tight retries on transient errors cross ~10k invalid/10min → **24h API ban**; retries re-trigger it, extending the outage. | **Low, verify (P1).** Loops swallow errors to stay alive; confirm they back off, not tight-loop. |
| **Privileged-intent break** | Message Content became privileged (**Sept 1 2022**); without it, `content`/`embeds`/`attachments` come back empty. Requesting an unapproved intent → gateway close **4014 (Disallowed intent)**. | **None.** We never request Message Content; default intents only. |
| **The 100-server growth gate** | An unverified bot crossing 100 servers can't join more, and portal intent toggles stop being honoured. | **None.** Single guild is far below the 75/100 thresholds. |
| **Token leakage** | A leaked token = full control of the bot; common via hardcoding, public repos, logging. Discord auto-scans public GitHub and invalidates found tokens. | **Low.** Token via env/`Config`; keep it out of logs and Git (`.gitignore`), rotate if exposed. |
| **Over-broad permissions** | Administrator bypasses every check; one leak = whole-server blast radius. | **Low.** Invite with least-privilege; avoid Administrator. |
| **Self-host = single point of failure** | "If it goes down at 2AM, you're fixing it." Free/ephemeral hosts kill bots; backups that are never restore-tested. | **Medium (inherent).** Mitigated by managed host + persistent disk + restart-on-crash; **add a backup/restore drill** for the single SQLite file. |
| **Feature bloat / over-shouting** | Scope creep and noise erode the product (the very problem Calcifer was built to fix). | **By design, low.** The "every feature reduces noise or removes a chore" rule is the guardrail — keep enforcing it. |
| **Platform/ToS enforcement** | Discord can shut down apps over policy (e.g. the 2025 BotGhost token ultimatum). | **Low.** Standard library, own token, slash-first, no ToS-edgy behaviour. |

---

## 6. Monetisation, trust & the legal floor

Not a goal for a guild bot, but the lessons set useful guardrails:

- **Don't paywall what was free.** The single clearest trust-destroyer (MEE6). If
  funding ever matters, the trusted shape is **donation/Patreon-style**, or
  Carl-bot's "free core, paid limits" — never clawing back a free feature.
- **Discord-native monetisation likely doesn't apply.** Premium Apps take a
  **15% cut** (85% to the dev) and require the team owner be **US-based with a US
  bank account + US ID via Stripe** — a hard blocker for an EU guild. Since
  **Oct 7 2024**, anyone selling on-platform features *must also* offer them via
  Premium Apps at price parity. Net: on-platform monetisation is a non-starter
  here; external donations sit outside that system.
- **Self-hosted + open + own DB is the high-trust posture** — lean on it. It
  answers the data-handling and outage-dependency worries that SaaS bots can't.
- **The data is not a product.** Discord's Developer Policy bans disclosing API
  data to advertisers/data brokers and bans commercialising it. Whatever the
  identity graph becomes, "monetise the roster data" is off the table by policy —
  worth stating in the privacy policy (P0) as a *commitment*, not just a constraint.

---

## 7. Sources

Official Discord docs and the discord.py/discord.js guides are the load-bearing
sources; bot sites and comparison blogs are directional. (WebFetch was
environment-blocked during research, so several official-doc claims were captured
via search snippets that quoted those same pages; the few directly-fetched primary
sources — the GitHub policy text and the message-content discussion — gave exact
quotes.)

**Discord official (docs / policy / support)**
- Bot UX & interactions: https://docs.discord.com/developers/bots/overview · https://discordjs.guide/slash-commands/response-methods · https://discordjs.guide/legacy/interactive-components/buttons
- Components V2: https://docs.discord.com/developers/components/reference
- Intents & privileged-intent policy: https://discordpy.readthedocs.io/en/latest/intents.html · https://support-dev.discord.com/hc/en-us/articles/6207308062871 · https://support-dev.discord.com/hc/en-us/articles/5324827539479 · https://gist.github.com/advaith1/e69bcc1cdd6d0087322734451f15aa2f
- Message Content became privileged (Sept 1 2022): https://github.com/discord/discord-api-docs/discussions/5412 · https://github.com/discord/discord-api-docs/discussions/4729
- Rate limits / invalid-request ban: https://docs.discord.com/developers/topics/rate-limits
- Verification & monetisation: https://support-dev.discord.com/hc/en-us/articles/6206007597207 · https://support.discord.com/hc/en-us/articles/10575066024983-Monetization-Policy · https://support-dev.discord.com/hc/en-us/articles/17299902720919 · https://support-dev.discord.com/hc/en-us/articles/23810643331735 · https://docs.discord.com/developers/monetization/enabling-monetization
- Developer Policy / ToS (data use, privacy, deletion): https://support-dev.discord.com/hc/en-us/articles/8563934450327 · https://github.com/discord/discord-api-docs (Developer_Policy.md / Developer_Terms_of_Service.md)
- Scheduled events: https://support.discord.com/hc/en-us/articles/4409494125719

**discord.py / hosting / persistence**
- Command sync: https://github.com/Rapptz/discord.py/discussions/8170 · https://gist.github.com/AbstractUmbra/a9c188797ae194e592efe05fa129c57f
- allowed_mentions: https://tutorial.vcokltfre.dev/tips/mentions/
- systemd auto-restart: https://docs.discord.red/en/stable/autostart_systemd.html
- Sharding thresholds: https://discordjs.guide/legacy/sharding · https://anidiots.guide/understanding/sharding/
- SQLite/aiosqlite/WAL: https://aiosqlite.omnilib.dev/ · https://github.com/omnilib/aiosqlite

**Bots & WoW data (directional)**
- MEE6: https://wiki.mee6.xyz/en/plugins/levels · https://wiki.mee6.xyz/en/features/dashboard · https://www.toolify.ai/ai-news/unmasking-the-dark-secrets-of-mee6-101586
- Dyno / Carl-bot / Ticket Tool: https://dyno.gg/bot · https://carl.gg/ · https://tickettool.xyz/
- Comparisons: https://blog.communityone.io/best-discord-bots/ · https://peakbot.pro/blog/mee6-vs-dyno-vs-carl-bot-2026
- Raid-Helper / Apollo: https://discord.fandom.com/wiki/Bot:Raid-Helper · https://raid-helper.dev/documentation/api · https://apollo.fyi/blog/apollo-vs-sesh/
- WoW data APIs: https://raider.io/api · https://support.raider.io/kb/raider-io-score-and-raid-progress · https://www.warcraftlogs.com/api/docs · https://community.developer.battle.net/documentation/guides/using-oauth · https://wowaudit.com/
- Addons (data spine): https://github.com/evil-morfar/RCLootCouncil2 · https://www.curseforge.com/wow/addons/guild-roster-manager
- Open-source/self-host trust: https://github.com/botlabs-gg/yagpdb · https://itsfoss.com/open-source-discord-bots/

---

## Where the rest of the detail lives

- [`vision.md`](vision.md) — the plan this doc serves; every recommendation here
  maps to a phase or guardrail there.
- [`communication.md`](communication.md) — the Discord formatting palette (slash,
  components V2, timestamps, mentions, limits) referenced throughout.
- [`data-sources.md`](data-sources.md) — what each WoW API exposes and the addon
  data spine the privacy-policy recommendation (P0) concerns.
