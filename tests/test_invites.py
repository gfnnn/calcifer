"""D17: per-storefront invites.

``/invites create`` makes one permanent, unique invite per storefront and
stores its label; ``/invites list`` shows label, code and uses. The labels flow
through ``journey.invite_labels`` into the welcome card, and a join through a
freshly created invite is attributed to it.
"""

from __future__ import annotations

from ftabot import journey
from ftabot.bot import FtaBot
from ftabot.cogs.invites import InvitesCog, invite_rows
from ftabot.cogs.journey import JourneyCog
from ftabot.config import Config

from .test_cogs import _Interaction, _Perms
from .test_journey import _M, GUILD_ID, _Forbidden, _Guild, _Invite, _JBot
from .test_journey_cards import LAB, _Messenger

STOREFRONTS = ("Raider.IO", "Warcraft Logs", "WoWProgress", "Guilds of WoW",
               "Linktree", "In game")
RULES = "333"


class _InviteChannel:
    """Stands in for #rules-and-info: hands out fresh codes."""

    def __init__(self, guild, *, forbidden_after=None):
        self.id = int(RULES)
        self.guild = guild
        self.calls: list[dict] = []
        self.forbidden_after = forbidden_after

    async def create_invite(self, **kwargs):
        if self.forbidden_after is not None and len(self.calls) >= self.forbidden_after:
            raise _Forbidden()
        self.calls.append(kwargs)
        code = f"new{len(self.calls)}"
        self.guild._invites[code] = 0
        return _Invite(code, 0, self.guild)


class _IBot(_JBot):
    def __init__(self, db, config, guild, channel=None):
        super().__init__(db, config, [guild])
        self.channel = channel
        self.cogs_by_name: dict[str, object] = {}
        self.fetched = 0

    def get_channel(self, channel_id):
        return None  # force the fetch path

    async def fetch_channel(self, channel_id):
        self.fetched += 1
        return self.channel

    def get_cog(self, name):
        return self.cogs_by_name.get(name)


# The invites that predate per-storefront tracking, named through the env
# (JOURNEY_INVITE_LABELS). Placeholder codes: the real ones live in the host's .env.
OLD_LABELS = (("oldShared01", "Old shared link"), ("storefront02", "Storefronts (shared)"))


def _cfg(**over):
    base = dict(
        journey_invite_labels=OLD_LABELS,
        journey_enabled=True, welcome_cards_enabled=True, discord_guild_id=GUILD_ID,
        storefront_invites_enabled=True, invite_channel_id=RULES,
        posting_mode="test", test_channel_id=LAB,
    )
    base.update(over)
    return Config(**base)


def _setup(db, *, with_journey=True, forbidden_after=None, **over):
    guild = _Guild({"oldShared01": 40, "storefront02": 12})
    channel = _InviteChannel(guild, forbidden_after=forbidden_after)
    bot = _IBot(db, _cfg(**over), guild, channel)
    journey_cog = None
    if with_journey:
        journey_cog = JourneyCog(bot)
        journey_cog.messenger = _Messenger()
        bot.cogs_by_name["JourneyCog"] = journey_cog
    return InvitesCog(bot), journey_cog, guild, channel


def _officer(guild=None):
    it = _Interaction(perms=_Perms(manage_guild=True))
    it.guild = guild
    return it


# --- config -----------------------------------------------------------------


def test_flag_defaults_off_and_storefronts_are_configurable(monkeypatch):
    cfg = Config()
    assert cfg.storefront_invites_enabled is False and cfg.invite_channel_id is None
    assert cfg.invite_storefronts == STOREFRONTS
    monkeypatch.setenv("STOREFRONT_INVITES_ENABLED", "1")
    monkeypatch.setenv("INVITE_CHANNEL_ID", RULES)
    monkeypatch.setenv("INVITE_STOREFRONTS", "Raider.IO, Reddit")
    cfg = Config.from_env()
    assert cfg.storefront_invites_enabled and cfg.invite_channel_id == RULES
    assert cfg.invite_storefronts == ("Raider.IO", "Reddit")


