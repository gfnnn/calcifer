#!/usr/bin/env python3
"""Smoke-test the Raider.IO integration with one live, read-only call.

Raider.IO is keyless (the always-available fallback), so this needs no secret —
it just fetches the configured guild's roster. Exit code 0 means it's working.
See docs/testing-status.md.

    python -m scripts.check_raiderio
"""

from __future__ import annotations

import asyncio
import sys

from ftabot.config import Config
from scripts.smoke_all import _check_raiderio


def main() -> None:  # pragma: no cover - live network entry point
    config = Config.from_env()
    result = asyncio.run(_check_raiderio(config))
    print(f"{'✓' if result.status == 'ok' else '✗'} {result.name}: {result.detail}")
    sys.exit(0 if result.status == "ok" else 2)


if __name__ == "__main__":  # pragma: no cover
    main()
