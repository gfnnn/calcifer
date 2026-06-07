#!/usr/bin/env python3
"""Smoke-test the Blizzard integration with one live, read-only guild-roster call.

Reads BLIZZARD_CLIENT_ID / BLIZZARD_CLIENT_SECRET via the normal Config. Exit
code 0 means the integration is working. See docs/testing-status.md.

    python -m scripts.check_blizzard
"""

from __future__ import annotations

import asyncio
import sys

from ftabot.config import Config
from scripts.smoke_all import _check_blizzard


def main() -> None:  # pragma: no cover - live network entry point
    config = Config.from_env()
    if not config.blizzard_enabled:
        print("✗ BLIZZARD_CLIENT_ID / BLIZZARD_CLIENT_SECRET not set — disabled.")
        sys.exit(1)
    result = asyncio.run(_check_blizzard(config))
    print(f"{'✓' if result.status == 'ok' else '✗'} {result.name}: {result.detail}")
    sys.exit(0 if result.status == "ok" else 2)


if __name__ == "__main__":  # pragma: no cover
    main()