async def test_cog_loads_only_with_the_flag(tmp_path, monkeypatch):
    async def _sync(*, guild=None):
        return None

    for enabled, expected in ((False, 10), (True, 11)):
        bot = FtaBot(Config(database_path=str(tmp_path / f"{enabled}.db"),
                            storefront_invites_enabled=enabled))
        monkeypatch.setattr(bot.tree, "sync", _sync)
        try:
            await bot.setup_hook()
            assert len(bot.cogs) == expected
            assert ("InvitesCog" in bot.cogs) is enabled
        finally:
            await bot.close()


# --- the label mapping ---------------------------------------------------------


async def test_label_layers_stored_then_env(db):
    # Nothing is built in: an unlabelled database knows no invites.
    assert await journey.invite_labels(db, Config()) == {}
    # An env label names a code that predates per-storefront tracking.
    old = Config(journey_invite_labels=OLD_LABELS)
    assert await journey.invite_labels(db, old) == dict(OLD_LABELS)
    await db.add_invite("abc123", "Raider.IO", channel_id=RULES, created_by="1")
    await db.add_invite("abc123", "Raider.IO")  # same code again: still one row
    labels = await journey.invite_labels(db, old)
    assert labels["abc123"] == "Raider.IO" and len(await db.invites()) == 1
    assert labels["oldShared01"] == "Old shared link"
    # An explicit env label has the last word over a stored one.
    override = Config(journey_invite_labels=(*OLD_LABELS, ("abc123", "RIO profile")))
    labels = await journey.invite_labels(db, override)
    assert labels["abc123"] == "RIO profile"
    assert labels["storefront02"] == "Storefronts (shared)"


def test_invite_rows_sort_by_label_and_carry_uses():
    labels = {"b": "Warcraft Logs", "a": "Raider.IO", "gone": "Linktree"}
    assert invite_rows(labels, {"a": 3, "b": 0}) == [
        ("Linktree", "gone", None), ("Raider.IO", "a", 3), ("Warcraft Logs", "b", 0),
    ]
    assert invite_rows({"a": "Raider.IO"}, None) == [("Raider.IO", "a", None)]


# --- /invites create -------------------------------------------------------------


async def test_create_makes_one_permanent_unique_invite_per_storefront(db):
    cog, _, guild, channel = _setup(db)
    it = _officer(guild)
    await InvitesCog.create.callback(cog, it)

    assert it.response.deferred and it.followup.ephemeral
    assert len(channel.calls) == 6
    for call in channel.calls:  # no expiry, no use limit, never a re-used code
        assert (call["max_age"], call["max_uses"], call["unique"]) == (0, 0, True)
    stored = {row["label"]: row for row in await db.invites()}
    assert tuple(stored) == STOREFRONTS
    assert stored["Raider.IO"]["code"] == "new1"
    assert stored["Raider.IO"]["channel_id"] == RULES
    assert stored["Raider.IO"]["created_by"] == str(it.user.id)
    assert "**Raider.IO**: `https://discord.gg/new1`" in it.followup.content
    # The old invites were not touched.
    assert {"oldShared01", "storefront02"} <= set(guild._invites)


async def test_create_is_idempotent_and_fills_only_gaps(db):
    cog, _, guild, channel = _setup(db)
    await InvitesCog.create.callback(cog, _officer(guild))
    again = _officer(guild)
    await InvitesCog.create.callback(cog, again)
    assert len(channel.calls) == 6  # nothing new
    assert "already has its invite" in again.followup.content

    # A storefront added to the config later gets its invite; the rest stay.
    bigger = InvitesCog(_IBot(db, _cfg(invite_storefronts=(*STOREFRONTS, "Reddit")),
                              guild, channel))
    more = _officer(guild)
    await InvitesCog.create.callback(bigger, more)
    assert len(channel.calls) == 7 and "**Reddit**" in more.followup.content


