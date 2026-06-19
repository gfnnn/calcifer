#!/usr/bin/env python3
"""Run a roster sync without Discord — handy for cron or a first-time seed.

Usage:
    python -m scripts.sync_cli            # sync the configured guild
    python -m scripts.sync_cli --no-prune # keep characters who left the guild
    python -m scripts.sync_cli --list     # print the stored roster afterwards
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from ftabot.config import Config
from ftabot.db import Database
from ftabot.sync import sync_guild


async def _run(args: argparse.Namespace) -> None:
    config = Config.from_env()
    async with Database(config.database_path) as db:
        result = await sync_guild(
            db,
            config,
            name=args.name,
            realm=args.realm,
            region=args.region,
            prune=not args.no_prune,
        )
        print(result.summary())

        if args.list:
            print("\nRoster (by M+ score):")
            for i, m in enumerate(await db.all_members(), start=1):
                score = f"{m.mythic_plus_score:.0f}" if m.mythic_plus_score else "—"
                ilvl = f"{m.item_level:.0f}" if m.item_level else "—"
                print(
                    f"{i:>3}. {m.character_name:<18} "
                    f"{(m.active_spec or ''):<14} {(m.class_name or '?'):<14} "
                    f"ilvl {ilvl:>4}  M+ {score:>5}"
                )


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync the guild roster into SQLite.")
    parser.add_argument("--name", help="Override guild name")
    parser.add_argument("--realm", help="Override realm slug")
    parser.add_argument("--region", help="Override region (eu/us/kr/tw)")
    parser.add_argument(
        "--no-prune",
        action="store_true",
        help="Do not delete characters missing from the current roster",
    )
    parser.add_argument(
        "--list", action="store_true", help="Print the roster after syncing"
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    asyncio.run(_run(args))


if __name__ == "__main__":  # pragma: no cover
    main()
