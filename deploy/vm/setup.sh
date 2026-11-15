#!/usr/bin/env bash
# One-time setup of a fresh Ubuntu VM (Oracle Cloud Always Free, a Raspberry Pi,
# a spare PC) to run Calcifer with Docker Compose. Safe to run again: every step
# checks before it changes anything. Step-by-step guide: docs/deploy.md.
#
#   git clone https://github.com/gfnnn/calcifer && cd calcifer
#   ./deploy/vm/setup.sh
#
# Optional: HEARTBEAT_URL=https://hc-ping.com/<uuid> ./deploy/vm/setup.sh
# adds a 5-minute heartbeat that pings that URL only while the bot reports
# healthy, so a free monitor (e.g. healthchecks.io) emails you when it stops.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_DIR"
say() { printf '\n==> %s\n' "$*"; }

if [ "$(id -u)" -eq 0 ]; then
  echo "Run this as your normal user (e.g. ubuntu), not root. It uses sudo where needed." >&2
  exit 1
fi

say "Installing Docker (skipped if already present)"
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sudo sh
fi
if ! id -nG "$USER" | grep -qw docker; then
  sudo usermod -aG docker "$USER"
  NEED_RELOGIN=1
fi
sudo systemctl enable --now docker >/dev/null 2>&1 || true

say "Making sure security updates install automatically"
sudo apt-get update -qq
sudo apt-get install -y -qq unattended-upgrades cron >/dev/null
sudo systemctl enable --now unattended-upgrades cron >/dev/null 2>&1 || true

say "Adding swap on small machines (skipped with 3 GB of memory or more, or if swap exists)"
mem_kb=$(awk '/MemTotal/ {print $2}' /proc/meminfo)
if [ "$mem_kb" -lt 3000000 ] && [ "$(swapon --show --noheadings | wc -l)" -eq 0 ]; then
  sudo fallocate -l 2G /swapfile
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile >/dev/null
  sudo swapon /swapfile
  grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
  echo "2 GB swap file added."
fi

say "Creating .env from the template (kept if it already exists)"
if [ ! -f .env ]; then
  cp .env.example .env
  # The recommended starting set from docs/deploy.md. Posting stays off.
  set_env() { sed -i "s|^$1=.*|$1=$2|" .env; }
  set_env SYNC_INTERVAL_MINUTES 360
  set_env BOARD_REFRESH_MINUTES 30
  set_env SCORE_REFRESH_MINUTES 60
  set_env FEED_POLL_MINUTES 15
  set_env MILESTONES_FEED_ENABLED 1
  set_env POSTING_MODE off
  echo "Created .env with the recommended intervals. You still need to fill in the ids and token."
fi
chmod 600 .env
mkdir -p data

say "Installing the nightly backup (04:15, keeps the newest 14)"
MARK="# calcifer"
{
  crontab -l 2>/dev/null | grep -v "$MARK" || true
  echo "15 4 * * * cd $REPO_DIR && docker compose exec -T -u app calcifer python -m scripts.backup_db --dest /data/backups >> $REPO_DIR/data/backup.log 2>&1 $MARK"
  if [ -n "${HEARTBEAT_URL:-}" ]; then
    echo "*/5 * * * * curl -fsS -m 10 http://127.0.0.1:8080/healthz | grep -q '\"status\": \"ok\"' && curl -fsS -m 10 -o /dev/null $HEARTBEAT_URL $MARK"
  fi
} | crontab -
crontab -l | grep "$MARK"

say "Done"
cat <<EOF
Next:
$( [ "${NEED_RELOGIN:-0}" = 1 ] && echo "  0. Log out and back in once (exit, then ssh again) so you can run docker." )
  1. nano .env        fill in DISCORD_TOKEN, DISCORD_GUILD_ID, GM_DISCORD_ID,
                      FEED_DEFAULT_CHANNEL_ID and the Blizzard keys if you have them
  2. ./deploy/vm/deploy.sh      checks the config, builds and starts the bot
  3. ./deploy/vm/status.sh      confirms it is healthy and connected
EOF
