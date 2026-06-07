"""Run every live-API smoke check the injected credentials allow, as one table.

Each integration is checked only when it's configured; the rest are reported as
skipped. Run from a **fresh** deployed session so the secrets are injected into
the container (a session started before a secret was added can't see it) — see
``docs/testing-status.md``.

    python -m scripts.smoke_all

The pure result model + formatting here is unit-tested offline; the per-service
``_check_*`` calls and ``run_all`` hit the live network and are excluded from
coverage.
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass

from ftabot.config import Config

OK, FAIL, SKIP = "ok", "fail", "skip"
_MARK = {OK: "✓", FAIL: "✗", SKIP: "–"}


@dataclass
class SmokeResult:
    name: str
    status: str  # one of OK / FAIL / SKIP
    detail: str = ""


def configured(config: Config) -> dict[str, bool]:
    """Which integrations have enough configuration to be checked live."""
    return {
        "WoW Audit": config.wowaudit_enabled,
        "Blizzard": config.blizzard_enabled,
        "Warcraft Logs": config.warcraftlogs_enabled,
        "Raider.IO": True,  # keyless — always reachable
        "Raid-Helper": bool(config.raidhelper_webhook_secret),
    }


def format_results(results: list[SmokeResult]) -> str:
    """Render results as an aligned ✓/✗/– table with a summary line."""
    width = max((len(r.name) for r in results), default=0)
    lines = [
        f"{r.name:<{width}}  {_MARK.get(r.status, '?')} {r.status:<4}  {r.detail}".rstrip()
        for r in results
    ]
    tally = {s: sum(1 for r in results if r.status == s) for s in (OK, FAIL, SKIP)}
    lines.append(f"\n{tally[OK]} ok · {tally[FAIL]} failed · {tally[SKIP]} skipped")
    return "\n".join(lines)


def exit_code(results: list[SmokeResult]) -> int:
    """0 unless a *configured* integration failed (a skip never fails the run)."""
    return 1 if any(r.status == FAIL for r in results) else 0


async def _check_raidhelper(config: Config) -> SmokeResult:
    """Raid-Helper is an inbound webhook receiver — there's nothing to call out
    to, so 'working' means the secret is set and the parser accepts a payload."""
    from ftabot.services.raidhelper import parse_webhook

    sample = {
        "type": "create",
        "event": {"id": "smoke", "title": "Smoke Test", "startTime": 1893456000},
    }
    event = parse_webhook(sample)
    if event is None:
        return SmokeResult("Raid-Helper", FAIL, "parser returned None on a sample payload")
    return SmokeResult("Raid-Helper", OK, f"secret set; parser OK ({event.event_type})")


async def _check_wowaudit(config: Config) -> SmokeResult:  # pragma: no cover - network
    from ftabot.services.wowaudit import WowAuditClient

    try:
        async with WowAuditClient(
            config.wowaudit_api_key,
            base_url=config.wowaudit_base_url,
            default_region=config.guild_region,
            default_realm=config.guild_realm,
        ) as client:
            team = await client.team()
            members = await client.characters()
        return SmokeResult(
            "WoW Audit", OK,
            f"team {team.get('name') or '(unnamed)'!r}, {len(members)} chars",
        )
    except Exception as exc:  # noqa: BLE001 - any failure is a failed smoke test
        return SmokeResult("WoW Audit", FAIL, str(exc)[:80])


async def _check_blizzard(config: Config) -> SmokeResult:  # pragma: no cover - network
    from ftabot.services.blizzard import BlizzardClient

    try:
        async with BlizzardClient(
            config.blizzard_client_id, config.blizzard_client_secret,
            region=config.guild_region,
        ) as client:
            roster = await client.guild_roster(config.guild_name, config.guild_realm)
        return SmokeResult("Blizzard", OK, f"guild roster: {len(roster)} members")
    except Exception as exc:  # noqa: BLE001
        return SmokeResult("Blizzard", FAIL, str(exc)[:80])


async def _check_warcraftlogs(config: Config) -> SmokeResult:  # pragma: no cover - network
    from ftabot.services.warcraftlogs import WarcraftLogsClient

    try:
        # No pinned zone -> current_zone_id() makes a real authenticated query.
        async with WarcraftLogsClient(
            config.warcraftlogs_client_id, config.warcraftlogs_client_secret,
        ) as client:
            zone = await client.current_zone_id()
        return SmokeResult("Warcraft Logs", OK, f"auth OK; current zone {zone}")
    except Exception as exc:  # noqa: BLE001
        return SmokeResult("Warcraft Logs", FAIL, str(exc)[:80])


async def _check_raiderio(config: Config) -> SmokeResult:  # pragma: no cover - network
    from ftabot.services.raiderio import RaiderIOClient

    try:
        async with RaiderIOClient() as client:
            roster = await client.guild_members(
                config.guild_name, config.guild_realm, config.guild_region
            )
        return SmokeResult("Raider.IO", OK, f"guild roster: {len(roster)} members")
    except Exception as exc:  # noqa: BLE001
        return SmokeResult("Raider.IO", FAIL, str(exc)[:80])


# (name, check) in display order; run_all skips any the config doesn't enable.
_CHECKS = (
    ("WoW Audit", _check_wowaudit),
    ("Blizzard", _check_blizzard),
    ("Warcraft Logs", _check_warcraftlogs),
    ("Raider.IO", _check_raiderio),
    ("Raid-Helper", _check_raidhelper),
)


async def run_all(config: Config) -> list[SmokeResult]:  # pragma: no cover - network
    enabled = configured(config)
    results: list[SmokeResult] = []
    for name, check in _CHECKS:
        if enabled.get(name):
            results.append(await check(config))
        else:
            results.append(SmokeResult(name, SKIP, "not configured"))
    return results


def main() -> None:  # pragma: no cover - live network entry point
    config = Config.from_env()
    results = asyncio.run(run_all(config))
    print(format_results(results))
    sys.exit(exit_code(results))


if __name__ == "__main__":  # pragma: no cover
    main()
