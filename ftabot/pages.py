"""Sticky pages: multi-message, GM-authored markdown pages the bot owns.

A *page* is hand-written Discord markdown (headers, ``-#`` subtext, lists,
emoji) posted as a short run of plain-content messages that stack into one
continuous wall post — the Rules & Info welcome page is the first. Unlike the
living boards (data-driven embeds in ``boards/``), a page's copy is the GM's
words verbatim: it lives in ``ftabot/content/page_<name>.json``, a dashboard
edit saves an override into the ``content_docs`` table, and the bundled file is
only the shipped default.

Placement follows the officer's explicit channel choice (``/page post``):

* **First post** — the page's messages are created in the requested channel.
* **Moved** — requested channel differs from where the live copy sits: the old
  messages are deleted, then the page is re-posted in the new channel.
* **Unchanged** — same channel, same copy: nothing happens at all.
* **Edited** — same channel but the copy changed (a dashboard save): each
  message is edited in place, no re-post, no scroll.

Like every outbound surface, the destination resolves through the posting
guardrail first. A page passes ``promoted=True`` (the channel is an officer's
explicit choice, same as feed-rule routing), so ``POSTING_MODE=off`` posts
nothing, ``test`` redirects the whole page to the sandbox channel, and ``live``
sends it where the officer pointed.

The engine is pure of Discord: it drives the same messenger protocol as the
boards (``send``/``edit`` plus ``delete``), so the whole lifecycle is
offline-testable. Pages post with ``allowed_mentions=none`` via the bot
messenger, so any ``<@…>`` mentions in the copy render without ever pinging.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from . import content
from .boards.base import BoardPayload
from .boards.rules import SafeDict, live_tokens, safe_format

if TYPE_CHECKING:
    from .config import Config
    from .db import Database
    from .posting import PostGate

# Discord's hard cap on bot message content; the dashboard editor enforces it.
MAX_POST_CHARS = 2000


@dataclass(frozen=True)
class PageDef:
    """One sticky page: its registry name and where its copy document lives."""

    name: str  # registry key, used by /page and the dashboard
    title: str  # friendly name for command choices and the dashboard
    content_doc: str  # content document name (content/<doc>.json + DB override)


# The sticky pages on offer. Adding a page = a bundled content/page_<name>.json
# plus one entry here; the /page choices and the dashboard section derive from it.
PAGES: dict[str, PageDef] = {
    "rules_info": PageDef(
        name="rules_info",
        title="Rules & Info",
        content_doc="page_rules_info",
    ),
    "guides_resources": PageDef(
        name="guides_resources",
        title="Guides & Resources",
        content_doc="page_guides_resources",
    ),
}


@dataclass
class PagePayload:
    """A rendered page: the ordered message texts + the diff fingerprint."""

    fingerprint: str
    posts: list[str]


async def load_page_doc(db: "Database", page: PageDef) -> dict:
    """The page's copy document: dashboard override first, bundled file second."""
    doc = await db.get_content_doc(page.content_doc)
    return doc if doc is not None else content.load(page.content_doc)


async def render_page(db: "Database", config: "Config", page: PageDef) -> PagePayload | None:
    """Build the page's message texts, or ``None`` when there is no copy.

    The copy is the GM's words and is posted verbatim (no voice sanitising),
    but it shares the managed-prose ``{tokens}`` with the Rules board, so live
    facts like ``{member_count}`` never drift stale. The fingerprint hashes the
    *substituted* text, so a live-fact change re-edits exactly like a copy edit.
    """
    doc = await load_page_doc(db, page)
    raw_posts = [p for p in doc.get("posts") or [] if isinstance(p, str) and p.strip()]
    if not raw_posts:
        return None
    tokens = SafeDict(await live_tokens(db, config))
    posts = [safe_format(p, tokens) for p in raw_posts]
    fingerprint = hashlib.sha1(
        json.dumps(posts, ensure_ascii=False).encode()
    ).hexdigest()
    return PagePayload(fingerprint=fingerprint, posts=posts)


class PageMessenger(Protocol):
    """How pages post: the boards messenger protocol plus ``delete``."""

    async def send(self, channel_id: str, payload: BoardPayload) -> str: ...

    async def edit(
        self, channel_id: str, message_id: str, payload: BoardPayload
    ) -> bool: ...

    async def delete(self, channel_id: str, message_id: str) -> bool: ...


def _payload(post: str) -> BoardPayload:
    """Wrap one message text for the shared bot messenger (content, no embed)."""
    return BoardPayload(fingerprint="", content=post)


async def _delete_messages(messenger: PageMessenger, channel_id: str | None, ids: list[str]) -> int:
    """Best-effort removal of a page's old messages; returns how many went."""
    deleted = 0
    for message_id in ids:
        if not channel_id:
            continue
        try:
            if await messenger.delete(channel_id, message_id):
                deleted += 1
        except Exception:  # noqa: BLE001 - a stuck delete must not block the move
            continue
    return deleted


async def refresh_page(
    db: "Database",
    config: "Config",
    page: PageDef,
    row: dict,
    messenger: PageMessenger,
    gate: "PostGate",
) -> str:
    """Reconcile one placed page with Discord. Returns the action taken.

    ``row`` is the ``sticky_pages`` record (see ``Database.get_sticky_page``).
    Actions mirror the boards engine: ``blocked`` (guardrail), ``skip`` (no
    copy), ``unchanged``, ``edited`` (copy changed, edited in place),
    ``created`` (first post), ``moved`` (channel changed: old copy deleted,
    re-posted), ``reposted`` (self-heal: a message vanished or the post count
    changed, so the page was re-posted cleanly in its channel).
    """
    channel_id = gate.resolve(row["channel_id"], promoted=True)
    if channel_id is None:
        return "blocked"  # guardrail: no confirmed destination, post nothing

    payload = await render_page(db, config, page)
    if payload is None:
        return "skip"

    message_ids = row.get("message_ids") or []
    same_channel = bool(message_ids) and row.get("posted_channel_id") == channel_id
    if same_channel:
        if row.get("content_hash") == payload.fingerprint:
            return "unchanged"  # diff-before-edit: the copy is current
        if len(message_ids) == len(payload.posts):
            edited_all = True
            for message_id, post in zip(message_ids, payload.posts):
                if not await messenger.edit(channel_id, message_id, _payload(post)):
                    edited_all = False  # a message vanished: re-post cleanly below
                    break
            if edited_all:
                await db.set_sticky_page_messages(
                    page.name, message_ids, payload.fingerprint, channel_id
                )
                return "edited"
        # Post count changed or a message is gone — fall through to a clean re-post.

    # First post, channel move, or self-heal: drop the old copy, post fresh.
    moved = bool(message_ids) and row.get("posted_channel_id") != channel_id
    await _delete_messages(messenger, row.get("posted_channel_id"), message_ids)
    new_ids = [await messenger.send(channel_id, _payload(post)) for post in payload.posts]
    await db.set_sticky_page_messages(page.name, new_ids, payload.fingerprint, channel_id)
    if not message_ids:
        return "created"
    return "moved" if moved else "reposted"


async def remove_page(db: "Database", row: dict, messenger: PageMessenger) -> int:
    """Delete a page's live messages and forget its placement entirely.

    Returns how many messages were actually deleted (already-gone ones don't
    count, but the placement is forgotten regardless).
    """
    deleted = await _delete_messages(
        messenger, row.get("posted_channel_id"), row.get("message_ids") or []
    )
    await db.delete_sticky_page(row["page_name"])
    return deleted
