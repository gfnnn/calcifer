"""Runtime configuration, loaded from environment variables (and a .env file).

Nothing here talks to Discord or any API; it just gathers settings so the rest
of the code can stay free of ``os.environ`` lookups.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:  # python-dotenv is convenient but not strictly required at runtime.
    from dotenv import load_dotenv

    load_dotenv()
except ModuleNotFoundError:  # pragma: no cover - only hit without the dep
    pass


def _clean(value: str | None) -> str | None:
    """Return a stripped value, treating empty strings as "unset"."""
    if value is None:
        return None
    value = value.strip()
    return value or None


@dataclass(frozen=True)
class Config:
    """All tunable settings for the bot and the sync tooling."""

    # Discord
    discord_token: str | None = None
    discord_guild_id: int | None = None
    admin_role_name: str = "Officer"
    # The GM's Discord user id, so Calcifer can address Harry in his own register.
    gm_discord_id: int | None = None
    # Master switch for the bot's slash commands. Set BOT_COMMANDS_ENABLED=0 on
    # production to skip loading the command cogs and clear any commands already
    # registered with Discord (the bot still logs in and runs feeds/presence).
    bot_commands_enabled: bool = True

    # WoW guild identity (used as defaults for commands and sync)
    guild_name: str = "From the Ashes"
    guild_realm: str = "hellfire"
    guild_region: str = "eu"

    # Guild-management tuning.
    officer_rank_max: int = 2  # guild_rank <= this counts as an officer
    activity_window_days: int = 14  # "active" if seen within this many days

    # Sync performance: skip re-enriching members refreshed within this many
    # hours (0 = always enrich), and optionally cap how many stale non-raiders
    # are deep-enriched per run (0 = no cap; raiders are always enriched).
    enrich_max_age_hours: int = 12
    enrich_limit: int = 0
    # Concurrent enrichment requests. Kept low so a full sync stays gentle on
    # memory and the Raider.IO rate limit, even on a small host.
    enrich_concurrency: int = 2

    database_path: str = "ftabot.db"

    # Inbound ingest listener (healthz + Raid-Helper webhook + addon upload).
    web_host: str = "0.0.0.0"
    web_port: int = 8080

    # All-in-one runner: refresh the roster every N minutes (0 = never).
    sync_interval_minutes: int = 0

    # Shared secret for the desktop-addon upload endpoint that feeds the data
    # spine (RCLootCouncil loot + GRM identity SavedVariables). Unset = the
    # upload route is disabled.
    savedvars_upload_secret: str | None = None

    # WoW Audit API (primary source of truth when set)
    wowaudit_api_key: str | None = None
    wowaudit_base_url: str = "https://wowaudit.com/v1"

    # Blizzard API (optional)
    blizzard_client_id: str | None = None
    blizzard_client_secret: str | None = None

    # Warcraft Logs API (optional). The raid tier (zone) is auto-detected from
    # the API by default; warcraftlogs_zone_id only pins a specific tier.
    warcraftlogs_client_id: str | None = None
    warcraftlogs_client_secret: str | None = None
    warcraftlogs_zone_id: int | None = None
    # Tier zone ids (oldest → newest) the recruitment scout reads parse history
    # from; falls back to the single warcraftlogs_zone_id when unset.
    warcraftlogs_zone_ids: tuple[int, ...] = ()

    # Recruitment scout tuning.
    recruit_min_item_level: float = 275.0
    recruit_roles_open: tuple[str, ...] = ("TANK", "HEALER", "DPS")
    # Raider.IO season slugs (oldest → newest) for the M+ trajectory. These
    # change each expansion/tier — override with RECRUIT_SEASONS.
    recruit_seasons: tuple[str, ...] = ("season-tww-3", "season-mn-1")
    # A roster slot is "weak" (worth scouting) below this team percentile.
    recruit_weak_threshold: float = 0.4

    # --- Integration hub (Calcifer feeds) ----------------------------------
    # Poll external feeds every N minutes (0 = feeds off).
    feed_poll_minutes: int = 0
    # Channel that catches feed alerts with no specific routing rule.
    feed_default_channel_id: str | None = None
    # Calcifer's flavour intensity: "off" / "light" / "full".
    persona_tone: str = "light"
    # Wowhead news RSS (blue posts / patch notes / general WoW news).
    wowhead_rss_url: str = "https://www.wowhead.com/news/rss/all"
    wowhead_enabled: bool = True

    # Blizzard official news RSS — a second source for the News board, archived
    # alongside Wowhead so the board shows more. The default points at the WoW
    # news feed; override the URL for a different (e.g. all-Blizzard) feed.
    blizzard_news_enabled: bool = True
    blizzard_news_rss_url: str = (
        "https://news.blizzard.com/en-us/world-of-warcraft/rss"
    )

    # Raider.IO Mythic+ feed (off by default — it polls many characters).
    mplus_feed_enabled: bool = False
    mplus_alert_min_level: int = 18  # only announce timed keys at/above this
    mplus_feed_member_cap: int = 40  # bound API calls (raiders first)

    # Raid-Helper webhook receiver (POST /webhooks/raidhelper).
    raidhelper_webhook_secret: str | None = None  # shared secret to verify pushes
    raidhelper_default_channel_id: str | None = None  # event mirror destination
    raidhelper_api_key: str | None = None  # for API backfill (optional)
    raidhelper_server_id: str | None = None

    # WoW Audit raid reminders (needs WOWAUDIT_API_KEY).
    raid_reminder_enabled: bool = False
    raid_reminder_window_hours: float = 6.0  # remind when a raid is this close
    # WoW Audit reports raid times in the team's local timezone; ``raid_tz`` is
    # that zone as an IANA name, so it tracks DST correctly across the CET<->CEST
    # switch (a fixed UTC offset would be an hour wrong for half the year).
    raid_tz: str = "Europe/Paris"

    # Warcraft Logs live-prog feed (the Wipefest role; needs WCL creds).
    prog_feed_enabled: bool = False
    prog_alert_threshold: float = 30.0  # only announce pulls at/under this boss%

    # Raider.IO affixes feed — announces this week's M+ rotation.
    affixes_feed_enabled: bool = False
    # Raider.IO realm progression feed — monitors raid standings on home realm.
    # realm_progress_raids is a tuple of Raider.IO raid slugs (oldest → newest).
    # Midnight Season 1: the three-wing tier is a single combined slug "tier-mn-1"
    # (Raider.IO description: "Tier 35 (VS / DR / MQD)" = Voidspire + Dreamrift +
    # March of Quel'Dorei).  When Sporefall opens, append "sporefall":
    #   REALM_PROGRESS_RAIDS=tier-mn-1,sporefall
    realm_progress_feed_enabled: bool = False
    realm_progress_raids: tuple[str, ...] = ("tier-mn-1",)
    realm_progress_difficulty: str = "mythic"

    # YouTube channel uploads (keyless Atom feed per channel). Off until at least
    # one channel id is configured.
    youtube_feed_enabled: bool = False
    youtube_channel_ids: tuple[str, ...] = ()

    # WoWProgress guild-ranking feed (their JSON Data Export; no RSS exists).
    # Announces our guild's world/realm rank movement. Off by default.
    # wowprogress_rating optionally pins a tier/rating (e.g. "tier35"); unset =
    # the overall rank.
    wowprogress_feed_enabled: bool = False
    wowprogress_rating: str | None = None

    # --- Living boards ------------------------------------------------------
    # Re-render bot-owned board messages every N minutes (0 = boards off).
    board_refresh_minutes: int = 0
    # Recurring refresh of the active key-runners' M+ scores, then nudge the
    # boards. Bounded by mplus_feed_member_cap. 0 = off.
    score_refresh_minutes: int = 0

    # Digest flush interval (hours). Digest-mode rules buffer events until the
    # flush loop fires; 0 disables automatic flushing (manual /feed digest only).
    digest_flush_hours: int = 168  # default: once a week

    # --- Posting guardrail (deny-by-default; see posting.PostGate) ----------
    # "off" posts nothing; "test" redirects every post to test_channel_id;
    # "live" lets promoted surfaces reach their real channel.
    posting_mode: str = "off"
    # The confirmed sandbox channel that test/un-promoted posts land in.
    test_channel_id: str | None = None

    @property
    def wowaudit_enabled(self) -> bool:
        return bool(self.wowaudit_api_key)

    @property
    def blizzard_enabled(self) -> bool:
        return bool(self.blizzard_client_id and self.blizzard_client_secret)

    @property
    def warcraftlogs_enabled(self) -> bool:
        # The zone (raid tier) is auto-detected, so only credentials are needed.
        return bool(self.warcraftlogs_client_id and self.warcraftlogs_client_secret)

    @property
    def scout_credentials(self) -> bool:
        """The scout's parse-based discovery needs WCL creds + at least one zone."""
        return bool(
            self.warcraftlogs_client_id
            and self.warcraftlogs_client_secret
            and self.scout_zone_ids
        )

    @property
    def scout_zone_ids(self) -> tuple[int, ...]:
        """Tier zones for discovery + parse curve (newest is used for scanning)."""
        if self.warcraftlogs_zone_ids:
            return self.warcraftlogs_zone_ids
        return (self.warcraftlogs_zone_id,) if self.warcraftlogs_zone_id else ()

    @property
    def feeds_enabled(self) -> bool:
        """The feed loop runs only when polling is on and a destination exists."""
        return self.feed_poll_minutes > 0 and bool(self.feed_default_channel_id)

    @property
    def boards_enabled(self) -> bool:
        """The board refresh loop runs only when a refresh interval is set."""
        return self.board_refresh_minutes > 0

    @property
    def scores_enabled(self) -> bool:
        """The recurring M+ score refresh runs only when an interval is set."""
        return self.score_refresh_minutes > 0

    @property
    def raidhelper_channel(self) -> str | None:
        """Where Raid-Helper event mirrors go (its own channel or the default)."""
        return self.raidhelper_default_channel_id or self.feed_default_channel_id

    @property
    def raid_timezone(self) -> tzinfo:
        """The guild's server timezone for raid/event wall-clock times.

        Resolves the IANA zone name in ``raid_tz`` (Europe/Paris by default),
        which tracks DST correctly across the CET<->CEST switch, falling back to
        UTC only if the name is unknown. This is the single handle every
        time-anchored surface (raid reminders, and the Weekly Events /
        raid-night boards) resolves "this week" / "next raid" against, so
        nothing hardcodes an offset. A fixed offset, if ever wanted, is itself a
        zone name (e.g. ``Etc/GMT-2`` for UTC+2), so no separate knob is needed.
        """
        try:
            return ZoneInfo(self.raid_tz)
        except (ZoneInfoNotFoundError, ValueError):
            return timezone.utc

    @classmethod
    def from_env(cls) -> "Config":
        def _int(name: str) -> int | None:
            raw = _clean(os.environ.get(name))
            if raw is None:
                return None
            try:
                return int(raw)
            except ValueError:
                return None

        def _float(name: str) -> float | None:
            raw = _clean(os.environ.get(name))
            if raw is None:
                return None
            try:
                return float(raw)
            except ValueError:
                return None

        def _str_list(name: str) -> tuple[str, ...]:
            raw = _clean(os.environ.get(name))
            if raw is None:
                return ()
            return tuple(p.strip() for p in raw.split(",") if p.strip())

        def _int_list(name: str) -> tuple[int, ...]:
            out: list[int] = []
            for part in _str_list(name):
                try:
                    out.append(int(part))
                except ValueError:
                    continue
            return tuple(out)

        def _bool(name: str, default: bool) -> bool:
            """Parse a boolean env var, case-insensitively (unset = default).

            Anything in the falsy set turns the flag off; everything else turns
            it on. Lowercasing matters: without it ``FALSE``/``No``/``Off`` would
            slip past the falsy set and read as *enabled*.
            """
            raw = _clean(os.environ.get(name))
            if raw is None:
                return default
            return raw.lower() not in {"0", "false", "no", "off"}

        return cls(
            discord_token=_clean(os.environ.get("DISCORD_TOKEN")),
            discord_guild_id=_int("DISCORD_GUILD_ID"),
            admin_role_name=_clean(os.environ.get("ADMIN_ROLE_NAME")) or "Officer",
            gm_discord_id=_int("GM_DISCORD_ID"),
            bot_commands_enabled=_bool("BOT_COMMANDS_ENABLED", True),
            guild_name=_clean(os.environ.get("GUILD_NAME")) or "From the Ashes",
            guild_realm=_clean(os.environ.get("GUILD_REALM")) or "hellfire",
            guild_region=(_clean(os.environ.get("GUILD_REGION")) or "eu").lower(),
            officer_rank_max=_int("OFFICER_RANK_MAX") or 2,
            activity_window_days=_int("ACTIVITY_WINDOW_DAYS") or 14,
            enrich_max_age_hours=(
                _int("ENRICH_MAX_AGE_HOURS")
                if _int("ENRICH_MAX_AGE_HOURS") is not None
                else 12
            ),
            enrich_limit=_int("ENRICH_LIMIT") or 0,
            enrich_concurrency=_int("ENRICH_CONCURRENCY") or 2,
            database_path=_clean(os.environ.get("DATABASE_PATH")) or "ftabot.db",
            web_host=_clean(os.environ.get("WEB_HOST")) or "0.0.0.0",
            # Most hosts inject the port to bind via $PORT.
            web_port=_int("PORT") or _int("WEB_PORT") or 8080,
            sync_interval_minutes=_int("SYNC_INTERVAL_MINUTES") or 0,
            savedvars_upload_secret=_clean(os.environ.get("SAVEDVARS_UPLOAD_SECRET")),
            wowaudit_api_key=_clean(os.environ.get("WOWAUDIT_API_KEY")),
            wowaudit_base_url=(
                _clean(os.environ.get("WOWAUDIT_BASE_URL"))
                or "https://wowaudit.com/v1"
            ),
            blizzard_client_id=_clean(os.environ.get("BLIZZARD_CLIENT_ID")),
            blizzard_client_secret=_clean(os.environ.get("BLIZZARD_CLIENT_SECRET")),
            warcraftlogs_client_id=_clean(os.environ.get("WARCRAFTLOGS_CLIENT_ID")),
            warcraftlogs_client_secret=_clean(
                os.environ.get("WARCRAFTLOGS_CLIENT_SECRET")
            ),
            warcraftlogs_zone_id=_int("WARCRAFTLOGS_ZONE_ID"),
            warcraftlogs_zone_ids=_int_list("WARCRAFTLOGS_ZONE_IDS"),
            recruit_min_item_level=(
                _float("RECRUIT_MIN_ITEM_LEVEL")
                if _float("RECRUIT_MIN_ITEM_LEVEL") is not None
                else 275.0
            ),
            recruit_roles_open=(
                _str_list("RECRUIT_ROLES_OPEN") or ("TANK", "HEALER", "DPS")
            ),
            recruit_seasons=(
                _str_list("RECRUIT_SEASONS")
                or ("season-tww-3", "season-mn-1")
            ),
            recruit_weak_threshold=(
                _float("RECRUIT_WEAK_THRESHOLD")
                if _float("RECRUIT_WEAK_THRESHOLD") is not None
                else 0.4
            ),
            feed_poll_minutes=_int("FEED_POLL_MINUTES") or 0,
            feed_default_channel_id=_clean(os.environ.get("FEED_DEFAULT_CHANNEL_ID")),
            persona_tone=(_clean(os.environ.get("PERSONA_TONE")) or "light").lower(),
            wowhead_rss_url=(
                _clean(os.environ.get("WOWHEAD_RSS_URL"))
                or "https://www.wowhead.com/news/rss/all"
            ),
            wowhead_enabled=_bool("WOWHEAD_ENABLED", True),
            blizzard_news_enabled=_bool("BLIZZARD_NEWS_ENABLED", True),
            blizzard_news_rss_url=(
                _clean(os.environ.get("BLIZZARD_NEWS_RSS_URL"))
                or "https://news.blizzard.com/en-us/world-of-warcraft/rss"
            ),
            mplus_feed_enabled=_bool("MPLUS_FEED_ENABLED", False),
            mplus_alert_min_level=_int("MPLUS_ALERT_MIN_LEVEL") or 18,
            mplus_feed_member_cap=_int("MPLUS_FEED_MEMBER_CAP") or 40,
            raidhelper_webhook_secret=_clean(os.environ.get("RAIDHELPER_WEBHOOK_SECRET")),
            raidhelper_default_channel_id=_clean(
                os.environ.get("RAIDHELPER_DEFAULT_CHANNEL_ID")
            ),
            raidhelper_api_key=_clean(os.environ.get("RAIDHELPER_API_KEY")),
            raidhelper_server_id=_clean(os.environ.get("RAIDHELPER_SERVER_ID")),
            raid_reminder_enabled=_bool("RAID_REMINDER_ENABLED", False),
            raid_reminder_window_hours=(
                _float("RAID_REMINDER_WINDOW_HOURS")
                if _float("RAID_REMINDER_WINDOW_HOURS") is not None
                else 6.0
            ),
            raid_tz=(_clean(os.environ.get("RAID_TZ")) or "Europe/Paris"),
            prog_feed_enabled=_bool("PROG_FEED_ENABLED", False),
            prog_alert_threshold=(
                _float("PROG_ALERT_THRESHOLD")
                if _float("PROG_ALERT_THRESHOLD") is not None
                else 30.0
            ),
            affixes_feed_enabled=_bool("AFFIXES_FEED_ENABLED", False),
            realm_progress_feed_enabled=_bool("REALM_PROGRESS_FEED_ENABLED", False),
            realm_progress_raids=(
                _str_list("REALM_PROGRESS_RAIDS")
                or ("tier-mn-1",)
            ),
            realm_progress_difficulty=(
                (_clean(os.environ.get("REALM_PROGRESS_DIFFICULTY")) or "mythic").lower()
            ),
            youtube_feed_enabled=_bool("YOUTUBE_FEED_ENABLED", False),
            youtube_channel_ids=_str_list("YOUTUBE_CHANNEL_IDS"),
            wowprogress_feed_enabled=_bool("WOWPROGRESS_FEED_ENABLED", False),
            wowprogress_rating=_clean(os.environ.get("WOWPROGRESS_RATING")),
            board_refresh_minutes=_int("BOARD_REFRESH_MINUTES") or 0,
            score_refresh_minutes=_int("SCORE_REFRESH_MINUTES") or 0,
            digest_flush_hours=(
                _int("DIGEST_FLUSH_HOURS")
                if _int("DIGEST_FLUSH_HOURS") is not None
                else 168
            ),
            posting_mode=(_clean(os.environ.get("POSTING_MODE")) or "off").lower(),
            test_channel_id=_clean(os.environ.get("TEST_CHANNEL_ID")),
        )
