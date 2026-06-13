"""Tests for the three previously-uncovered officer/member cogs: ``boards``,
``alerts``, and ``scores``.

Same approach as ``test_cogs.py`` — invoke each command's underlying coroutine
via ``Cog.command.callback(cog, interaction, ...)`` with a lightweight fake
Interaction and the real temp-file ``db`` fixture, staying fully offline. The
shared fakes (``_Interaction``, ``_Perms``, ``_Bot`` …) live in ``test_cogs``;
we reuse them here rather than re-declaring the harness.

Focus areas mirror the risk in each cog:

* **boards** — the off -> test -> promote guardrail flow and officer gating
  (the most safety-relevant cog: it decides what the community sees).
* **alerts** — subscription DB writes + the officer-only subscriber audit.
* **scores** — the Flamekeeper gate and the linked-character lookup.
"""

from __future__ import annotations

from discord import app_commands

from ftabot.cogs.alerts import AlertsCog
from ftabot.cogs.boards import BoardsCog
from ftabot.cogs.scores import ScoresCog
from ftabot.config import Config

from .test_cogs import (
    _Bot,
    _cfg,
    _Channel,
    _choice,
    _Interaction,
    _Perms,
    _seed,
    _User,
)


def _named_choice(name, value):
    """A Choice carrying a friendly display name (the real choices the cog sees),
    unlike ``test_cogs._choice`` which sets name == value."""
    return app_commands.Choice(name=name, value=value)


# --- boards cog -------------------------------------------------------------


async def test_board_create_denied_for_non_admin(db):
    cog = BoardsCog(_Bot(db, _cfg()))
    it = _Interaction(perms=_Perms())  # plain user
    await BoardsCog.create.callback(cog, it, _choice("hot20"), _Channel(123))
    assert it.response.ephemeral is True
    assert "Guild Master" in it.response.content
    assert await db.boards(enabled_only=False) == []  # nothing registered


async def test_board_create_then_list(db):
    cog = BoardsCog(_Bot(db, _cfg()))
    admin = _Interaction(perms=_Perms(manage_guild=True))
    await BoardsCog.create.callback(
        cog, admin, _named_choice("Calcifer's Hot 20", "hot20"), _Channel(123)
    )
    assert "Calcifer's Hot 20" in admin.response.content

    boards = await db.boards(enabled_only=False)
    assert len(boards) == 1
    assert boards[0]["board_type"] == "hot20"
    assert boards[0]["promoted"] is False  # starts sandboxed

    list_it = _Interaction(perms=_Perms(manage_guild=True))
    await BoardsCog.list_boards.callback(cog, list_it)
    assert "Calcifer's Hot 20" in list_it.response.content
    assert "sandboxed" in list_it.response.content


async def test_board_create_unknown_type(db):
    cog = BoardsCog(_Bot(db, _cfg()))
    it = _Interaction(perms=_Perms(manage_guild=True))
    await BoardsCog.create.callback(
        cog, it, _named_choice("Mystery", "not_a_board"), _Channel(123)
    )
    assert "Unknown board type" in it.response.content
    assert await db.boards(enabled_only=False) == []


async def test_board_create_duplicate_is_reported(db):
    cog = BoardsCog(_Bot(db, _cfg()))
    admin = _Interaction(perms=_Perms(manage_guild=True))
    await BoardsCog.create.callback(cog, admin, _choice("hot20"), _Channel(123))

    dupe = _Interaction(perms=_Perms(manage_guild=True))
    await BoardsCog.create.callback(cog, dupe, _choice("hot20"), _Channel(123))
    assert "already registered" in dupe.response.content
    assert len(await db.boards(enabled_only=False)) == 1  # no second row


async def test_board_list_empty(db):
    cog = BoardsCog(_Bot(db, _cfg()))
    it = _Interaction(perms=_Perms(manage_guild=True))
    await BoardsCog.list_boards.callback(cog, it)
    assert "No boards yet" in it.response.content


async def test_board_list_shows_disabled_state(db):
    board_id = await db.add_board("hot20", "123")
    await db.set_board_enabled(board_id, False)
    cog = BoardsCog(_Bot(db, _cfg()))
    it = _Interaction(perms=_Perms(manage_guild=True))
    await BoardsCog.list_boards.callback(cog, it)
    assert "disabled" in it.response.content


async def test_board_refresh_unknown_board_id(db):
    cog = BoardsCog(_Bot(db, _cfg()))
    it = _Interaction(perms=_Perms(manage_guild=True))
    await BoardsCog.refresh.callback(cog, it, 9999)
    assert "No board" in it.response.content


