"""Living-boards persistence + the render/diff/self-heal engine."""

from __future__ import annotations

from ftabot.boards.base import Board, BoardPayload
from ftabot.boards.engine import refresh_board
from ftabot.config import Config
from ftabot.posting import PostGate, PostingMode

# A gate that lets the lifecycle tests post (everything to one sandbox channel).
_TEST_GATE = PostGate(PostingMode.TEST, "555")


# --- DB layer ---------------------------------------------------------------


async def test_board_crud(db):
    board_id = await db.add_board("hot20", "123", config={"size": 20})
    rows = await db.boards()
    assert len(rows) == 1
    row = rows[0]
    assert row["board_type"] == "hot20"
    assert row["channel_id"] == "123"
    assert row["config"] == {"size": 20}
    assert row["message_id"] is None

    await db.set_board_message(board_id, "999", "hash-a")
    fetched = await db.get_board(board_id)
    assert fetched["message_id"] == "999"
    assert fetched["content_hash"] == "hash-a"

    # disabling hides it from the enabled-only listing the loop uses
    assert await db.set_board_enabled(board_id, False)
    assert await db.boards(enabled_only=True) == []
    assert len(await db.boards(enabled_only=False)) == 1

    assert await db.delete_board(board_id)
    assert await db.boards(enabled_only=False) == []


# --- engine -----------------------------------------------------------------


class _FakeBoard(Board):
    board_type = "fake"

    def __init__(self, payload: BoardPayload | None):
        self.payload = payload

    async def render(self, db, config, board_config, emoji=None) -> BoardPayload | None:
        return self.payload


class _FakeMessenger:
    """Records send/edit calls; ``edit_ok=False`` simulates a vanished message."""

    def __init__(self, edit_ok: bool = True, start: int = 1000):
        self.sent: list[tuple[str, str]] = []
        self.edited: list[tuple[str, str, str]] = []
        self.edit_ok = edit_ok
        self._counter = start

    async def send(self, channel_id, payload) -> str:
        self.sent.append((channel_id, payload.fingerprint))
        self._counter += 1
        return str(self._counter)

    async def edit(self, channel_id, message_id, payload) -> bool:
        self.edited.append((channel_id, message_id, payload.fingerprint))
        return self.edit_ok


async def _refresh(db, board, board_id, messenger, gate=_TEST_GATE):
    row = await db.get_board(board_id)
    return await refresh_board(db, Config(), board, row, messenger, gate)


async def test_first_run_creates_and_stores(db):
    board_id = await db.add_board("fake", "chan")
    board = _FakeBoard(BoardPayload(fingerprint="v1", content="hello"))
    messenger = _FakeMessenger()

    action = await _refresh(db, board, board_id, messenger)

    assert action == "created"
    assert len(messenger.sent) == 1
    assert not messenger.edited
    stored = await db.get_board(board_id)
    assert stored["message_id"] is not None
    assert stored["content_hash"] == "v1"


async def test_unchanged_skips_the_edit(db):
    board_id = await db.add_board("fake", "chan")
    board = _FakeBoard(BoardPayload(fingerprint="v1", content="hello"))
    messenger = _FakeMessenger()

    await _refresh(db, board, board_id, messenger)  # created
    action = await _refresh(db, board, board_id, messenger)  # same fingerprint

    assert action == "unchanged"
    assert len(messenger.sent) == 1  # no second send
    assert not messenger.edited  # and crucially no edit


async def test_change_edits_in_place(db):
    board_id = await db.add_board("fake", "chan")
    board = _FakeBoard(BoardPayload(fingerprint="v1", content="hello"))
    messenger = _FakeMessenger()

    await _refresh(db, board, board_id, messenger)  # created
    board.payload = BoardPayload(fingerprint="v2", content="changed")
    action = await _refresh(db, board, board_id, messenger)

    assert action == "edited"
    assert len(messenger.edited) == 1
    assert (await db.get_board(board_id))["content_hash"] == "v2"


async def test_self_heals_a_deleted_message(db):
    board_id = await db.add_board("fake", "chan")
    board = _FakeBoard(BoardPayload(fingerprint="v1", content="hello"))
    created = _FakeMessenger()
    await _refresh(db, board, board_id, created)
    first_id = (await db.get_board(board_id))["message_id"]

    # the message was deleted: edit reports gone, engine re-creates
    board.payload = BoardPayload(fingerprint="v2", content="changed")
    healer = _FakeMessenger(edit_ok=False, start=5000)
    action = await _refresh(db, board, board_id, healer)

    assert action == "created"
    assert len(healer.edited) == 1  # it tried to edit first
    assert len(healer.sent) == 1  # then re-posted
    new_id = (await db.get_board(board_id))["message_id"]
    assert new_id is not None and new_id != first_id


