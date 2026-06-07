"""WoW Audit raid reminders — nudge the guild before a scheduled raid night.

Polls WoW Audit's planned raids and emits a reminder for any starting within a
window, with the sign-up count. Deduped by raid date/time so each night is
announced once.
"""

from __future__ import annotations

from datetime import datetime

from ..config import Config
from .base import FeedEvent, FeedSource


def _epoch(iso: str | None) -> int | None:
    if not iso:
        return None
    try:
        return int(datetime.fromisoformat(iso).timestamp())
    except ValueError:
        return None


def _reminder_event(raid: dict) -> FeedEvent:
    instance = raid.get("instance") or "Raid"
    difficulty = raid.get("difficulty")
    label = f"{instance} {difficulty}".strip() if difficulty else instance
    ts = _epoch(raid.get("start"))
    when = f"<t:{ts}:R>" if ts else (raid.get("time") or "soon")

    present, total = raid.get("present"), raid.get("total")
    if present is not None and total:
        signups = f"{present}/{total} signed up"
        severity = "warning" if present < total * 0.8 else "info"
    elif present is not None:
        signups = f"{present} signed up"
        severity = "info"
    else:
        signups = "sign-ups open"
        severity = "info"

    return FeedEvent(
        source="wowaudit_raids",
        event_type="wowaudit.raid-reminder",
        external_id=f"raid-{raid.get('date')}-{raid.get('time')}",
        title=f"🔥 Raid reminder: {label}",
        summary=f"Starts {when} · {signups}",
        categories=["Raid"],
        severity=severity,
        published_at=raid.get("start"),
        extra={"present": present, "total": total},
    )


class WowAuditRaidSource(FeedSource):
    name = "wowaudit_raids"

    def __init__(self, config: Config):
        self.config = config

    async def fetch(self, session) -> list[FeedEvent]:
        if not self.config.wowaudit_enabled:
            return []
        # WoW Audit needs its own auth'd session, so we don't reuse the shared one.
        from ..services.wowaudit import WowAuditClient

        async with WowAuditClient(
            self.config.wowaudit_api_key,  # type: ignore[arg-type]
            base_url=self.config.wowaudit_base_url,
            default_region=self.config.guild_region,
            default_realm=self.config.guild_realm,
        ) as wa:
            raids = await wa.upcoming_raids(
                within_hours=self.config.raid_reminder_window_hours,
                tz=self.config.raid_timezone,
            )
        return [_reminder_event(r) for r in raids]
