"""Go-live preflight: check a deploy's configuration before the bot starts.

Reads the same settings the bot will (``Config.from_env``, so ``.env`` too) and
reports, as one table, anything that would make the first run fail or behave
unexpectedly: a missing token, an id that is not a number (which the config
silently drops), an unknown timezone, a loop that is switched off. It makes no
network calls and never prints a secret.

    python -m scripts.preflight                                # local
    docker compose run --rm calcifer python -m scripts.preflight   # on the host

It checks the environment defaults only: a ``/posting`` override saved in the
database wins at runtime, so on an existing install the posting mode it
reports is the boot default, not necessarily what is in effect.

Exit code 1 if anything is a FAIL; warnings alone exit 0. The live API checks
are a separate step: ``python -m scripts.smoke_all``.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ftabot.config import Config

OK, WARN, FAIL = "ok", "warn", "fail"
_MARK = {OK: "✓", WARN: "!", FAIL: "✗"}
POSTING_MODES = ("off", "test", "live")


@dataclass
class Finding:
    name: str
    level: str  # one of OK / WARN / FAIL
    detail: str = ""


def _is_snowflake(value: object) -> bool:
    """A Discord id: 17 to 20 digits."""
    text = str(value) if value is not None else ""
    return text.isdigit() and 17 <= len(text) <= 20


def _token_shape_ok(token: str) -> bool:
    """A bot token is three dot-separated parts. Checks shape only, not validity."""
    parts = token.split(".")
    return len(parts) == 3 and all(parts)


def check(config: Config) -> list[Finding]:
    """Every preflight finding for ``config``. Pure: no I/O, no network."""
    out: list[Finding] = []

    # Settings the config could not read (it silently falls back) -------------
    for name in config.unparsed_env:
        out.append(Finding(name, FAIL, f"{name} is set but is not a whole number, so it is being ignored"))

    # Discord identity -------------------------------------------------------
    token = config.discord_token
    if not token:
        out.append(Finding("Discord token", FAIL, "DISCORD_TOKEN is not set: the bot will not log in"))
    elif not _token_shape_ok(token):
        out.append(Finding("Discord token", FAIL, "DISCORD_TOKEN does not look like a bot token (expected three dot-separated parts)"))
    else:
        out.append(Finding("Discord token", OK, "set (validity is checked when the bot logs in)"))

    if config.discord_guild_id is None:
        out.append(Finding("Server id", WARN, "DISCORD_GUILD_ID is unset: slash commands go global and take about an hour to appear"))
    elif not _is_snowflake(config.discord_guild_id):
        out.append(Finding("Server id", FAIL, "DISCORD_GUILD_ID is not a Discord id (17 to 20 digits)"))
    else:
        out.append(Finding("Server id", OK, "commands register to this server instantly"))

    if config.gm_discord_id is None:
        out.append(Finding("GM id", WARN, "GM_DISCORD_ID is unset: GM-only commands (/gmreport, /trends) and the GM register are off; /posting and /board still work for Discord admins"))
    elif not _is_snowflake(config.gm_discord_id):
        out.append(Finding("GM id", FAIL, "GM_DISCORD_ID is not a Discord user id"))
    else:
        out.append(Finding("GM id", OK))

    if not config.bot_commands_enabled:
        out.append(Finding("Slash commands", WARN, "BOT_COMMANDS_ENABLED=0: no commands, so the bot cannot be configured from Discord"))

    # Posting guardrail -------------------------------------------------------
    mode = config.posting_mode
    if mode not in POSTING_MODES:
        out.append(Finding("Posting mode", FAIL, f"POSTING_MODE={mode!r} is not one of off, test, live"))
    elif mode == "live":
        out.append(Finding("Posting mode", WARN, "boots live: start at off and open it from Discord with /posting mode"))
    else:
        out.append(Finding("Posting mode", OK, f"boots {mode}"))

    for label, var, value in (
        ("Test channel", "TEST_CHANNEL_ID", config.test_channel_id),
        ("Default feed channel", "FEED_DEFAULT_CHANNEL_ID", config.feed_default_channel_id),
        ("Raid-Helper channel", "RAIDHELPER_DEFAULT_CHANNEL_ID", config.raidhelper_default_channel_id),
    ):
        if value and not _is_snowflake(value):
            out.append(Finding(label, FAIL, f"{var} is not a Discord channel id (17 to 20 digits)"))

    # Loops -----------------------------------------------------------------
    if config.sync_interval_minutes <= 0:
        out.append(Finding("Roster sync", WARN, "SYNC_INTERVAL_MINUTES is 0: the roster only refreshes when someone runs /sync"))
    else:
        out.append(Finding("Roster sync", OK, f"every {config.sync_interval_minutes} min"))

    if not config.boards_enabled:
        out.append(Finding("Board refresh", WARN, "BOARD_REFRESH_MINUTES is 0: boards post once and never update by themselves"))
    else:
        out.append(Finding("Board refresh", OK, f"every {config.board_refresh_minutes} min"))

    if config.feed_poll_minutes > 0 and not config.feed_default_channel_id:
        out.append(Finding("Feeds", WARN, "FEED_POLL_MINUTES is set but FEED_DEFAULT_CHANNEL_ID is not: the feed loop stays off"))
    elif config.feeds_enabled:
        out.append(Finding("Feeds", OK, f"polling every {config.feed_poll_minutes} min"))
    else:
        out.append(Finding("Feeds", WARN, "off (FEED_POLL_MINUTES is 0): no news, milestones or alerts"))

    if config.milestones_feed_enabled and not config.feeds_enabled:
        out.append(Finding("Milestones", WARN, "MILESTONES_FEED_ENABLED=1 but the feed loop is off, so nothing will post"))

    # Onboarding journey ----------------------------------------------------
    if config.journey_enabled:
        detail = (
            "on: the Server Members intent must also be switched on in the "
            "Discord developer portal, or the bot will refuse to log in"
        )
        if config.discord_guild_id is None:
            out.append(Finding("Journey", WARN, detail + "; set DISCORD_GUILD_ID so it knows which server to watch"))
        else:
            out.append(Finding("Journey", OK, detail))

    # Data sources ------------------------------------------------------------
    if not config.blizzard_enabled:
        out.append(Finding("Blizzard API", WARN, "not configured: no character levels, so The Climb and level-cap milestones stay empty"))
    else:
        out.append(Finding("Blizzard API", OK, "configured (run scripts.smoke_all to test it live)"))

    # Time -----------------------------------------------------------------
    try:
        ZoneInfo(config.raid_tz)
        out.append(Finding("Server timezone", OK, config.raid_tz))
    except (ZoneInfoNotFoundError, ValueError):
        out.append(Finding("Server timezone", FAIL, f"RAID_TZ={config.raid_tz!r} is not a known zone: weekly resets would run on UTC"))

    return out


def format_findings(findings: list[Finding]) -> str:
    """Render findings as an aligned table with a summary line."""
    width = max((len(f.name) for f in findings), default=0)
    lines = [
        f"{f.name:<{width}}  {_MARK.get(f.level, '?')} {f.level:<4}  {f.detail}".rstrip()
        for f in findings
    ]
    tally = {lv: sum(1 for f in findings if f.level == lv) for lv in (OK, WARN, FAIL)}
    verdict = "NOT READY" if tally[FAIL] else "ready to start"
    lines.append(f"\n{tally[OK]} ok · {tally[WARN]} warnings · {tally[FAIL]} failed: {verdict}")
    return "\n".join(lines)


def exit_code(findings: list[Finding]) -> int:
    """0 unless something FAILed (warnings never block)."""
    return 1 if any(f.level == FAIL for f in findings) else 0


def main() -> None:  # pragma: no cover - thin CLI wrapper
    findings = check(Config.from_env())
    print(format_findings(findings))
    sys.exit(exit_code(findings))


if __name__ == "__main__":  # pragma: no cover
    main()