async def test_board_refresh_unknown_board_type(db):
    # A row whose type isn't in the REGISTRY (e.g. a renamed/removed board).
    board_id = await db.add_board("bogus_type", "123")
    cog = BoardsCog(_Bot(db, _cfg()))
    it = _Interaction(perms=_Perms(manage_guild=True))
    await BoardsCog.refresh.callback(cog, it, board_id)
    assert "unknown type" in it.response.content


async def test_board_refresh_blocked_by_guardrail(db):
    # Default config is POSTING_MODE=off, so the gate blocks every post.
    await _seed(db)
    board_id = await db.add_board("hot20", "123")
    cog = BoardsCog(_Bot(db, _cfg()))
    it = _Interaction(perms=_Perms(manage_guild=True))
    await BoardsCog.refresh.callback(cog, it, board_id)
    assert it.response.deferred is True
    assert "Guardrail held" in it.followup.content
    assert "off" in it.followup.content


async def test_board_promote_and_demote(db):
    board_id = await db.add_board("hot20", "123")
    cog = BoardsCog(_Bot(db, _cfg()))

    promote_it = _Interaction(perms=_Perms(manage_guild=True))
    await BoardsCog.promote.callback(cog, promote_it, board_id)
    assert "Promoted board" in promote_it.response.content
    assert (await db.get_board(board_id))["promoted"] is True

    demote_it = _Interaction(perms=_Perms(manage_guild=True))
    await BoardsCog.demote.callback(cog, demote_it, board_id)
    assert "sandboxed again" in demote_it.response.content
    assert (await db.get_board(board_id))["promoted"] is False


async def test_board_promote_unknown_id(db):
    cog = BoardsCog(_Bot(db, _cfg()))
    it = _Interaction(perms=_Perms(manage_guild=True))
    await BoardsCog.promote.callback(cog, it, 9999)
    assert "No board" in it.response.content


async def test_board_demote_unknown_id(db):
    cog = BoardsCog(_Bot(db, _cfg()))
    it = _Interaction(perms=_Perms(manage_guild=True))
    await BoardsCog.demote.callback(cog, it, 9999)
    assert "No board" in it.response.content


async def test_board_remove(db):
    board_id = await db.add_board("hot20", "123")
    cog = BoardsCog(_Bot(db, _cfg()))

    rm_it = _Interaction(perms=_Perms(manage_guild=True))
    await BoardsCog.remove.callback(cog, rm_it, board_id)
    assert "Removed board" in rm_it.response.content
    assert await db.get_board(board_id) is None

    miss_it = _Interaction(perms=_Perms(manage_guild=True))
    await BoardsCog.remove.callback(cog, miss_it, board_id)
    assert "to remove" in miss_it.response.content


async def test_board_commands_gated(db):
    """Every board command bounces a non-(GM/Discord-admin) before doing work."""
    cog = BoardsCog(_Bot(db, _cfg()))
    board_id = await db.add_board("hot20", "123")
    for invoke in (
        lambda it: BoardsCog.list_boards.callback(cog, it),
        lambda it: BoardsCog.refresh.callback(cog, it, board_id),
        lambda it: BoardsCog.promote.callback(cog, it, board_id),
        lambda it: BoardsCog.demote.callback(cog, it, board_id),
        lambda it: BoardsCog.remove.callback(cog, it, board_id),
    ):
        it = _Interaction(perms=_Perms())
        await invoke(it)
        assert it.response.ephemeral is True
        assert "Guild Master" in it.response.content
    # The denied refresh/promote/etc left the board untouched.
    assert (await db.get_board(board_id))["promoted"] is False


# --- alerts cog -------------------------------------------------------------


async def test_alerts_subscribe_then_list(db):
    cog = AlertsCog(_Bot(db, _cfg()))
    sub_it = _Interaction(user=_User(id=7))
    await AlertsCog.subscribe.callback(
        cog, sub_it, _named_choice("M+ runs", "raiderio_mplus")
    )
    assert sub_it.response.ephemeral is True
    assert "M+ runs" in sub_it.response.content
    assert "raiderio_mplus" in await db.user_subscriptions("7")

    list_it = _Interaction(user=_User(id=7))
    await AlertsCog.list_subs.callback(cog, list_it)
    assert "M+ runs" in list_it.response.content


async def test_alerts_list_empty(db):
    cog = AlertsCog(_Bot(db, _cfg()))
    it = _Interaction(user=_User(id=7))
    await AlertsCog.list_subs.callback(cog, it)
    assert "no active subscriptions" in it.response.content


