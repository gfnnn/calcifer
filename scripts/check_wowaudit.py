#!/usr/bin/env python3
"""Smoke-test the WoW Audit integration end to end (no database writes).

Reads ``WOWAUDIT_API_KEY`` (and optional ``WOWAUDIT_BASE_URL``) via the normal
``Config`` and makes two live, read-only calls — ``/team`` and ``/characters`` —
then prints a short report. Exit code 0 means the integration is working.

Usage:
    python -m scripts.check_wowaudit
"""

from __future__ import annotations

import asyncio
import sys

from ftabot.config import Config
from ftabot.services.wowaudit import WowAuditClient, WowAuditError


async def _run() -> int:  # pragma: no cover - live network smoke check
    config = Config.from_env()
    if not config.wowaudit_enabled:
        print("✗ WOWAUDIT_API_KEY is not set — the integration is disabled.")
        print("  Add the key (exact name: WOWAUDIT_API_KEY) and re-run.")
        return 1

    print(f"• Key detected (length {len(config.wowaudit_api_key)}).")
    print(f"• Base URL: {config.wowaudit_base_url}")

    async with WowAuditClient(
        config.wowaudit_api_key,
        base_url=config.wowaudit_base_url,
        default_region=config.guild_region,
        default_realm=config.guild_realm,
    ) as client:
        try:
            team = await client.team()
        except WowAuditError as exc:
            print(f"✗ /team failed: {exc}")
            return 2
        print(f"✓ /team OK — team name: {team.get('name') or '(unnamed)'}")

        try:
            members = await client.characters()
        except WowAuditError as exc:
            print(f"✗ /characters failed: {exc}")
            return 2

        print(f"✓ /characters OK — {len(members)} characters returned.")
        for m in members[:5]:
            ilvl = f"{m.item_level:.0f}" if m.item_level else "—"
            print(
                f"    - {m.character_name:<18} "
                f"{(m.class_name or '?'):<14} ilvl {ilvl}"
            )
        if len(members) > 5:
            print(f"    … and {len(members) - 5} more.")

    print("\n✓ WoW Audit integration is working.")
    return 0


def main() -> None:  # pragma: no cover - live network entry point
    sys.exit(asyncio.run(_run()))


if __name__ == "__main__":  # pragma: no cover
    main()