async def test_render_none_posts_nothing(db):
    board_id = await db.add_board("fake", "555")
    board = _FakeBoard(None)
    messenger = _FakeMessenger()

    action = await _refresh(db, board, board_id, messenger)

    assert action == "skip"
    assert not messenger.sent and not messenger.edited


# --- posting guardrail: a board is invisible until confirmed + promoted -----


async def test_guardrail_off_blocks_everything(db):
    board_id = await db.add_board("fake", "prod-chan")
    board = _FakeBoard(BoardPayload(fingerprint="v1", content="hi"))
    messenger = _FakeMessenger()

    action = await _refresh(db, board, board_id, messenger, PostGate(PostingMode.OFF))

    assert action == "blocked"
    assert not messenger.sent and not messenger.edited
    # nothing was even stored, so the board has no live message
    assert (await db.get_board(board_id))["message_id"] is None


async def test_test_mode_redirects_to_the_test_channel(db):
    board_id = await db.add_board("fake", "prod-chan")  # production channel
    board = _FakeBoard(BoardPayload(fingerprint="v1", content="hi"))
    messenger = _FakeMessenger()

    gate = PostGate(PostingMode.TEST, "sandbox-chan")
    action = await _refresh(db, board, board_id, messenger, gate)

    assert action == "created"
    # it went to the sandbox, never the production channel
    assert messenger.sent[0][0] == "sandbox-chan"


async def test_live_keeps_unpromoted_boards_sandboxed(db):
    board_id = await db.add_board("fake", "prod-chan")
    board = _FakeBoard(BoardPayload(fingerprint="v1", content="hi"))
    messenger = _FakeMessenger()

    gate = PostGate(PostingMode.LIVE, "sandbox-chan")
    await _refresh(db, board, board_id, messenger, gate)

    assert messenger.sent[0][0] == "sandbox-chan"  # not yet promoted -> sandbox


async def test_promotion_reposts_in_the_production_channel(db):
    board_id = await db.add_board("fake", "prod-chan")
    board = _FakeBoard(BoardPayload(fingerprint="v1", content="hi"))

    # reviewed in the sandbox first
    sandbox = _FakeMessenger()
    await _refresh(db, board, board_id, sandbox, PostGate(PostingMode.TEST, "sandbox-chan"))
    assert sandbox.sent[0][0] == "sandbox-chan"

    # confirmed in full -> promote -> next refresh posts in production
    assert await db.set_board_promoted(board_id, True)
    assert (await db.get_board(board_id))["message_id"] is None  # dropped on promote
    live = _FakeMessenger(start=2000)
    action = await _refresh(db, board, board_id, live, PostGate(PostingMode.LIVE, "sandbox-chan"))

    assert action == "created"
    assert live.sent[0][0] == "prod-chan"  # seen by the community at last


async def test_posting_mode_flip_reposts_into_production(db):
    """A promoted board rolled out while POSTING_MODE=test still lands in the
    sandbox; flipping the mode to live (a config change, not a promotion) must
    re-post it into production even though the content has not changed. Without
    tracking where the message actually went, the engine would see a matching
    fingerprint and wrongly report "unchanged", leaving the board invisible.
    """
    board_id = await db.add_board("fake", "prod-chan")
    assert await db.set_board_promoted(board_id, True)
    board = _FakeBoard(BoardPayload(fingerprint="v1", content="hi"))

    # Rolled out under POSTING_MODE=test: the promoted board still goes to the
    # sandbox, and the stored message lives there.
    sandbox = _FakeMessenger()
    await _refresh(db, board, board_id, sandbox, PostGate(PostingMode.TEST, "sandbox-chan"))
    assert sandbox.sent[0][0] == "sandbox-chan"
    assert (await db.get_board(board_id))["posted_channel_id"] == "sandbox-chan"

    # Operator flips test -> live (no promote, no content change).
    live = _FakeMessenger(start=4000)
    action = await _refresh(db, board, board_id, live, PostGate(PostingMode.LIVE, "sandbox-chan"))

    assert action == "created"  # re-posted, not skipped as "unchanged"
    assert live.sent[0][0] == "prod-chan"
    assert (await db.get_board(board_id))["posted_channel_id"] == "prod-chan"

    # And once it is settled in production, an unchanged refresh is a no-op again.
    steady = _FakeMessenger(start=5000)
    assert await _refresh(db, board, board_id, steady, PostGate(PostingMode.LIVE, "sandbox-chan")) == "unchanged"
    assert not steady.sent and not steady.edited


