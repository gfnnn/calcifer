#!/usr/bin/env python3
"""Smoke-test the Warcraft Logs integration with one live authenticated query.

Reads WARCRAFTLOGS_CLIENT_ID / WARCRAFTLOGS_CLIENT_SECRET via the normal Config
and resolves the current raid zone (which mints a token and runs a GraphQL
query). Exit code 0 means the integration is working. See docs/testing-status.md.

    python -m scripts.check_warcraftlogs
"""

from __future__ import annotations

import asyncio
import sys

from ftabot.config import Config
from scripts.smoke_all import _check_warcraftlogs


def main() -> None:  # pragma: no cover - live network entry point
    config = Config.from_env()
    if not config.warcraftlogs_enabled:
        print("✗ WARCRAFTLOGS_CLIENT_ID / WARCRAFTLOGS_CLIENT_SECRET not set — disabled.")
        sys.exit(1)
    result = asyncio.run(_check_warcraftlogs(config))
    print(f"{'✓' if result.status == 'ok' else '✗'} {result.name}: {result.detail}")
    sys.exit(0 if result.status == "ok" else 2)


if __name__ == "__main__":  # pragma: no cover
    main()
