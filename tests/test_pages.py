"""Sticky pages: the render + reconcile engine (offline, fake messenger).

Covers the lifecycle the feature promises: first ``/page post`` creates the
messages, a different channel deletes the old copy and re-posts, the same
channel with unchanged copy does nothing, and a copy change is applied
by editing each message in place. Plus the guardrail (off/test/live) and the
self-heal paths (vanished message, post-count change).
"""

from __future__ import annotations

from ftabot import content
from ftabot.config import Config
from ftabot.models import Member
from ftabot.pages import (
    MAX_POST_CHARS,
    PAGES,
    _delete_messages,
    load_page_doc,
    refresh_page,
    remove_page,
    render_page,
)
from ftabot.posting import PostGate, PostingMode

RULES = PAGES["rules_info"]

_LIVE = PostGate(PostingMode.LIVE, "999")
_TEST = PostGate(PostingMode.TEST, "999")
_OFF = PostGate(PostingMode.OFF, "999")


class FakeMessenger:
    """Records sends/edits/deletes; ``dead`` ids fail like vanished messages."""

    def __init__(self):
        self.sent: list[tuple[str, str, str]] = []  # (channel, id, content)
        self.edited: list[tuple[str, str, str]] = []
        self.deleted: list[tuple[str, str]] = []
        self.dead: set[str] = set()
        self._next = 100

    async def send(self, channel_id, payload) -> str:
        self._next += 1
        message_id = str(self._next)
        self.sent.append((channel_id, message_id, payload.content))
        return message_id

    async def edit(self, channel_id, message_id, payload) -> bool:
        if message_id in self.dead:
            return False
        self.edited.append((channel_id, message_id, payload.content))
        return True

    async def delete(self, channel_id, message_id) -> bool:
        self.deleted.append((channel_id, message_id))
        return message_id not in self.dead


async def _place(db, channel_id="111"):
    await db.upsert_sticky_page(RULES.name, channel_id)
    return await db.get_sticky_page(RULES.name)


# --- registry + bundled copy --------------------------------------------------


def test_rules_info_page_is_registered():
    assert RULES.title == "Rules & Info"
    assert RULES.content_doc == "page_rules_info"


def test_bundled_copy_fits_discord_limits():
    doc = content.load(RULES.content_doc)
    posts = doc.get("posts")
    assert posts and all(isinstance(p, str) for p in posts)
    assert all(0 < len(p) <= MAX_POST_CHARS for p in posts)
    # The welcome page ships the GM's two posts (post one split under the limit).
    joined = "\n".join(posts)
    assert "Welcome to From the Ashes" in joined
    assert "Guild History" in joined


def test_guides_resources_page_is_registered():
    page = PAGES["guides_resources"]
    assert page.title == "Guides & Resources"
    doc = content.load(page.content_doc)
    posts = doc.get("posts")
    assert posts and len(posts) == 2  # the GM's two posts, as given
    assert all(0 < len(p) <= MAX_POST_CHARS for p in posts)
    joined = "\n".join(posts)
    assert "General WoW Resources" in joined
    assert "Content Creators" in joined


async def test_guides_resources_page_renders(db):
    payload = await render_page(db, Config(), PAGES["guides_resources"])
    assert len(payload.posts) == 2
    assert payload.posts[0].startswith("# 📚 Guides & Resources")


# --- render -------------------------------------------------------------------


async def test_render_uses_bundled_copy_by_default(db):
    payload = await render_page(db, Config(), RULES)
    assert payload is not None
    assert len(payload.posts) == 3
    assert payload.posts[0].startswith("# 🔥 Welcome to From the Ashes")


async def test_render_prefers_imported_override(db):
    await db.set_content_doc(RULES.content_doc, {"posts": ["# Custom rules"]})
    payload = await render_page(db, Config(), RULES)
    assert payload.posts == ["# Custom rules"]
    assert (await load_page_doc(db, RULES))["posts"] == ["# Custom rules"]


async def test_render_substitutes_live_tokens_and_keeps_unknown(db):
    await db.upsert_synced(Member("Emberlyn"))
    await db.upsert_synced(Member("Ashbringer"))
    await db.set_content_doc(
        RULES.content_doc, {"posts": ["We are {member_count} strong. {mystery}"]}
    )
    payload = await render_page(db, Config(), RULES)
    assert "We are 2 strong." in payload.posts[0]
    assert "{mystery}" in payload.posts[0]  # unknown token stays visible


async def test_render_returns_none_without_posts(db):
    await db.set_content_doc(RULES.content_doc, {"posts": ["   ", 42]})
    assert await render_page(db, Config(), RULES) is None