# --- the bot-backed messenger never pings (safe for a live-server test channel)


class _FakeMessage:
    def __init__(self, message_id: int):
        self.id = message_id
        self.edits: list[dict] = []

    async def edit(self, **kwargs):
        self.edits.append(kwargs)


class _FakeChannel:
    def __init__(self):
        self.sends: list[dict] = []
        self.message = _FakeMessage(777)

    async def send(self, **kwargs):
        self.sends.append(kwargs)
        return self.message

    async def fetch_message(self, message_id):
        return self.message


class _FakeBot:
    def __init__(self, channel):
        self._channel = channel

    def get_channel(self, channel_id):
        return self._channel

    async def fetch_channel(self, channel_id):
        return self._channel


async def test_board_messenger_suppresses_all_pings():
    from ftabot.boards.messenger import BotBoardMessenger

    channel = _FakeChannel()
    messenger = BotBoardMessenger(_FakeBot(channel))
    # a payload that *contains* a role mention must still never notify anyone
    payload = BoardPayload(fingerprint="v1", content="<@&123> the Hot 20 moved")

    message_id = await messenger.send("555", payload)
    sent = channel.sends[0]["allowed_mentions"]
    assert (sent.everyone, sent.roles, sent.users) == (False, False, False)

    assert await messenger.edit("555", message_id, payload) is True
    edited = channel.message.edits[0]["allowed_mentions"]
    assert (edited.everyone, edited.roles, edited.users) == (False, False, False)


# --- Board base contract ----------------------------------------------------


async def test_board_render_is_abstract():
    # The base render is the documented contract; reaching it via super() raises.
    import pytest

    class _Bare(Board):
        board_type = "bare"

        async def render(self, db, config, board_config, emoji=None):
            return await super().render(db, config, board_config, emoji)

    with pytest.raises(NotImplementedError):
        await _Bare().render(None, None, {})


# --- messenger: channel-cache miss + vanished-message handling --------------


def _not_found():
    """A minimal discord.NotFound for a fake fetch (404)."""
    import discord

    class _Resp:
        status = 404
        reason = "Not Found"

    return discord.NotFound(_Resp(), "unknown message")


class _CacheMissBot:
    """get_channel misses the cache; fetch_channel is the fallback path."""

    def __init__(self, channel):
        self._channel = channel
        self.fetched = False

    def get_channel(self, channel_id):
        return None  # cache miss -> messenger must fall back to fetch_channel

    async def fetch_channel(self, channel_id):
        self.fetched = True
        return self._channel


async def test_messenger_falls_back_to_fetch_channel_on_cache_miss():
    from ftabot.boards.messenger import BotBoardMessenger

    channel = _FakeChannel()
    bot = _CacheMissBot(channel)
    messenger = BotBoardMessenger(bot)

    await messenger.send("555", BoardPayload(fingerprint="v1", content="hi"))
    assert bot.fetched is True  # the cache miss forced an API fetch
    assert channel.sends  # and the message went out


class _GoneChannel:
    """A channel whose message has been deleted: fetch_message 404s."""

    async def fetch_message(self, message_id):
        raise _not_found()


class _GoneBot:
    def get_channel(self, channel_id):
        return _GoneChannel()


async def test_messenger_edit_reports_a_vanished_message():
    from ftabot.boards.messenger import BotBoardMessenger

    messenger = BotBoardMessenger(_GoneBot())
    # The message is gone -> edit returns False so the engine can self-heal.
    ok = await messenger.edit("555", "777", BoardPayload(fingerprint="v1", content="x"))
    assert ok is False


async def test_messenger_delete_reports_an_already_gone_message():
    from ftabot.boards.messenger import BotBoardMessenger

    messenger = BotBoardMessenger(_GoneBot())
    # Deleting an already-deleted message returns False, mirroring edit.
    assert await messenger.delete("555", "777") is False


async def test_messenger_delete_removes_a_live_message():
    from ftabot.boards.messenger import BotBoardMessenger

    deleted: list[bool] = []

    class _Msg:
        async def delete(self):
            deleted.append(True)

    class _Chan:
        async def fetch_message(self, message_id):
            return _Msg()

    class _Bot:
        def get_channel(self, channel_id):
            return _Chan()

    messenger = BotBoardMessenger(_Bot())
    assert await messenger.delete("555", "777") is True
    assert deleted == [True]

