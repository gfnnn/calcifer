# Running it from your phone (no dev environment)

You don't run this *on* your phone — you deploy it to a cloud host once, then
run everything from the Discord app. The repo ships an all-in-one process
(`python -m ftabot.serve`) that runs the **Discord bot + its feed/board loops +
scheduled sync** together, plus a `Dockerfile` so any container host can run it.

There is no dashboard to host. The process binds one port only so the host can
health-check `/healthz` and so two sources can push data in (the Raid-Helper
webhook and the SavedVariables upload).

After it's deployed you operate it entirely from mobile:

- **Discord app** → slash commands (`/sync`, `/posting`, `/board`, `/feed`,
  `/gmreport`, `/raidcheck`, …). Every runtime control lives here.
- **Host console** (browser) → logs, secrets, restarts.
- **GitHub** → anything that isn't a command is a change request to the repo.

---

## Option A — Railway (recommended, ~$5/mo)

Best balance of always-on + persistent storage, set up entirely from a browser.

The repo includes [`railway.json`](../railway.json) (Dockerfile build,
`/healthz` health check, restart-on-failure), so Railway is close to turnkey.

1. On **railway.app** (mobile browser), sign in with GitHub.
2. **New Project → Deploy from GitHub repo →** pick `gfnnn/fta`. Railway reads
   `railway.json` and builds the `Dockerfile`. (The first build fails its health
   check until you add the volume + variables below — that's expected.)
3. **Add a Volume** (service → **Variables/Settings → Volumes → New Volume**)
   mounted at **`/data`** so the SQLite database survives restarts.
4. Service → **Variables**, add:
   - `DATABASE_PATH` = `/data/ftabot.db`
   - `WOWAUDIT_API_KEY` = *your team key*
   - `SYNC_INTERVAL_MINUTES` = `360` for a 6-hourly auto-sync (or leave it
     unset and sync on demand with `/sync`)
   - **(add after first boot is healthy)** `DISCORD_TOKEN`, and
     `DISCORD_GUILD_ID` for instant slash-command registration
5. **Settings → Networking → Generate Domain**. You only need the public URL
   for the Raid-Helper webhook and the SavedVariables upload; skip it if you
   use neither.
6. Check the deploy is healthy (the host's health check hits `/healthz`, which
   answers `{"status":"ok",...}`), then run `/sync` in Discord. First sync
   takes a few minutes (full guild); re-syncs the same day are fast
   (incremental).

> Tip: deploy **without `DISCORD_TOKEN` first** to confirm it's healthy, then
> add the token — an invalid bot token would crash-loop the process.

---

## Option B — Render Blueprint (~$7/mo)

The repo includes [`render.yaml`](../render.yaml), so it's close to one-tap.

1. On **render.com** (mobile browser): **New → Blueprint** → select this repo.
2. Render reads `render.yaml` (one service + 1 GB disk at `/data`; it is typed
   `web` only so Render health-checks it and routes the inbound webhook).
3. Enter the secret env vars when prompted (`DISCORD_TOKEN`,
   `WOWAUDIT_API_KEY`, …) → **Apply**.

> Render's free web services spin down when idle, which disconnects a Discord
> bot — use the paid `starter` instance (already set in the blueprint) for an
> always-on bot.

---

## Option C — GitHub Actions for the sync (free, no server)

If you only want the **data kept fresh** (and to add a host later), the included workflow [`.github/workflows/sync.yml`](../.github/workflows/sync.yml)
runs the sync on a schedule or on demand — entirely free, no server.

- Trigger from the **GitHub mobile app**: *Actions → Sync roster → Run workflow*.
- It builds `ftabot.db` and uploads it as a downloadable **artifact**.
- Raider.IO needs no key, so it works with zero secrets; add
  `WOWAUDIT_API_KEY` / `BLIZZARD_*` as **repo secrets** for richer data.

This does **not** run the Discord bot (Actions jobs are short-lived). Pair it
with Option A/B when you want that.

---

## Option D — Termux, directly on an Android phone (free)

Truly on-device, but the phone must stay awake and online.

```bash
pkg install python git
git clone https://github.com/gfnnn/fta && cd fta
pip install -r requirements.txt
export DISCORD_TOKEN=...  WOWAUDIT_API_KEY=...  SYNC_INTERVAL_MINUTES=360
python -m ftabot.serve
```

Fine for testing; not recommended for a guild that relies on uptime.

---

## Triggering a sync (no auto-sync)

Auto-sync is off by default (`SYNC_INTERVAL_MINUTES=0`). Refresh the roster
whenever you want, both phone-friendly:

- **Discord.** Run `/sync` (officers only) from the Discord mobile app.
- **GitHub Actions.** *Actions → Sync roster → Run workflow* (Option C above).

A sync pulls each character's data **and** details
(gear, vault, wishlist, loot) from the configured sources.

## The posting guardrail (deny-by-default)

Before *any* message reaches Discord it passes through one choke point, so
posting is off until you deliberately open it. The mode has three stages that
map to how you roll a surface out. Switch it live from Discord with
`/posting mode` (GM or Discord admin); `POSTING_MODE` / `TEST_CHANNEL_ID` in the
environment are only the boot defaults, and `/posting status` shows what is in
effect.

| Mode | What posts, and where |
| --- | --- |
| `off` *(default)* | **Nothing, anywhere.** Boards and feed alerts are both muted. A deploy can never surprise the server. |
| `test` | **Every post is redirected to the test channel** (`/posting test-channel`) — your private channel. Production channels receive nothing. Posts only start once that channel is confirmed (set). |
| `live` | Boards you have **promoted** reach their real channel; everything not yet promoted still lands in the test channel. |

This is what enforces the rule "*seen by members only once confirmed*": a board
is sandboxed until you have reviewed it in full and run `/board promote`, which
re-posts it in its production channel. The guardrail covers feed alerts too (they
drop in `off`, redirect in `test`).

## Testing new boards/alerts safely (private channel, live server)

You can trial new surfaces in the live server without the community seeing a
thing. The workflow:

1. **Make a private channel** (e.g. `#calcifer-lab`), officer-only, and run
   `/posting test-channel channel:#calcifer-lab`.
2. **Confirm the location, then send test posts.** Run `/posting mode test`. Now
   *all* boards/alerts render into `#calcifer-lab` and nowhere else — point a
   board at its eventual production channel with `/board create` and it still
   shows up only in the test channel.
3. **Review it in full** — every state and variant of the post.
4. **Promote to production.** Run `/posting mode live` and `/board promote`
   on the board: only now is it re-posted in its real channel and seen by
   members. Un-promoted surfaces stay sandboxed.

**Why this is safe in the live server:**

- **Boards never ping.** Living boards are sent/edited with `allowed_mentions=none`,
  so even a board whose text contains a role mention can never notify members —
  on first post or any later refresh.
- **Commands are scoped.** With `DISCORD_GUILD_ID` set, slash commands exist only
  in your server, and `/board` + `/feed` are officer-gated.
- **Nothing auto-fires.** `BOARD_REFRESH_MINUTES=0` and the feed flags are off
  until you turn them on, so a deploy never surprises the channel.

The one thing to watch: **feed alerts can mention roles** (that is their job, e.g.
a prog ping). Keep feed *rules* pointed at the private channel until you have seen
the real alert render, then move them. Boards carry no such risk.

> Prefer total isolation? Make a second Discord server, invite the same Calcifer
> app, and set that server's id as `DISCORD_GUILD_ID` on a staging instance with
> its own `DATABASE_PATH` (a copy of the live DB renders boards against real
> data). Nothing there can touch the live server.

## What each variable does

See [`.env.example`](../.env.example) for the full list. The essentials for a
hosted deploy:

| Variable | Why |
| --- | --- |
| `DISCORD_TOKEN` | Runs the bot (omit it and only the sync + ingest listener run). |
| `WOWAUDIT_API_KEY` | Primary data source (gear/vault/wishlist/loot). |
| `SYNC_INTERVAL_MINUTES` | Auto-refresh interval for the all-in-one runner. |
| `DATABASE_PATH` | Point at the mounted volume, e.g. `/data/ftabot.db`. |
| `PORT` | Set automatically by most hosts; the ingest listener binds to it. |
| `RAIDHELPER_WEBHOOK_SECRET` / `SAVEDVARS_UPLOAD_SECRET` | Enable the two inbound routes; each answers 503 until its secret is set. |

The first roster appears after the first sync — either wait for the interval,
or run `/sync` from Discord once the bot is up.