async def test_fingerprint_tracks_copy_and_live_facts(db):
    await db.set_content_doc(RULES.content_doc, {"posts": ["{member_count} of us."]})
    p1 = await render_page(db, Config(), RULES)
    p2 = await render_page(db, Config(), RULES)
    assert p1.fingerprint == p2.fingerprint
    await db.upsert_synced(Member("New"))  # live fact changes -> new fingerprint
    p3 = await render_page(db, Config(), RULES)
    assert p3.fingerprint != p1.fingerprint


# --- engine lifecycle -----------------------------------------------------------


async def test_first_refresh_creates_in_order(db):
    row = await _place(db)
    m = FakeMessenger()
    assert await refresh_page(db, Config(), RULES, row, m, _LIVE) == "created"
    assert [c for c, _, _ in m.sent] == ["111", "111", "111"]

    stored = await db.get_sticky_page(RULES.name)
    assert stored["message_ids"] == [mid for _, mid, _ in m.sent]
    assert stored["posted_channel_id"] == "111"
    assert stored["content_hash"]


async def test_second_refresh_is_unchanged(db):
    row = await _place(db)
    m = FakeMessenger()
    await refresh_page(db, Config(), RULES, row, m, _LIVE)
    row = await db.get_sticky_page(RULES.name)
    assert await refresh_page(db, Config(), RULES, row, m, _LIVE) == "unchanged"
    assert m.edited == [] and len(m.sent) == 3  # no extra traffic


async def test_copy_edit_edits_in_place(db):
    await db.set_content_doc(RULES.content_doc, {"posts": ["one", "two"]})
    row = await _place(db)
    m = FakeMessenger()
    await refresh_page(db, Config(), RULES, row, m, _LIVE)
    old_ids = (await db.get_sticky_page(RULES.name))["message_ids"]

    await db.set_content_doc(RULES.content_doc, {"posts": ["one EDITED", "two"]})
    row = await db.get_sticky_page(RULES.name)
    assert await refresh_page(db, Config(), RULES, row, m, _LIVE) == "edited"
    assert [(mid, text) for _, mid, text in m.edited] == [
        (old_ids[0], "one EDITED"),
        (old_ids[1], "two"),
    ]
    stored = await db.get_sticky_page(RULES.name)
    assert stored["message_ids"] == old_ids  # same messages, no re-post
    assert m.deleted == []


async def test_channel_move_deletes_old_copy_and_reposts(db):
    row = await _place(db, "111")
    m = FakeMessenger()
    await refresh_page(db, Config(), RULES, row, m, _LIVE)
    old_ids = (await db.get_sticky_page(RULES.name))["message_ids"]

    row = await _place(db, "222")  # the /page post move: only the target changes
    assert row["message_ids"] == old_ids  # old copy still tracked for deletion
    assert await refresh_page(db, Config(), RULES, row, m, _LIVE) == "moved"
    assert m.deleted == [("111", mid) for mid in old_ids]

    stored = await db.get_sticky_page(RULES.name)
    assert stored["posted_channel_id"] == "222"
    assert stored["message_ids"] != old_ids
    assert all(c == "222" for c, _, _ in m.sent[3:])


async def test_vanished_message_self_heals_with_repost(db):
    await db.set_content_doc(RULES.content_doc, {"posts": ["one", "two"]})
    row = await _place(db)
    m = FakeMessenger()
    await refresh_page(db, Config(), RULES, row, m, _LIVE)
    old_ids = (await db.get_sticky_page(RULES.name))["message_ids"]

    m.dead.add(old_ids[1])  # someone deleted the second message
    await db.set_content_doc(RULES.content_doc, {"posts": ["one!", "two!"]})
    row = await db.get_sticky_page(RULES.name)
    assert await refresh_page(db, Config(), RULES, row, m, _LIVE) == "reposted"
    stored = await db.get_sticky_page(RULES.name)
    assert stored["message_ids"] != old_ids
    assert len(stored["message_ids"]) == 2


async def test_post_count_change_reposts_cleanly(db):
    await db.set_content_doc(RULES.content_doc, {"posts": ["one", "two"]})
    row = await _place(db)
    m = FakeMessenger()
    await refresh_page(db, Config(), RULES, row, m, _LIVE)
    old_ids = (await db.get_sticky_page(RULES.name))["message_ids"]

    await db.set_content_doc(RULES.content_doc, {"posts": ["just the one now"]})
    row = await db.get_sticky_page(RULES.name)
    assert await refresh_page(db, Config(), RULES, row, m, _LIVE) == "reposted"
    assert m.deleted == [("111", mid) for mid in old_ids]  # no orphan messages
    stored = await db.get_sticky_page(RULES.name)
    assert len(stored["message_ids"]) == 1


