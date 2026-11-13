# Hosting and deployment

Calcifer is one long-running process (`python -m ftabot.serve`): the Discord bot,
its board and feed loops, the scheduled roster sync and a small inbound listener.
This page covers where to run it for free or close to it, how to set that up by
hand, and how to bring the bot up safely once it is running.

Once deployed you operate it from three places:

- **Discord**: slash commands (`/sync`, `/posting`, `/board`, `/feed`, ...).
  Every runtime control lives here.
- **The host**: logs, secrets, restarts.
- **GitHub**: anything that is not a command is a change request to this repo.

Contents: [what the bot needs](#what-the-bot-needs-from-a-host) ·
[options compared](#hosting-options-compared) ·
[recommendation](#recommendation) ·
[before you start](#before-you-start-any-host) ·
[Oracle Cloud](#guide-a-oracle-cloud-always-free-0) ·
[Fly.io](#guide-b-flyio-about-4-a-month) ·
[your own machine](#guide-c-a-machine-you-already-own-0) ·
[first run](#first-run-bringing-the-bot-up-in-discord) ·
[backups and updates](#backups-and-updates) ·
[posting guardrail](#the-posting-guardrail-deny-by-default)

---

## What the bot needs from a host

| Need | Why | What it rules out |
| --- | --- | --- |
| **Always on** | The bot holds a connection open to Discord. If the process stops, the bot goes offline. | Free tiers that sleep when there is no web traffic, or scale to zero. |
| **A disk that survives restarts** | Everything lives in one SQLite file: the roster, board message ids, what has already been posted. | Hosts with a throwaway filesystem and no volume. |
| **Outbound HTTPS** | To Discord, Raider.IO and (optionally) Blizzard, Warcraft Logs, WoW Audit. | Nothing mainstream. |
| **A public URL** *(optional)* | Only for the Raid-Helper webhook and the SavedVariables upload. Everything else works without one. | Nothing: skip those two features, or add a tunnel later. |

It is small. Measured on this code with the real guild (674 characters): about
**50 MB of memory idle and under 70 MB during a full roster sync**, before the
Discord connection, which adds a little more. The database is under 5 MB. Any
host with 256 MB of memory will run it; 512 MB is comfortable.

---

## Hosting options compared

Researched in November 2026. Prices and free-tier terms change often (several of
these changed this year), so check the linked pricing page before you commit.
"Monthly cost" is for this bot specifically.

| Option | Monthly cost | Always on | Keeps the database | Verdict |
| --- | --- | --- | --- | --- |
| **[Oracle Cloud Always Free](https://www.oracle.com/cloud/free/)** VM | **$0** | Yes | Yes | **Recommended (free).** A real VM in London or Frankfurt. Needs a card to sign up, and has caveats covered in the guide. |
| **A machine you own** (Raspberry Pi, spare PC) | **$0** plus electricity | While it is powered and online | Yes | **Recommended if you have the hardware.** No account, no card, no provider risk. |
| **[Fly.io](https://fly.io/pricing/)** | **About $2.50 to $4.50** | Yes, once auto-stop is off | Yes | **Recommended (low cost, least effort).** Deploys the `Dockerfile` from `fly.toml`, includes an HTTPS URL. No free tier. |
| [Google Cloud](https://cloud.google.com/free) `e2-micro` | $0 to about $3.65 | Yes | Yes | Workable alternative. US regions only, and it is unclear whether the public IPv4 address is now billed. |
| [Railway](https://railway.com/pricing) Hobby | $5 flat | Yes | Yes | Works and is already configured (`railway.json`), but it is a $5 minimum for about $1.70 of usage. |
| Railway Free | $0 | No | 0.5 GB | Not viable: the $1 monthly credit runs out and the service stops. |
| [Render](https://render.com/pricing) | $7 plus disk | Paid only | Paid only | Over budget. The free tier sleeps after 15 minutes and has no disk. |
| Hetzner, OVH, other small VPS | About €6 to €7 | Yes | Yes | Good hosts, but now over the $5 target after 2026 price rises. |
| Koyeb, Zeabur (free tiers) | $0 | No | No | Not viable: scale to zero, or no longer offered on shared hosting. |
| AWS, Azure | $0 for 6 to 12 months | Yes | Yes | Trials, not free hosting: you would be migrating again within a year. |
| Free "Discord bot hosting" panels | $0 | Unreliable | Limited | Not recommended: daily renewals, mixed uptime reports, and you hand them your bot token. |
| GitHub Actions on a schedule | $0 | No | No | Not a host. See below. |

**Why not GitHub Actions?** It cannot hold the Discord connection open (jobs end
after at most six hours), it has no persistent disk, and GitHub's terms forbid
using hosted runners as a general-purpose server. Separately, this repository is
public, so the logs of any workflow that touched the roster would be public too.

---

## Recommendation

1. **Free: an Oracle Cloud Always Free VM, running the bot with Docker Compose.**
   It is the only option confirmed as a permanent $0 always-on machine with a
   persistent disk in a UK or EU region. → [Guide A](#guide-a-oracle-cloud-always-free-0)
2. **If you would rather pay a little and think about it less: Fly.io**, at
   roughly the price of a coffee a month. One config file, one deploy command,
   and a public HTTPS URL included. → [Guide B](#guide-b-flyio-about-4-a-month)
3. **If you have a Raspberry Pi or a spare PC: run it at home.** It is the same
   Docker Compose setup as Oracle with no account involved.
   → [Guide C](#guide-c-a-machine-you-already-own-0)

All three use the files in this repo: `docker-compose.yml` for A and C, `fly.toml`
for B. Moving between them later is copying one database file.

**What has and has not been tested.** The bot process itself was run end to end
on this code (health check, scheduled sync against the live guild, memory
measured). `docker-compose.yml` passes `docker compose config`. The image build
and both platform deployments have **not** been run from this repository yet,
so treat the first deploy as the test and expect to adjust a step.

---

## Before you start (any host)

Have these ready. Only the first is required.

1. **A Discord bot token.**
   1. Go to <https://discord.com/developers/applications> and create an application.
   2. Open **Bot**, press **Reset Token**, and copy the token. This is `DISCORD_TOKEN`.
      Treat it like a password: it never goes in the repo or in a chat message.
   3. Open **OAuth2 → URL Generator**, tick the `bot` and `applications.commands`
      scopes, and give the bot **View Channels**, **Send Messages**, **Embed
      Links** and **Read Message History**. Open the generated URL to invite it
      to your server.
2. **Your Discord server id** (`DISCORD_GUILD_ID`), so slash commands appear
   immediately. In Discord: Settings → Advanced → Developer Mode on, then
   right-click the server → Copy Server ID.
3. **A private channel for testing** (for example `#calcifer-lab`), visible only
   to officers. Nothing reaches members until you say so.
4. *(Optional)* **A free Blizzard API client** from
   <https://develop.battle.net/access/clients>, for `BLIZZARD_CLIENT_ID` and
   `BLIZZARD_CLIENT_SECRET`. This is what supplies character **levels**, so The
   Climb board and level-cap alerts need it. Everything else works without it.

### The settings to use

Guides A and C put these in a `.env` file; Guide B sets the secret ones with
`fly secrets set`. This is a sensible starting set for the whole MVP:

```ini
DISCORD_TOKEN=...your bot token...
DISCORD_GUILD_ID=...your server id...

# Refresh the roster from Raider.IO every 6 hours.
SYNC_INTERVAL_MINUTES=360
# Re-render the boards every 30 minutes, and keep active players' M+ scores fresh.
BOARD_REFRESH_MINUTES=30
SCORE_REFRESH_MINUTES=60
# Check the feeds every 15 minutes and announce guild milestones.
FEED_POLL_MINUTES=15
FEED_DEFAULT_CHANNEL_ID=...channel id that catches unrouted alerts...
MILESTONES_FEED_ENABLED=1

# Optional: character levels (The Climb board, level-cap alerts).
BLIZZARD_CLIENT_ID=
BLIZZARD_CLIENT_SECRET=
```

Leave `POSTING_MODE` unset. It defaults to `off`, so a fresh deploy posts
nothing until you open it from Discord. Every other setting is described in
[`.env.example`](../.env.example).

---

## Guide A: Oracle Cloud Always Free ($0)

You get a small Linux VM that is yours to keep. About 45 minutes the first time.

**Know before you start**

- Sign-up needs a mobile number and a **credit card** (or a debit card that
  works as credit) for identity checks. Oracle places a small temporary hold and
  does not charge for Always Free resources.
- You pick a **home region** at sign-up and **cannot change it later**. Choose
  **UK South (London)** or **Germany Central (Frankfurt)**.
- Oracle may **stop instances it considers idle** on free accounts (very low CPU,
  network and memory over a week), which this bot would be. Upgrading the
  account to **Pay As You Go** is widely reported to exempt you, and Always Free
  resources stay free after the upgrade. A stopped instance is not deleted and
  can be started again. Step 2 and the [backups](#backups-and-updates) section
  are your protection here.

### 1. Create the account

1. Go to <https://www.oracle.com/cloud/free/> and choose **Start for free**.
2. Complete the form, choosing your home region carefully (see above).

### 2. Upgrade to Pay As You Go (recommended)

In the Oracle Cloud console open the menu → **Billing & Cost Management** →
**Upgrade and Manage Payment**, and upgrade to Pay As You Go. Expect a temporary
authorisation on the card. You will not be billed while you stay inside the
Always Free allowance, and one small VM is well inside it.

### 3. Create the VM

1. Menu → **Compute** → **Instances** → **Create instance**.
2. **Name**: `calcifer`.
3. **Image**: change it to **Canonical Ubuntu 24.04**.
4. **Shape**: change it to **Ampere → VM.Standard.A1.Flex** with **1 OCPU and
   6 GB memory**. Check it shows the **Always Free-eligible** label.
   - If creation fails with *"Out of host capacity"*, try a different
     availability domain, try again later, or use **VM.Standard.E2.1.Micro**
     instead (1 GB memory, still free, still plenty; pick the standard x86
     Ubuntu image with it).
5. **Networking**: create a new virtual cloud network with a public subnet, and
   make sure **Assign a public IPv4 address** is on.
6. **SSH keys**: choose **Generate a key pair for me** and **download the
   private key**. It is shown once.
7. Press **Create**, wait for the instance to show **Running**, and note its
   **public IP address**.

### 4. Connect and install Docker

From your own computer (replace the path and the address):

```bash
ssh -i ~/Downloads/ssh-key.key ubuntu@YOUR.PUBLIC.IP
```

If SSH refuses the key as "too open", run `chmod 600` on the key file first.
Then, on the VM:

```bash
curl -fsSL https://get.docker.com | sudo sh
```

```bash
sudo usermod -aG docker ubuntu && sudo apt-get install -y git
```

Log out and back in (`exit`, then the `ssh` command again) so the group change
takes effect.

### 5. Start the bot

```bash
git clone https://github.com/gfnnn/calcifer && cd calcifer
```

```bash
cp .env.example .env && nano .env
```

Fill in [the settings](#the-settings-to-use), save (`Ctrl+O`, `Enter`) and exit
(`Ctrl+X`). Then:

```bash
docker compose up -d --build
```

The first build takes a few minutes. Check it is healthy:

```bash
docker compose ps
```

```bash
curl -s localhost:8080/healthz
```

You want `"status": "ok"` and, once Discord has connected, the bot showing as
online in your server. `docker compose logs -f` shows what it is doing
(`Ctrl+C` stops watching, not the bot).

The container restarts itself after a crash or a reboot. Continue with
[First run](#first-run-bringing-the-bot-up-in-discord).

### Optional: the two inbound features

The bot needs nothing inbound for boards, alerts or commands, and
`docker-compose.yml` deliberately binds its port to the VM only. If you later
want the **Raid-Helper webhook** or the **SavedVariables upload**, they need a
public HTTPS address. The clean way on a VM is a
[Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/)
pointed at `http://localhost:8080` (it needs a domain on Cloudflare, and no
firewall changes). If you would rather not manage that, Fly.io includes an
HTTPS address out of the box.

---

## Guide B: Fly.io (about $4 a month)

Fly builds the `Dockerfile` and runs it from the committed [`fly.toml`](../fly.toml).
About 15 minutes.

**Know before you start**

- There is **no free tier**. You can start without a card, but trial machines
  stop after five minutes, so add a card before relying on it.
- Cost is roughly **$3.70 to $4.30 a month** for the 512 MB machine in `fly.toml`
  plus a 1 GB volume, depending on region. The bot would fit in 256 MB (about
  $2.50); start at 512 MB and reduce once you have watched its real usage.
- `fly.toml` already turns **auto-stop off**. Fly's default is to stop a machine
  that receives no web traffic, which would take a Discord bot offline.

### Steps

1. Install the CLI and sign in:

   ```bash
   curl -L https://fly.io/install.sh | sh
   ```

   ```bash
   fly auth signup
   ```

   (`fly auth login` if you already have an account.) Add a card under
   **Billing** in the Fly dashboard.

2. Clone the repo and pick an app name. Names are unique across Fly, so edit the
   first setting in `fly.toml`:

   ```toml
   app = "your-app-name"
   ```

3. Create the app and its volume (London is `lhr`; Amsterdam, `ams`, is slightly
   cheaper):

   ```bash
   fly apps create your-app-name
   ```

   ```bash
   fly volumes create calcifer_data --region lhr --size 1
   ```

4. Set the secrets. These never go in `fly.toml`:

   ```bash
   fly secrets set DISCORD_TOKEN=... DISCORD_GUILD_ID=... FEED_DEFAULT_CHANNEL_ID=...
   ```

   Non-secret settings (`BOARD_REFRESH_MINUTES`, `FEED_POLL_MINUTES`,
   `MILESTONES_FEED_ENABLED`, ...) can go in the `[env]` block of `fly.toml`
   next to the two already there.

5. Deploy. `--ha=false` matters: the database volume can only attach to one
   machine, and Fly otherwise starts two.

   ```bash
   fly deploy --ha=false
   ```

6. Check it:

   ```bash
   fly status
   ```

   ```bash
   fly logs
   ```

   `https://your-app-name.fly.dev/healthz` should answer `"status": "ok"`. That
   same address is what you give Raid-Helper for its webhook
   (`/webhooks/raidhelper`).

Deploy an update with `fly deploy --ha=false` again. Fly takes a daily snapshot
of the volume automatically. Continue with
[First run](#first-run-bringing-the-bot-up-in-discord).

---

## Guide C: A machine you already own ($0)

A Raspberry Pi (3 or newer, on a 64-bit OS), an old laptop or a home server
works well: the bot only dials out, so it runs happily behind a home router with
no port forwarding. The trade-off is that it is offline whenever that machine or
your broadband is.

1. Install Docker: on Linux or Raspberry Pi OS, the same two commands as
   [Guide A step 4](#4-connect-and-install-docker) (use your own username in
   place of `ubuntu`); on Windows or macOS, install Docker Desktop.
2. Follow [Guide A step 5](#5-start-the-bot) exactly.

If you want the Raid-Helper webhook from a home connection, use a Cloudflare
Tunnel as described at the end of Guide A.

---

## First run: bringing the bot up in Discord

The same on every host. Nothing here is visible to members until the last step.

1. **Load the roster.** Run `/sync`. The first run takes a couple of minutes for
   a guild this size; later ones are faster.
2. **Open a private sandbox.**
   `/posting test-channel channel:#calcifer-lab`, then `/posting mode mode:test`.
   Every post now lands in that channel and nowhere else.
3. **Create the boards**, each pointed at the channel it will eventually live in:

   | `/board create board_type:` | Shows | Needs |
   | --- | --- | --- |
   | Calcifer's Hot 20 | Mythic+ leaderboard with weekly movement | nothing |
   | Raid Progress | Bosses down per difficulty and realm/region/world standing | nothing |
   | The Armoury | Best-geared characters with weekly movement | nothing |
   | Roster Pulse | Member count, activity, averages, health | nothing |
   | The Climb | Who is levelling, and who hit the cap this week | Blizzard API keys |
   | News Highlights | Current WoW news in one calm page | `FEED_POLL_MINUTES` |
   | Recruitment | Open slots and trial rules | `/recruit` setup |
   | Weekly Events | The week's Raid-Helper events | Raid-Helper webhook |

   Run `/board list` for the board numbers and `/board refresh board_id:N` to
   render one into the sandbox straight away.
4. **Route the alerts** you want:

   ```
   /feed route source:Guild milestones channel:#achievements
   /feed route source:Wowhead news channel:#wow-news
   /feed test source:Guild milestones
   ```

   Anything without a route goes to `FEED_DEFAULT_CHANNEL_ID`. The first check
   of each feed is silent, so there is never a dump of old news.
5. **Go live.** When a board looks right: `/posting mode mode:live`, then
   `/board promote board_id:N` for each board you are happy with. Promoted boards
   move to their real channels; everything else stays in the sandbox.

---

## Backups and updates

**Backups (Guides A and C).** Everything is in one file, `data/ftabot.db`, next
to `docker-compose.yml`. Take a safe copy while the bot is running:

```bash
docker compose exec calcifer python -m scripts.backup_db --dest /data/backups
```

Copies land in `data/backups/` and the newest 14 are kept. To do it nightly, run
`crontab -e` and add (adjust the path):

```cron
15 4 * * * cd /home/ubuntu/calcifer && docker compose exec -T calcifer python -m scripts.backup_db --dest /data/backups
```

A backup that lives only on the same machine does not protect you from losing
the machine, so copy one off it now and then. From your own computer:

```bash
scp -i ~/Downloads/ssh-key.key "ubuntu@YOUR.PUBLIC.IP:calcifer/data/backups/*.db" .
```

**Restoring or moving host**: stop the bot (`docker compose down`), put a backup
at `data/ftabot.db`, start it again. The roster itself can always be rebuilt
with `/sync`; what a backup preserves is the human-entered data (links, notes,
teams), board placements and what has already been announced.

**Updates.**

```bash
cd calcifer && git pull && docker compose up -d --build
```

Database changes apply themselves on start-up.

---

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
| `test` | **Every post is redirected to the test channel** (`/posting test-channel`), your private channel. Production channels receive nothing. Posts only start once that channel is confirmed (set). |
| `live` | Boards you have **promoted** reach their real channel; everything not yet promoted still lands in the test channel. |

This is what enforces the rule "*seen by members only once confirmed*": a board
is sandboxed until you have reviewed it in full and run `/board promote`, which
re-posts it in its production channel. The guardrail covers feed alerts too (they
drop in `off`, redirect in `test`). An alert held back while posting is off is
not lost: milestones, for example, are stored and posted once the mode allows.

## Testing new boards/alerts safely (private channel, live server)

You can trial new surfaces in the live server without the community seeing a
thing. The workflow:

1. **Make a private channel** (e.g. `#calcifer-lab`), officer-only, and run
   `/posting test-channel channel:#calcifer-lab`.
2. **Confirm the location, then send test posts.** Run `/posting mode test`. Now
   *all* boards/alerts render into `#calcifer-lab` and nowhere else. Point a
   board at its eventual production channel with `/board create` and it still
   shows up only in the test channel.
3. **Review it in full**: every state and variant of the post.
4. **Promote to production.** Run `/posting mode live` and `/board promote`
   on the board: only now is it re-posted in its real channel and seen by
   members. Un-promoted surfaces stay sandboxed.

**Why this is safe in the live server:**

- **Boards never ping.** Living boards are sent/edited with `allowed_mentions=none`,
  so even a board whose text contains a role mention can never notify members,
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

---

## Other hosts already configured

- **Railway** ([`railway.json`](../railway.json), $5 a month flat). New Project →
  Deploy from GitHub repo → pick this repo; add a **Volume** mounted at `/data`;
  set `DATABASE_PATH=/data/ftabot.db` and the settings above under **Variables**.
  Deploy without `DISCORD_TOKEN` first to confirm `/healthz` is green, then add
  it (an invalid token would crash-loop the process).
- **Render** ([`render.yaml`](../render.yaml), from $7 a month plus disk). New →
  Blueprint → select this repo and enter the secrets when prompted. The free
  tier is not usable for a bot: it sleeps without web traffic and has no disk.

## What each variable does

See [`.env.example`](../.env.example) for the full list. The essentials for a
hosted deploy:

| Variable | Why |
| --- | --- |
| `DISCORD_TOKEN` | Runs the bot (omit it and only the sync + ingest listener run). |
| `DISCORD_GUILD_ID` | Makes slash commands appear in your server immediately. |
| `SYNC_INTERVAL_MINUTES` | Auto-refresh interval for the roster. |
| `BOARD_REFRESH_MINUTES` / `SCORE_REFRESH_MINUTES` | Turn on the board loop and the M+ score refresh. |
| `FEED_POLL_MINUTES` / `FEED_DEFAULT_CHANNEL_ID` | Turn on the feed loop and give unrouted alerts a home. |
| `MILESTONES_FEED_ENABLED` | Boss kill, Mythic+ threshold and level-cap alerts. |
| `BLIZZARD_CLIENT_ID` / `BLIZZARD_CLIENT_SECRET` | Character levels, rank, race (free Blizzard API client). |
| `WOWAUDIT_API_KEY` | Raid-team extras: Great Vault, wishlists, sign-ups (paid WoW Audit feature). |
| `DATABASE_PATH` | Where the SQLite file lives. Set for you by `docker-compose.yml` and `fly.toml`. |
| `PORT` | Set automatically by most hosts; the ingest listener binds to it. |
| `RAIDHELPER_WEBHOOK_SECRET` / `SAVEDVARS_UPLOAD_SECRET` | Enable the two inbound routes; each answers 503 until its secret is set. |

The first roster appears after the first sync: either wait for the interval, or
run `/sync` from Discord once the bot is up.
