#!/usr/bin/env bash
# One-screen health report for a Calcifer Docker Compose host.
#
#   ./deploy/vm/status.sh
set -uo pipefail

cd "$(dirname "$0")/../.." || exit 1
say() { printf '\n==> %s\n' "$*"; }

say "Container"
docker compose ps calcifer

say "Health (/healthz)"
curl -fsS -m 5 http://127.0.0.1:8080/healthz || echo "no answer: the process is not running or still starting"
echo

say "Code"
echo "$(git rev-parse --short HEAD) $(git log -1 --format='%cd %s' --date=short)"

say "Resources"
docker stats --no-stream --format 'memory {{.MemUsage}}  cpu {{.CPUPerc}}' calcifer 2>/dev/null
df -h --output=avail,pcent . | tail -1 | awk '{print "disk free " $1 " (" $2 " used)"}'
# Under WAL most recent writes live in ftabot.db-wal until a checkpoint, so the
# main file alone can read 4 KB on a full database: count all three files.
du -ch data/ftabot.db data/ftabot.db-wal data/ftabot.db-shm 2>/dev/null | tail -1 | awk '{print "database " $1 " (incl. WAL)"}'

say "Backups"
# shellcheck disable=SC2012
ls -1t data/backups/*.db 2>/dev/null | head -3 || true
[ -z "$(ls data/backups/*.db 2>/dev/null)" ] && echo "none yet (the first runs at 04:15)"

say "Recent warnings and errors"
docker compose logs --since 24h calcifer 2>/dev/null | grep -E "WARNING|ERROR|CRITICAL|Traceback" | tail -10 || true
echo "(full log: docker compose logs -f)"
