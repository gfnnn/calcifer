"""The dashboard Content section: the copy-editing API + HTML page.

Same offline TestClient approach as ``test_web``. Focus: the sync-token gate
(the editor changes what the whole Discord sees, so writes must be locked), the
validation that keeps a save postable (Discord's per-message limit), and the
override -> revert lifecycle.
"""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from ftabot.config import Config
from ftabot.db import Database
from ftabot.pages import MAX_POST_CHARS
from ftabot.web import create_app

_TOKEN = "edit-secret"
_AUTH = {"X-Sync-Token": _TOKEN}


def _client(tmp_path, **overrides) -> TestClient:
    cfg = Config(
        database_path=str(tmp_path / "content.db"),
        sync_token=_TOKEN,
        cookie_secure=False,
        **overrides,
    )
    return TestClient(create_app(cfg))


# --- auth ---------------------------------------------------------------------


def test_content_api_requires_token(tmp_path):
    with _client(tmp_path) as c:
        assert c.get("/api/content").status_code == 401
        assert c.get("/api/content/rules_info").status_code == 401
        assert c.put("/api/content/rules_info", json={"posts": ["x"]}).status_code == 401
        assert c.delete("/api/content/rules_info").status_code == 401
        assert c.get("/api/content", headers={"X-Sync-Token": "wrong"}).status_code == 401


def test_content_api_disabled_without_sync_token(tmp_path):
    cfg = Config(database_path=str(tmp_path / "nosync.db"))
    with TestClient(create_app(cfg)) as c:
        assert c.get("/api/content", headers=_AUTH).status_code == 503
        page = c.get("/content")
        assert page.status_code == 200
        assert "disabled" in page.text  # locked view explains how to enable


# --- read ---------------------------------------------------------------------


def test_content_list_and_get_bundled_copy(tmp_path):
    with _client(tmp_path) as c:
        docs = c.get("/api/content", headers=_AUTH).json()
        assert [d["page"] for d in docs] == ["rules_info", "guides_resources"]
        assert all(d["source"] == "bundled" for d in docs)

        doc = c.get("/api/content/rules_info", headers=_AUTH).json()
        assert doc["title"] == "Rules & Info"
        assert len(doc["posts"]) == 3
        assert "Welcome to From the Ashes" in doc["posts"][0]


def test_content_get_unknown_page_404s(tmp_path):
    with _client(tmp_path) as c:
        assert c.get("/api/content/nope", headers=_AUTH).status_code == 404
        assert (
            c.put("/api/content/nope", headers=_AUTH, json={"posts": ["x"]}).status_code
            == 404
        )
        assert c.delete("/api/content/nope", headers=_AUTH).status_code == 404


# --- save / validate / revert ----------------------------------------------------


def test_content_save_then_revert(tmp_path):
    with _client(tmp_path) as c:
        r = c.put(
            "/api/content/rules_info", headers=_AUTH, json={"posts": ["# New", "copy"]}
        )
        assert r.status_code == 200 and r.json() == {"status": "saved", "posts": 2}

        doc = c.get("/api/content/rules_info", headers=_AUTH).json()
        assert doc["source"] == "custom"
        assert doc["posts"] == ["# New", "copy"]
        assert doc["updated_at"]

        assert c.delete("/api/content/rules_info", headers=_AUTH).json() == {
            "status": "reverted"
        }
        doc = c.get("/api/content/rules_info", headers=_AUTH).json()
        assert doc["source"] == "bundled" and len(doc["posts"]) == 3
        # A second revert is a no-op, not an error.
        assert c.delete("/api/content/rules_info", headers=_AUTH).json() == {
            "status": "already_bundled"
        }


def test_content_save_validation(tmp_path):
    with _client(tmp_path) as c:
        put = lambda body: c.put("/api/content/rules_info", headers=_AUTH, json=body)  # noqa: E731
        assert put({}).status_code == 400  # no posts
        assert put({"posts": []}).status_code == 400  # empty list
        assert put({"posts": "one string"}).status_code == 400  # not a list
        assert put({"posts": ["ok", "   "]}).status_code == 400  # blank message
        assert put({"posts": ["ok", 42]}).status_code == 400  # not text
        assert put({"posts": ["x"] * 11}).status_code == 400  # too many messages

        over = "x" * (MAX_POST_CHARS + 1)
        r = put({"posts": [over]})
        assert r.status_code == 400
        assert str(MAX_POST_CHARS) in r.json()["detail"]

        bad = c.put(
            "/api/content/rules_info",
            headers={**_AUTH, "Content-Type": "application/json"},
            content="{not json",
        )
        assert bad.status_code == 400

        # Nothing of the above stuck: the copy is still the bundled default.
        assert c.get("/api/content/rules_info", headers=_AUTH).json()["source"] == "bundled"


def test_content_save_at_exact_limit_is_allowed(tmp_path):
    with _client(tmp_path) as c:
        r = c.put(
            "/api/content/rules_info", headers=_AUTH, json={"posts": ["x" * MAX_POST_CHARS]}
        )
        assert r.status_code == 200


# --- HTML page --------------------------------------------------------------------


def test_content_page_locked_until_unlocked(tmp_path):
    with _client(tmp_path) as c:
        locked = c.get("/content")
        assert "enter token" in locked.text
        assert "<textarea" not in locked.text

        wrong = c.get("/content", params={"token": "wrong"})
        assert "Invalid token" in wrong.text

        # The shared /admin unlock cookie opens the editor too.
        assert c.post("/api/sync/unlock", json={"token": _TOKEN}).status_code == 200
        page = c.get("/content")
        assert "<textarea" in page.text
        assert "Rules &amp; Info" in page.text
        assert "bundled copy" in page.text
        assert "Not placed in Discord yet" in page.text


def test_content_page_shows_custom_copy_and_placement(tmp_path):
    # Seed the override + a live placement on the same DB file, then serve it.
    path = str(tmp_path / "content.db")

    async def seed() -> None:
        db = Database(path)
        await db.connect()
        await db.set_content_doc("page_rules_info", {"posts": ["# Mine"]})
        await db.upsert_sticky_page("rules_info", "111")
        await db.set_sticky_page_messages("rules_info", ["1", "2"], "hash", "111")
        await db.close()

    asyncio.run(seed())
    cfg = Config(database_path=path, sync_token=_TOKEN, cookie_secure=False)
    with TestClient(create_app(cfg)) as c:
        page = c.get("/content", params={"token": _TOKEN})
        assert "custom copy" in page.text
        assert "# Mine" in page.text
        assert "2 message(s)" in page.text


def test_content_page_shows_registered_but_unposted_placement(tmp_path):
    # A page registered to a channel but with no live messages yet renders the
    # "Registered but not posted yet" hint (web.py:1156).
    path = str(tmp_path / "content.db")

    async def seed() -> None:
        db = Database(path)
        await db.connect()
        await db.upsert_sticky_page("rules_info", "111")  # registered, no messages
        await db.close()

    asyncio.run(seed())
    cfg = Config(database_path=path, sync_token=_TOKEN, cookie_secure=False)
    with TestClient(create_app(cfg)) as c:
        page = c.get("/content", params={"token": _TOKEN})
        assert "Registered but not posted yet" in page.text