async def test_create_guards_and_explains_problems(db):
    cog, _, guild, channel = _setup(db)
    outsider = _Interaction()
    await InvitesCog.create.callback(cog, outsider)
    assert "Officer" in outsider.response.content and channel.calls == []

    unset, _, guild2, _ = _setup(db, invite_channel_id=None)
    it = _officer(guild2)
    await InvitesCog.create.callback(unset, it)
    assert "INVITE_CHANNEL_ID" in it.response.content

    # Create Invite missing from the start, and running out half way.
    blocked, _, guild3, _ = _setup(db, forbidden_after=0)
    it = _officer(guild3)
    await InvitesCog.create.callback(blocked, it)
    assert "No invites were created" in it.followup.content
    assert "Create Invite" in it.followup.content and await db.invites() == []

    partial, _, guild4, _ = _setup(db, forbidden_after=2)
    it = _officer(guild4)
    await InvitesCog.create.callback(partial, it)
    assert it.followup.content.count("https://discord.gg/") == 2
    assert "Create Invite" in it.followup.content and len(await db.invites()) == 2


async def test_create_works_without_the_journey_cog(db):
    cog, _, guild, channel = _setup(db, with_journey=False)
    await InvitesCog.create.callback(cog, _officer(guild))
    assert len(await db.invites()) == 6


# --- /invites list ------------------------------------------------------------------


async def test_list_shows_label_code_and_uses_including_env_labelled_old_invites(db):
    cog, _, guild, _ = _setup(db)
    await InvitesCog.create.callback(cog, _officer(guild))
    guild._invites["new1"] = 5
    guild._invites.pop("new6")  # someone deleted it in Discord
    it = _officer(guild)
    await InvitesCog.list_invites.callback(cog, it)
    lines = it.response.content.split("\n")
    assert it.response.ephemeral and len(lines) == 8
    assert "**Raider.IO** · `new1` · 5 uses" in lines
    assert "**Old shared link** · `oldShared01` · 40 uses" in lines
    assert "**Storefronts (shared)** · `storefront02` · 12 uses" in lines
    assert "**In game** · `new6` · no longer exists" in lines  # still listed, never dropped
    assert len(await db.invites()) == 6


async def test_list_without_manage_server_or_a_guild(db):
    cog, _, guild, _ = _setup(db)
    guild.forbidden = True
    it = _officer(guild)
    await InvitesCog.list_invites.callback(cog, it)
    assert "uses unknown" in it.response.content
    assert "Manage Server" in it.response.content

    nowhere = _officer(None)
    await InvitesCog.list_invites.callback(cog, nowhere)
    assert "**Old shared link** · `oldShared01` · uses unknown" in nowhere.response.content

    denied = _Interaction()
    await InvitesCog.list_invites.callback(cog, denied)
    assert "Officer" in denied.response.content


# --- attribution picks the new codes ------------------------------------------------


async def test_a_join_through_a_new_invite_is_attributed_and_labelled(db):
    cog, journey_cog, guild, _ = _setup(db)
    await journey_cog.on_ready()  # caches the two old invites
    await InvitesCog.create.callback(cog, _officer(guild))
    # The tracker was told about the new codes directly (no gateway event needed).
    assert journey_cog.invites.uses["new2"] == 0

    guild._invites["new2"] = 1  # someone arrives through the Warcraft Logs link
    await journey_cog.on_member_join(_M(175928847299117063, guild=guild))
    person = await db.get_person("175928847299117063")
    assert person["came_via"] == "new2"
    embed = journey_cog.messenger.sent[0][1]
    assert next(f.value for f in embed.fields if f.name == "Came via") == "Warcraft Logs"

    # The old shared link still attributes, under its new name.
    guild._invites["storefront02"] = 13
    await journey_cog.on_member_join(_M(175928847299117064, guild=guild))
    embed = journey_cog.messenger.sent[1][1]
    assert next(f.value for f in embed.fields if f.name == "Came via") == "Storefronts (shared)"


def test_diff_picks_a_brand_new_code_among_the_old_ones():
    before = {"oldShared01": 40, "storefront02": 12, "new1": 0, "new2": 0}
    after = {**before, "new1": 1}
    assert journey.attribute_invite(before, after) == "new1"
    # Never seen being created (no Manage Channels, so no gateway event): still found.
    assert journey.attribute_invite(
        {"oldShared01": 40, "storefront02": 12}, {**before, "new2": 1}
    ) == "new2"
    # Two storefronts in the same instant cannot be told apart.
    assert journey.attribute_invite(before, {**before, "new1": 1, "new2": 1}) == "unknown"
