#!/bin/sh
# Drop privileges to the non-root `app` user for the actual long-running process
# (the internet-facing web server), while first ensuring the mounted data volume
# is writable by it.
#
# The container starts as root only so this script can fix ownership of /data:
# Railway Volumes and Render disks mount root-owned at runtime, so a process that
# started as non-root could not create the SQLite database there. We chown the
# data dir, then `exec gosu` to hand the real process to the unprivileged user.
set -e

DATA_DIR="$(dirname "${DATABASE_PATH:-/data/ftabot.db}")"
mkdir -p "$DATA_DIR" 2>/dev/null || true
# The mount may be root-owned; hand it to the app user. Ignore failure (some
# hosts disallow chown) so we still attempt to run.
chown -R app:app "$DATA_DIR" 2>/dev/null || true

exec gosu app "$@"
