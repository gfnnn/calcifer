"""Warcraft Logs live progression feed (the role Wipefest plays).

Wipefest has no standalone API — it reads Warcraft Logs — so we poll WCL's most
recent report for the guild and summarise progression: best pull per boss and
kills. To stay quiet during easy farm and noisy only when it matters, we only
announce a boss once it's within a "getting close" threshold, bucketed so each
~5% improvement posts once, plus a one-off kill announcement.

WCL's exact percentage semantics sit behind their docs; we treat
``bossPercentage`` as boss health remaining (lower = closer to a kill) and fall
back to ``fightPercentage``. Adjust if a real report shows otherwise.
"""

from __future__ import annotations

from ..config import Config
from .base import FeedEvent, FeedSource


def _remaining_pct(boss_pct, fight_pct) -> float | None:
    """Boss health remaining for a wipe (lower = better pull), normalised 0..100."""
    for raw in (boss_pct, fight_pct):
        if raw is None:
            continue
        try:
            v = float(raw)
        except (TypeError, ValueError):
            continue
        if v > 100:  # WCL sometimes reports hundredths
            v /= 100
        return round(max(0.0, v), 1)
    return None


def summarize_report(fights: list[dict]) -> list[dict]:
    """Collapse a report's fights into per-boss progression rows."""
    by_boss: dict[tuple, dict] = {}
    for f in fights:
        name = f.get("name")
        if not name:
            continue
        key = (name, f.get("difficulty"))
        boss = by_boss.setdefault(
            key,
            {"boss": name, "difficulty": f.get("difficulty"), "pulls": 0,
             "best_pct": None, "killed": False},
        )
        boss["pulls"] += 1
        if f.get("kill"):
            boss["killed"] = True
            boss["best_pct"] = 0.0
            continue
        pct = _remaining_pct(f.get("bossPercentage"), f.get("fightPercentage"))
        if pct is not None and (boss["best_pct"] is None or pct < boss["best_pct"]):
            boss["best_pct"] = pct
    return list(by_boss.values())


def prog_events(
    report_code: str, bosses: list[dict], *, threshold: float = 30.0, published_at: str | None = None
) -> list[FeedEvent]:
    """Build kill / best-pull FeedEvents, bucketed so improvements post once."""
    events: list[FeedEvent] = []
    for b in bosses:
        boss, diff, pulls = b["boss"], b.get("difficulty"), b.get("pulls", 0)
        label = f"{boss} ({diff})" if diff else boss
        if b["killed"]:
            events.append(
                FeedEvent(
                    source="wcl_prog",
                    event_type="wcl.kill",
                    external_id=f"{report_code}-{boss}-{diff}-kill",
                    title=f"💀 {label} defeated!",
                    summary=f"Down after {pulls} pull(s) this session. 🔥",
                    categories=["Progression"],
                    severity="info",
                    published_at=published_at,
                )
            )
            continue
        best = b["best_pct"]
        if best is None or best > threshold:
            continue  # not close enough yet — stay quiet
        bucket = int(best // 5 * 5)  # one post per ~5% improvement
        events.append(
            FeedEvent(
                source="wcl_prog",
                event_type="wcl.best-pull",
                external_id=f"{report_code}-{boss}-{diff}-best-{bucket}",
                title=f"🔥 Progress on {label} · best pull {best:.1f}%",
                summary=f"{pulls} pull(s) in. So close I can feel the heat.",
                categories=["Progression"],
                severity="warning",
                published_at=published_at,
            )
        )
    return events


class WclProgSource(FeedSource):
    name = "wcl_prog"

    def __init__(self, config: Config):
        self.config = config

    async def fetch(self, session) -> list[FeedEvent]:
        cfg = self.config
        if not (cfg.warcraftlogs_client_id and cfg.warcraftlogs_client_secret):
            return []
        from ..models import normalize_realm
        from ..services.warcraftlogs import WarcraftLogsClient

        async with WarcraftLogsClient(
            cfg.warcraftlogs_client_id,
            cfg.warcraftlogs_client_secret,
            cfg.warcraftlogs_zone_id or 0,
            session=session,
        ) as wcl:
            reports = await wcl.guild_reports(
                cfg.guild_name, normalize_realm(cfg.guild_realm), cfg.guild_region, limit=1
            )
            if not reports:
                return []
            report = reports[0]
            detail = await wcl.report_fights(report["code"])

        bosses = summarize_report(detail.get("fights") or [])
        return prog_events(
            report["code"], bosses,
            threshold=cfg.prog_alert_threshold,
            published_at=_iso_from_ms(detail.get("startTime")),
        )


def _iso_from_ms(ms) -> str | None:
    from datetime import datetime, timezone

    try:
        return datetime.fromtimestamp(float(ms) / 1000, tz=timezone.utc).isoformat()
    except (TypeError, ValueError):
        return None
