#!/usr/bin/env python3
"""Readiness check for the Raid-Helper webhook receiver.

Raid-Helper pushes to us (POST /webhooks/raidhelper); there's no outbound API to
smoke-test, so 'ready' means RAIDHELPER_WEBHOOK_SECRET is set and the payload
parser accepts a sample event. See docs/testing-status.md.

    python -m scripts.check_raidhelper
"""

from __future__ import annotations

import asyncio
import sys

from ftabot.config import Config
from scripts.smoke_all import _check_raidhelper


def main() -> None:  # pragma: no cover - thin entry point
    config = Config.from_env()
    if not config.raidhelper_webhook_secret:
        print("✗ RAIDHELPER_WEBHOOK_SECRET not set — the receiver rejects all pushes.")
        sys.exit(1)
    result = asyncio.run(_check_raidhelper(config))
    print(f"{'✓' if result.status == 'ok' else '✗'} {result.name}: {result.detail}")
    sys.exit(0 if result.status == "ok" else 2)


if __name__ == "__main__":  # pragma: no cover
    main()
