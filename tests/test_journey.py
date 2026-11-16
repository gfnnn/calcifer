"""J0: People + the append-only journey log.

Covers the pure pieces (invite diff, roster match), the DB-backed recorders
(join / leave / rejoin, role diffs, baseline + reconcile, retention) and the
Discord listeners in ``cogs/journey.py`` driven by lightweight fakes. Offline:
a real temp-file SQLite database and no gateway.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import discord

from ftabot import journey
from ftabot.bot import FtaBot
from ftabot.cogs.admin import AdminCog
from ftabot.cogs.journey import InviteTracker, JourneyCog
from ftabot.config import Config
from ftabot.models import IdentityRecord, Member

from .test_cogs import _Bot, _Interaction, _Perms, _User

GUILD_ID = 4242


# --- fakes ------------------------------------------------------------------


class _Invite:
    def __init__(self, code, uses=0, guild=None):
        self.code, self.uses, self.guild = code, uses, guild


class _Forbidden(discord.Forbidden):
    def __init__(self):  # skip the aiohttp response plumbing
        pass


class _Guild:
    def __init__(self, invites=None, *, members=(), forbidden=False, features=(),
                 vanity=None, id=GUILD_ID):
        self.id = id
        self._invites = dict(invites or {})
        self.members = list(members)
        self.forbidden = forbidden
        self.features = list(features)
        self._vanity = vanity
        self.fetches = 0

    async def invites(self):
        self.fetches += 1
        if self.forbidden:
            raise _Forbidden()
        return [_Invite(code, uses) for code, uses in self._invites.items()]

    async def vanity_invite(self):
        return _Invite("guildname", self._vanity) if self._vanity is not None else None


class _R:
    def __init__(self, id, name):
        self.id, self.name = id, name


class _M:
    def __init__(self, id, name="newbie", *, guild=None, nick=None, roles=(),
                 bot=False, joined_at=None):
        self.id = id
        self.name = name
        self.nick = nick
        self.global_name = None
        self.display_name = nick or name
        self.guild = guild
        self.roles = list(roles)
        self.bot = bot
        self.joined_at = joined_at or datetime(2026, 11, 16, 12, tzinfo=timezone.utc)


class _JBot(_Bot):
    def __init__(self, db, config, guilds=()):
        super().__init__(db, config)
        self.guilds = list(guilds)

    def get_guild(self, gid):
        return next((g for g in self.guilds if g.id == gid), None)


def _cog(db, guild, **cfg):
    cfg.setdefault("discord_guild_id", GUILD_ID)
    return JourneyCog(_JBot(db, Config(journey_enabled=True, **cfg), [guild]))


async def _types(db, discord_id=None):
    return [e["type"] for e in await db.journey_events(discord_id=discord_id)]


# --- flag off = no behaviour change ------------------------------------------


def test_flag_defaults_off_and_reads_from_env(monkeypatch):
    assert Config().journey_enabled is False
    assert Config().journey_retention_days == 180
    monkeypatch.setenv("JOURNEY_ENABLED", "1")
    monkeypatch.setenv("JOURNEY_RETENTION_DAYS", "30")
    cfg = Config.from_env()
    assert cfg.journey_enabled is True and cfg.journey_retention_days == 30


async def test_members_intent_follows_the_flag(tmp_path, monkeypatch):
    path = str(tmp_path / "b.db")
    off = FtaBot(Config(database_path=path))
    assert off.intents.members is False
    assert off.intents.message_content is False

    on = FtaBot(Config(database_path=path, journey_enabled=True,
                       bot_commands_enabled=False))
    assert on.intents.members is True
    assert on.intents.message_content is False  # still never requested

    async def _sync(*, guild=None):
        return None

    monkeypatch.setattr(on.tree, "sync", _sync)
    try:
        await on.setup_hook()
        # The listeners load even with the command cogs switched off.
        assert set(on.cogs) == {"JourneyCog"}
    finally:
        await on.close()


# --- invite attribution (pure) -----------------------------------------------


def test_invite_diff_single_ambiguous_none_and_uncached():
    before = {"aaa": 3, "bbb": 0}
    assert journey.attribute_invite(before, {"aaa": 4, "bbb": 0}) == "aaa"
    assert journey.attribute_invite(before, {"aaa": 4, "bbb": 1}) == "unknown"
    assert journey.attribute_invite(before, {"aaa": 3, "bbb": 0}) == "unknown"
    # An invite created and used before we heard of it still attributes.
    assert journey.attribute_invite(before, {"aaa": 3, "bbb": 0, "ccc": 1}) == "ccc"
    assert journey.attribute_invite(None, {"aaa": 4}) == "unknown"
    assert journey.attribute_invite(before, None) == "unknown"


async def test_tracker_attributes_and_moves_its_baseline():
    guild = _Guild({"aaa": 1, "bbb": 5})
    tracker = InviteTracker()
    await tracker.refresh(guild)
    guild._invites["bbb"] = 6
    assert await tracker.attribute(guild) == "bbb"
    # Nothing moved since: the next join cannot be attributed.
    assert await tracker.attribute(guild) == "unknown"


async def test_tracker_follows_invite_create_and_delete():
    guild = _Guild({"aaa": 1})
    tracker = InviteTracker()
    tracker.created(_Invite("early"))  # before the cache exists: ignored
    tracker.deleted(_Invite("early"))
    await tracker.refresh(guild)
    tracker.created(_Invite("new"))
    guild._invites["new"] = 1
    assert await tracker.attribute(guild) == "new"
    tracker.deleted(_Invite("aaa"))
    assert "aaa" not in tracker.uses


async def test_tracker_counts_the_vanity_link():
    guild = _Guild({"aaa": 1}, features=["VANITY_URL"], vanity=10)
    tracker = InviteTracker()
    await tracker.refresh(guild)
    guild._vanity = 11
    assert await tracker.attribute(guild) == journey.VIA_VANITY


async def test_missing_manage_server_degrades_to_unknown_with_one_warning(caplog):
    guild = _Guild({"aaa": 1}, forbidden=True)
    tracker = InviteTracker()
    with caplog.at_level(logging.WARNING, logger="ftabot.journey"):
        await tracker.refresh(guild)
        assert await tracker.attribute(guild) == "unknown"
        assert await tracker.attribute(guild) == "unknown"
    warnings = [r for r in caplog.records if "Manage Server" in r.getMessage()]
    assert len(warnings) == 1


async def test_tracker_survives_a_discord_outage():
    class _Down(_Guild):
        async def invites(self):
            raise discord.HTTPException.__new__(discord.HTTPException)

    tracker = InviteTracker()
    assert await tracker.attribute(_Down()) == "unknown"


# --- roster match (pure) -------------------------------------------------------


def _roster():
    return [
        Member("Ashbringer", guild_rank=1, id=1),
        Member("Çinder", guild_rank=4, id=2),
        Member("Emberlyn", guild_rank=5, id=3),
        Member("Brann", realm_slug="silvermoon", guild_rank=6, id=4, is_main=False),
        Member("Brann", guild_rank=6, id=5),
    ]


def test_roster_match_is_case_and_accent_insensitive():
    match = journey.match_roster(["cinder"], _roster())
    assert match.member.character_name == "Çinder"
    assert match.confidence == journey.EXACT
    assert journey.match_roster(["ASHBRINGER"], _roster()).member.id == 1


def test_roster_match_handles_structured_nicknames():
    for nick in ("Emberlyn (Dave)", "Dave (Emberlyn)", "Emberlyn / Dave",
                 "Dave | Emberlyn", "Emberlyn - Dave"):
        match = journey.match_roster([nick], _roster())
        assert match.member.character_name == "Emberlyn", nick
        assert match.confidence == journey.LIKELY


def test_roster_match_falls_back_to_a_word_and_then_to_other_names():
    match = journey.match_roster(["Big Dave Emberlyn"], _roster())
    assert match.member.id == 3 and match.confidence == journey.POSSIBLE
    # The nickname says nothing useful; the username does.
    match = journey.match_roster([None, "", "Dave", "ashbringer"], _roster())
    assert match.member.id == 1 and match.confidence == journey.EXACT
    assert journey.match_roster(["Dave", "Al"], _roster()) is None
    assert journey.match_roster([], _roster()) is None


def test_roster_match_rank_name_and_same_name_preference():
    ranks = {("ashbringer", "hellfire"): "Guild Master"}
    assert journey.match_roster(["Ashbringer"], _roster(), ranks).rank_name == "Guild Master"
    # No GRM rank name: the number, plainly.
    assert journey.match_roster(["Emberlyn"], _roster(), ranks).rank_name == "Rank 5"
    # Two characters share a name: the main wins.
    assert journey.match_roster(["Brann"], _roster()).member.id == 5
    unranked = journey.match_roster(["Solo"], [Member("Solo")])
    assert unranked.rank_name is None


async def test_roster_match_for_reads_the_stored_roster_and_grm_ranks(db):
    assert await journey.roster_match_for(db, ["Ashbringer"]) is None  # empty roster
    await db.upsert_synced(Member("Ashbringer", guild_rank=3))
    await db.upsert_identities([IdentityRecord("Ashbringer", "hellfire", rank_name="Ashes")])
    match = await journey.roster_match_for(db, ["Ashbringer (Harry)"])
    assert match.member.character_name == "Ashbringer"
    assert match.rank_name == "Ashes"


# --- join / leave / rejoin ---------------------------------------------------


async def test_join_leave_rejoin_events(db):
    first = await journey.record_join(
        db, discord_id="1", display_name="Newbie",
        joined_at="2026-11-01T10:00:00+00:00", came_via="storefront02",
    )
    assert first.rejoin is False
    person = await db.get_person("1")
    assert person["came_via"] == "storefront02" and person["left_at"] is None
    assert await _types(db, "1") == ["joined", "came_via"]

    assert await journey.record_leave(db, "1", at="2026-11-02T10:00:00+00:00") is True
    assert await journey.record_leave(db, "1") is False  # already gone: no 2nd event
    assert await journey.record_leave(db, "nobody") is False
    assert (await db.get_person("1"))["left_at"] == "2026-11-02T10:00:00+00:00"

    again = await journey.record_join(
        db, discord_id="1", display_name="Newbie",
        joined_at="2026-11-10T10:00:00+00:00", came_via="unknown",
    )
    assert again.rejoin is True
    assert again.previous_joined_at == "2026-11-01T10:00:00+00:00"
    person = await db.get_person("1")
    assert person["left_at"] is None and person["joined_at"].startswith("2026-11-10")
    assert await _types(db, "1") == ["joined", "came_via", "left", "rejoined", "came_via"]
    rejoined = (await db.journey_events(type="rejoined"))[0]
    assert rejoined["data"] == {"previous_joined_at": "2026-11-01T10:00:00+00:00"}
    codes = [e["data"]["code"] for e in await db.journey_events(type="came_via")]
    assert codes == ["storefront02", "unknown"]


async def test_event_log_filters(db):
    await db.add_journey_event("1", "greeted", actor_id=77, at="2026-11-01T00:00:00+00:00")
    await db.add_journey_event("2", "greeted", at="2026-11-05T00:00:00+00:00")
    assert len(await db.journey_events()) == 2
    recent = await db.journey_events(type="greeted", since="2026-11-02T00:00:00+00:00")
    assert [e["discord_id"] for e in recent] == ["2"]
    assert (await db.journey_events(discord_id="1"))[0]["actor_id"] == "77"


# --- baseline + reconcile ----------------------------------------------------


def _present(*ids):
    return [journey.Present(str(i), f"user{i}", "2026-01-01T00:00:00+00:00") for i in ids]


async def test_baseline_backfill_is_idempotent_and_silent(db):
    mid = await db.upsert_synced(Member("Ashbringer"))
    await db.set_discord_link(mid, "2")

    result = await journey.reconcile(db, _present(1, 2, 3))
    assert result.baselined == 3
    people = await db.people()
    assert {p["came_via"] for p in people} == {"baseline"}
    assert await db.journey_events() == []  # the first week is not flooded
    # An existing officer link is carried over, also without an event.
    assert (await db.get_person("2"))["linked_main"] == "ashbringer|hellfire|eu"

    again = await journey.reconcile(db, _present(1, 2, 3))
    assert again == journey.Reconciled()
    assert len(await db.people()) == 3 and await db.journey_events() == []


async def test_a_join_before_the_baseline_is_not_overwritten(db):
    await journey.record_join(db, discord_id="9", display_name="Early", came_via="aaa")
    await journey.reconcile(db, _present(1, 9))
    assert (await db.get_person("9"))["came_via"] == "aaa"
    assert (await db.get_person("1"))["came_via"] == "baseline"


async def test_reconcile_repairs_what_a_restart_missed(db):
    await journey.reconcile(db, _present(1, 2))
    # While the bot was down: 3 joined, 2 left.
    result = await journey.reconcile(db, _present(1, 3))
    assert (result.missed_joins, result.missed_leaves) == (1, 1)
    assert (await db.get_person("3"))["came_via"] == "unknown"
    assert await _types(db, "3") == ["joined", "came_via"]
    assert await _types(db, "2") == ["left"]
    assert len(await db.people(present_only=True)) == 2
    # And 2 coming back during a later gap is a rejoin.
    result = await journey.reconcile(db, _present(1, 2, 3))
    assert result.missed_joins == 1
    assert "rejoined" in await _types(db, "2")


# --- role diffs + links --------------------------------------------------------


async def test_role_diff_events(db):
    await journey.reconcile(db, _present(1))
    assert await journey.record_roles_changed(db, "1", added=["Visitor"], removed=[]) is True
    assert await journey.record_roles_changed(db, "1", added=[], removed=[]) is False
    assert await journey.record_roles_changed(db, "404", added=["Ashes"], removed=[]) is False
    event = (await db.journey_events(type="roles_changed"))[0]
    assert event["data"] == {"added": ["Visitor"], "removed": []}


async def test_link_command_keeps_the_journey_in_step(db):
    await db.upsert_synced(Member("Ashbringer"))
    await journey.reconcile(db, _present(999))
    cog = AdminCog(_Bot(db, Config(journey_enabled=True)))
    it = _Interaction(perms=_Perms(manage_guild=True))
    await AdminCog.link.callback(cog, it, "Ashbringer", user=_User(id=999))

    assert (await db.get_person("999"))["linked_main"] == "ashbringer|hellfire|eu"
    event = (await db.journey_events(type="character_linked"))[0]
    assert event["actor_id"] == str(it.user.id)
    assert event["data"] == {"character": "ashbringer|hellfire|eu"}

    await AdminCog.link.callback(
        cog, _Interaction(perms=_Perms(manage_guild=True)), "Ashbringer", user=None
    )
    assert (await db.get_person("999"))["linked_main"] is None


async def test_link_for_someone_outside_the_journey_logs_nothing(db):
    member = Member("Ashbringer")
    assert await journey.link_character(db, "404", member) is False
    assert await db.journey_events() == []


async def test_link_command_with_the_flag_off_writes_no_journey_rows(db):
    await db.upsert_synced(Member("Ashbringer"))
    cog = AdminCog(_Bot(db, Config()))
    await AdminCog.link.callback(
        cog, _Interaction(perms=_Perms(manage_guild=True)), "Ashbringer",
        user=_User(id=999),
    )
    assert await db.people() == [] and await db.journey_events() == []


# --- retention -----------------------------------------------------------------


async def test_retention_forgets_only_the_long_departed(db):
    now = datetime(2026, 11, 16, tzinfo=timezone.utc)

    async def _joined_and_left(discord_id, days_ago):
        await journey.record_join(db, discord_id=discord_id, display_name="x")
        await journey.record_leave(
            db, discord_id, at=journey.iso(now - timedelta(days=days_ago))
        )

    await _joined_and_left("old", 200)
    await _joined_and_left("recent", 20)
    await journey.record_join(db, discord_id="here", display_name="y")

    assert await journey.prune(db, 0, now=now) == 0  # 0 = keep everything
    assert await journey.prune(db, 180, now=now) == 1
    assert await db.get_person("old") is None
    assert await _types(db, "old") == []
    assert await db.get_person("recent") is not None
    assert sorted(await _types(db, "recent")) == ["came_via", "joined", "left"]
    assert await _types(db, "here") == ["joined", "came_via"]


async def test_journey_tables_exist_and_reopen_cleanly(tmp_path):
    from ftabot.db import Database

    path = str(tmp_path / "j.db")
    async with Database(path) as first:
        await journey.record_join(first, discord_id="1", display_name="x")
    # A second connect re-runs SCHEMA (CREATE IF NOT EXISTS) and keeps the rows.
    async with Database(path) as again:
        cur = await again.conn.execute("PRAGMA table_info(people)")
        assert {r["name"] for r in await cur.fetchall()} == {
            "discord_id", "display_name", "joined_at", "left_at", "came_via",
            "linked_main", "first_seen", "updated_at",
        }
        cur = await again.conn.execute("PRAGMA table_info(journey_events)")
        assert {r["name"] for r in await cur.fetchall()} == {
            "id", "discord_id", "type", "actor_id", "at", "data_json",
        }
        assert len(await again.journey_events()) == 2


def test_iso_normalises_to_utc():
    naive = journey.iso(datetime(2026, 1, 1, 12))
    assert naive == "2026-01-01T12:00:00+00:00"
    paris = journey.iso(datetime(2026, 1, 1, 13, tzinfo=timezone(timedelta(hours=1))))
    assert paris == naive
    assert journey.iso(None).endswith("+00:00")


# --- the listeners ---------------------------------------------------------------


async def test_on_ready_baselines_humans_and_caches_invites(db, caplog):
    guild = _Guild({"aaa": 1}, members=[_M(1), _M(2), _M(3, bot=True)])
    cog = _cog(db, guild)
    with caplog.at_level(logging.INFO, logger="ftabot.journey"):
        await cog.on_ready()
    assert {p["discord_id"] for p in await db.people()} == {"1", "2"}
    assert cog.invites.uses == {"aaa": 1}
    # Counts only at INFO: no names, no ids.
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "2 baselined" in text and "newbie" not in text


async def test_on_ready_without_a_home_server_warns(db, caplog):
    cog = JourneyCog(_JBot(db, Config(journey_enabled=True), [_Guild(), _Guild(id=2)]))
    with caplog.at_level(logging.WARNING, logger="ftabot.journey"):
        await cog.on_ready()
    assert "no home server" in caplog.text and await db.people() == []
    # A single server needs no DISCORD_GUILD_ID.
    solo = JourneyCog(_JBot(db, Config(journey_enabled=True), [_Guild(members=[_M(1)])]))
    await solo.on_ready()
    assert len(await db.people()) == 1


async def test_member_join_records_the_invite_used(db):
    guild = _Guild({"storefront02": 7, "oldShared01": 2})
    cog = _cog(db, guild)
    await cog.on_ready()
    guild._invites["storefront02"] = 8
    await cog.on_member_join(_M(50, guild=guild))
    assert (await db.get_person("50"))["came_via"] == "storefront02"
    assert await _types(db, "50") == ["joined", "came_via"]


async def test_listeners_ignore_bots_and_other_servers(db):
    guild, other = _Guild(), _Guild(id=1)
    cog = _cog(db, guild)
    await cog.on_ready()
    await cog.on_member_join(_M(1, guild=guild, bot=True))
    await cog.on_member_join(_M(2, guild=other))
    await cog.on_member_remove(_M(1, guild=guild, bot=True))
    await cog.on_member_update(_M(2, guild=other), _M(2, guild=other, roles=[_R(1, "x")]))
    await cog.on_invite_create(_Invite("zzz", guild=other))
    await cog.on_invite_delete(_Invite("zzz", guild=other))
    assert await db.people() == [] and await db.journey_events() == []
    assert cog.invites.uses == {}


async def test_member_remove_and_invite_events(db):
    guild = _Guild({"aaa": 0})
    cog = _cog(db, guild)
    await cog.on_ready()
    await cog.on_member_join(_M(5, guild=guild))
    await cog.on_member_remove(_M(5, guild=guild))
    assert (await db.get_person("5"))["left_at"] is not None
    await cog.on_invite_create(_Invite("bbb", guild=guild))
    await cog.on_invite_delete(_Invite("aaa", guild=guild))
    assert cog.invites.uses == {"bbb": 0}


async def test_member_update_logs_role_diffs_and_renames_only(db):
    guild = _Guild()
    cog = _cog(db, guild)
    await cog.on_ready()
    await cog.on_member_join(_M(5, guild=guild))
    everyone, ashes, visitor = _R(1, "@everyone"), _R(2, "Ashes"), _R(3, "Visitor")

    before = _M(5, guild=guild, roles=[everyone, visitor])
    after = _M(5, guild=guild, roles=[everyone, ashes], nick="Emberlyn (Dave)")
    await cog.on_member_update(before, after)
    event = (await db.journey_events(type="roles_changed"))[0]
    assert event["data"] == {"added": ["Ashes"], "removed": ["Visitor"]}
    assert (await db.get_person("5"))["display_name"] == "Emberlyn (Dave)"

    # Something else changed (an avatar, say): nothing is logged.
    await cog.on_member_update(after, after)
    assert len(await db.journey_events(type="roles_changed")) == 1