async def test_empty_copy_skips_without_touching_messages(db):
    row = await _place(db)
    m = FakeMessenger()
    await refresh_page(db, Config(), RULES, row, m, _LIVE)
    await db.set_content_doc(RULES.content_doc, {"posts": []})
    row = await db.get_sticky_page(RULES.name)
    assert await refresh_page(db, Config(), RULES, row, m, _LIVE) == "skip"
    assert m.deleted == []  # the live copy stays up


# --- the posting guardrail ------------------------------------------------------


async def test_gate_off_blocks_everything(db):
    row = await _place(db)
    m = FakeMessenger()
    assert await refresh_page(db, Config(), RULES, row, m, _OFF) == "blocked"
    assert m.sent == []


async def test_gate_test_sandboxes_then_live_moves_home(db):
    row = await _place(db, "111")
    m = FakeMessenger()
    assert await refresh_page(db, Config(), RULES, row, m, _TEST) == "created"
    assert all(c == "999" for c, _, _ in m.sent)  # redirected to the sandbox

    # Flip to live: the copy sits in the sandbox, so it must move, not "unchanged".
    row = await db.get_sticky_page(RULES.name)
    assert await refresh_page(db, Config(), RULES, row, m, _LIVE) == "moved"
    assert m.deleted and all(c == "999" for c, _ in m.deleted)
    assert (await db.get_sticky_page(RULES.name))["posted_channel_id"] == "111"


async def test_gate_test_without_confirmed_channel_blocks(db):
    row = await _place(db)
    m = FakeMessenger()
    gate = PostGate(PostingMode.TEST, None)
    assert await refresh_page(db, Config(), RULES, row, m, gate) == "blocked"


# --- removal + db round-trips ----------------------------------------------------


async def test_remove_page_deletes_messages_and_placement(db):
    row = await _place(db)
    m = FakeMessenger()
    await refresh_page(db, Config(), RULES, row, m, _LIVE)
    row = await db.get_sticky_page(RULES.name)

    deleted = await remove_page(db, row, m)
    assert deleted == 3
    assert await db.get_sticky_page(RULES.name) is None


async def test_remove_page_counts_only_real_deletions(db):
    await db.set_content_doc(RULES.content_doc, {"posts": ["one", "two"]})
    row = await _place(db)
    m = FakeMessenger()
    await refresh_page(db, Config(), RULES, row, m, _LIVE)
    row = await db.get_sticky_page(RULES.name)
    m.dead.add(row["message_ids"][0])  # already gone in Discord
    assert await remove_page(db, row, m) == 1
    assert await db.get_sticky_page(RULES.name) is None


async def test_delete_messages_skips_when_no_channel():
    # A page registered but never posted has message ids with no channel: there's
    # nothing to delete, so the helper skips each id and reports zero.
    m = FakeMessenger()
    assert await _delete_messages(m, None, ["1", "2"]) == 0
    assert m.deleted == []  # never called delete with a missing channel


async def test_delete_messages_swallows_a_stuck_delete():
    # A delete that raises (a wedged channel) must not abort the whole move; the
    # helper logs it off and keeps counting the ones that went.
    class _PartlyStuck(FakeMessenger):
        async def delete(self, channel_id, message_id):
            if message_id == "boom":
                raise RuntimeError("Discord 500")
            return await super().delete(channel_id, message_id)

    m = _PartlyStuck()
    deleted = await _delete_messages(m, "111", ["ok1", "boom", "ok2"])
    assert deleted == 2  # the two good deletes counted; the raise was swallowed


async def test_sticky_pages_listing_and_corrupt_ids(db):
    assert await db.sticky_pages() == []
    await db.upsert_sticky_page("rules_info", "111")
    await db.conn.execute(
        "UPDATE sticky_pages SET message_ids_json = 'not json' WHERE page_name = ?",
        ("rules_info",),
    )
    await db.conn.commit()
    rows = await db.sticky_pages()
    assert rows[0]["message_ids"] == []  # corrupt JSON degrades to "not posted"
    assert await db.delete_sticky_page("rules_info") is True
    assert await db.delete_sticky_page("rules_info") is False


async def test_content_doc_roundtrip_and_revert(db):
    assert await db.get_content_doc("page_rules_info") is None
    await db.set_content_doc("page_rules_info", {"posts": ["a"]})
    assert (await db.get_content_doc("page_rules_info"))["posts"] == ["a"]
    assert await db.delete_content_doc("page_rules_info") is True
    assert await db.get_content_doc("page_rules_info") is None


async def test_content_doc_corrupt_json_reads_as_missing(db):
    await db.conn.execute(
        "INSERT INTO content_docs (name, body_json, updated_at) VALUES (?, ?, ?)",
        ("page_rules_info", "{broken", "now"),
    )
    await db.conn.commit()
    assert await db.get_content_doc("page_rules_info") is None
    # The page falls back to the bundled copy rather than vanishing.
    assert (await load_page_doc(db, RULES)).get("posts")