async def test_alerts_unsubscribe_present_and_absent(db):
    cog = AlertsCog(_Bot(db, _cfg()))
    await AlertsCog.subscribe.callback(
        cog, _Interaction(user=_User(id=7)), _choice("wowhead")
    )

    remove_it = _Interaction(user=_User(id=7))
    await AlertsCog.unsubscribe.callback(cog, remove_it, _choice("wowhead"))
    assert "Removed" in remove_it.response.content
    assert await db.user_subscriptions("7") == []

    again_it = _Interaction(user=_User(id=7))
    await AlertsCog.unsubscribe.callback(cog, again_it, _choice("wowhead"))
    assert "weren't subscribed" in again_it.response.content


async def test_alerts_subscribers_denied_for_non_admin(db):
    cog = AlertsCog(_Bot(db, _cfg()))
    it = _Interaction(perms=_Perms())
    await AlertsCog.subscribers.callback(cog, it, _choice("wowhead"))
    assert it.response.ephemeral is True
    assert "role" in it.response.content


async def test_alerts_subscribers_empty_then_listed(db):
    cog = AlertsCog(_Bot(db, _cfg()))

    empty_it = _Interaction(perms=_Perms(manage_guild=True))
    await AlertsCog.subscribers.callback(cog, empty_it, _choice("wowhead"))
    assert "Nobody is subscribed" in empty_it.response.content

    await AlertsCog.subscribe.callback(
        cog, _Interaction(user=_User(id=7)), _choice("wowhead")
    )
    listed_it = _Interaction(perms=_Perms(manage_guild=True))
    await AlertsCog.subscribers.callback(cog, listed_it, _choice("wowhead"))
    assert "<@7>" in listed_it.response.content
    assert "(1)" in listed_it.response.content


# --- scores cog -------------------------------------------------------------


async def test_refreshme_denied_for_non_admin(db):
    cog = ScoresCog(_Bot(db, _cfg()))
    it = _Interaction(perms=_Perms())
    await ScoresCog.refreshme.callback(cog, it)
    assert it.response.ephemeral is True
    assert "Flamekeepers" in it.response.content


async def test_refreshme_no_linked_characters(db):
    cog = ScoresCog(_Bot(db, _cfg()))
    it = _Interaction(user=_User(id=7), perms=_Perms(manage_guild=True))
    await ScoresCog.refreshme.callback(cog, it)
    assert it.response.ephemeral is True
    assert "No characters are linked" in it.response.content


async def test_refreshme_reports_no_changes(db, monkeypatch):
    from ftabot.cogs import scores as scores_mod

    await _seed(db)
    member = await db.get_member("Emberlyn", "hellfire", "eu")
    await db.set_discord_link(member.id, "7")

    async def _no_change(database, config, characters):
        return []

    async def _noop_boards(database, config, bot):
        return None

    monkeypatch.setattr(scores_mod, "refresh_scores", _no_change)
    monkeypatch.setattr(scores_mod, "refresh_boards", _noop_boards)

    cog = ScoresCog(_Bot(db, _cfg()))
    it = _Interaction(user=_User(id=7), perms=_Perms(manage_guild=True))
    await ScoresCog.refreshme.callback(cog, it)
    assert it.response.deferred is True
    assert "no score changes" in it.followup.content


async def test_refreshme_reports_changes(db, monkeypatch):
    from ftabot.cogs import scores as scores_mod

    await _seed(db)
    member = await db.get_member("Emberlyn", "hellfire", "eu")
    await db.set_discord_link(member.id, "7")

    async def _changed(database, config, characters):
        m = await database.get_member("Emberlyn", "hellfire", "eu")
        m.mythic_plus_score = 3500
        return [m]

    async def _noop_boards(database, config, bot):
        return None

    monkeypatch.setattr(scores_mod, "refresh_scores", _changed)
    monkeypatch.setattr(scores_mod, "refresh_boards", _noop_boards)

    cog = ScoresCog(_Bot(db, _cfg()))
    it = _Interaction(user=_User(id=7), perms=_Perms(manage_guild=True))
    await ScoresCog.refreshme.callback(cog, it)
    assert "New scores" in it.followup.content
    assert "Emberlyn" in it.followup.content
    assert "3500" in it.followup.content


def test_config_is_usable_without_env():
    # Guards the shared _cfg() helper this module leans on.
    assert isinstance(_cfg(), Config)
