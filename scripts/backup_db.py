#!/usr/bin/env python3
"""Take a consistent copy of the SQLite database while the bot is running.

Everything the bot knows lives in one file, so this is the whole backup story.
It uses SQLite's online backup API, which is safe against a live writer (a plain
file copy taken mid-write can be corrupt), then prunes old copies.

Usage:
    python -m scripts.backup_db                        # -> backups/ftabot-<stamp>.db
    python -m scripts.backup_db --dest /data/backups   # choose the folder
    python -m scripts.backup_db --keep 30              # keep the newest 30 (default 14)

With Docker Compose, write into the mounted data folder so the copy lands on
the host, then copy it somewhere else (another machine, cloud storage):
    docker compose exec calcifer python -m scripts.backup_db --dest /data/backups
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from ftabot.config import Config

_PREFIX = "ftabot-"


def backup(source: str, dest_dir: str, *, keep: int = 14, now: datetime | None = None) -> Path:
    """Copy ``source`` into ``dest_dir`` and prune to the newest ``keep`` copies.

    Returns the path of the new backup. Raises ``FileNotFoundError`` when the
    source database does not exist (rather than backing up an empty new file).
    """
    if not Path(source).is_file():
        raise FileNotFoundError(f"no database at {source}")
    folder = Path(dest_dir)
    folder.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%d-%H%M%S")
    target = folder / f"{_PREFIX}{stamp}.db"

    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(target)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()

    # Names sort chronologically, so the oldest copies are the first ones.
    copies = sorted(folder.glob(f"{_PREFIX}*.db"))
    for old in copies[: max(0, len(copies) - max(1, keep))]:
        old.unlink()
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description="Back up the bot's SQLite database.")
    parser.add_argument("--dest", default="backups", help="Folder to write the copy to")
    parser.add_argument("--keep", type=int, default=14, help="How many copies to keep")
    args = parser.parse_args()
    target = backup(Config.from_env().database_path, args.dest, keep=args.keep)
    print(f"Backed up to {target} ({target.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":  # pragma: no cover
    main()
