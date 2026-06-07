import asyncio

from fastapi.testclient import TestClient

from ftabot.config import Config
from ftabot.db import Database
from ftabot.models import IdentityRecord, LootEntry, Member
from ftabot.web import create_app


def test_safe_url_allows_only_http_schemes():
    # API/roster-sourced links are scheme-checked before reaching href/src, so a
    # stored javascript:/data: URL can't ride into the page (html.escape alone
    # would not neutralise the scheme).
    from ftabot.web import _safe_url

    assert _safe_url("https://raider.io/x") == "https://raider.io/x"
    assert _safe_url("http://example.com") == "http://example.com"
    assert _safe_url("HTTPS://EXAMPLE.COM") == "HTTPS://EXAMPLE.COM"
    assert _safe_url("javascript:alert(1)") is None
    assert _safe_url("data:text/html,<script>alert(1)</script>") is None
    assert _safe_url("") is None
    assert _safe_url(None) is None


def _seed(path: str) -> None:
    async def run() -> None:
        db = Database(path)
        await db.connect()
        await db.upsert_synced(
            Member(
                "Ashbringer",
                class_name="Paladin",
                role="TANK",
                mythic_plus_score=3000,
                item_level=632,
                best_parse_avg=88,
                source="wowaudit",
                details={"gear": ["headpiece"]},
            )
        )
        await db.upsert_synced(
            Member(
                "Emberlyn",
                class_name="Mage",
                role="DPS",
                mythic_plus_score=3200,
                item_level=630,
            )
        )
        await db.close()

    asyncio.run(run())


def _app(tmp_path):
    path = str(tmp_path / "web.db")
    _seed(path)
    cfg = Config(
        database_path=path,
        guild_name="From the Ashes",
        guild_realm="hellfire",
        guild_region="eu",
    )
    return create_app(cfg)


def test_member_page_surfaces_spine_identity_and_loot(tmp_path):
    path = str(tmp_path / "spine_web.db")

    async def seed() -> None:
        db = Database(path)
        await db.connect()
        await db.upsert_synced(Member("Calcifer", class_name="Mage", role="DPS"))
        await db.upsert_identities(
            [
                IdentityRecord(
                    character_name="Calcifer",
                    rank_name="Raider",
                    join_date="2020-01-15",
                    is_main=True,
                    alts=["Emberling-Hellfire"],
                )
            ]
        )
        await db.add_loot(
            [
                LootEntry(
                    character_name="Calcifer",
                    item_id=212396,
                    item_name="Ember Greatsword",
                    boss="Nexus-King",
                    difficulty="Mythic",
                    response="BiS",
                    awarded_at="2026-06-01T20:15:30",
                    source="rclc",
                )
            ]
        )
        await db.close()

    asyncio.run(seed())
    cfg = Config(database_path=path, guild_name="From the Ashes")
    with TestClient(create_app(cfg)) as client:
        html = client.get("/member/Calcifer").text
        # GRM identity is now visible on the profile.
        assert "joined 2020-01-15" in html
        assert "rank Raider" in html
        assert "main character" in html
        # And the real RCLootCouncil ledger drives the loot history table.
        assert "Ember Greatsword" in html
        assert "Nexus-King" in html


def test_healthz(tmp_path):
    with TestClient(_app(tmp_path)) as client:
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok", "members": 2}


def test_api_members_sorted_by_score(tmp_path):
    with TestClient(_app(tmp_path)) as client:
        resp = client.get("/api/members")
        names = [m["character_name"] for m in resp.json()]
        assert names == ["Emberlyn", "Ashbringer"]  # M+ score descending


def test_api_member_detail_and_404(tmp_path):
    with TestClient(_app(tmp_path)) as client:
        resp = client.get("/api/members/Ashbringer")
        assert resp.status_code == 200
        body = resp.json()
        assert body["class"] == "Paladin"
        assert body["source"] == "wowaudit"
        assert body["details"]["gear"] == ["headpiece"]

        assert client.get("/api/members/Nobody").status_code == 404


def test_api_search_by_class(tmp_path):
    with TestClient(_app(tmp_path)) as client:
        resp = client.get("/api/members", params={"class": "Mage"})
        names = [m["character_name"] for m in resp.json()]
        assert names == ["Emberlyn"]


def test_api_stats(tmp_path):
    with TestClient(_app(tmp_path)) as client:
        body = client.get("/api/stats").json()
        assert body["total"] == 2
        assert body["by_role"] == {"TANK": 1, "DPS": 1}
        assert body["max_mythic_plus_score"] == 3200


def test_index_html_renders(tmp_path):
    with TestClient(_app(tmp_path)) as client:
        resp = client.get("/")
        assert resp.status_code == 200
        assert "From the Ashes" in resp.text
        assert "Ashbringer" in resp.text
        assert "Emberlyn" in resp.text


def test_gm_endpoints(tmp_path):
    with TestClient(_app(tmp_path)) as client:
        api = client.get("/api/gm").json()
        assert api["counts"]["total"] == 2
        assert "health" in api
        page = client.get("/gm")
        assert page.status_code == 200
        assert "guild health" in page.text.lower()


def test_raid_endpoints(tmp_path):
    with TestClient(_app(tmp_path)) as client:
        api = client.get("/api/raid").json()
        assert api["count"] == 2
        assert {row["name"] for row in api["rows"]} == {"Ashbringer", "Emberlyn"}
        page = client.get("/raid")
        assert page.status_code == 200
        assert "Readiness board" in page.text


def test_loot_endpoints(tmp_path):
    with TestClient(_app(tmp_path)) as client:
        api = client.get("/api/loot").json()
        assert "fairness" in api and "recent" in api
        page = client.get("/loot")
        assert page.status_code == 200
        prio = client.get("/api/loot/priority", params={"item": "Nothing"}).json()
        assert prio["ranked"] == []


def test_member_page_and_links(tmp_path):
    with TestClient(_app(tmp_path)) as client:
        # Roster links to the internal detail page.
        assert "/member/Ashbringer" in client.get("/").text
        page = client.get("/member/Ashbringer")
        assert page.status_code == 200
        assert "Ashbringer" in page.text and "Readiness" in page.text
        assert client.get("/member/Ghost").status_code == 404


def test_nav_present_on_pages(tmp_path):
    with TestClient(_app(tmp_path)) as client:
        for path in ("/", "/gm", "/raid", "/loot"):
            assert "Raid Leader" in client.get(path).text


def test_sync_trigger_disabled_without_token(tmp_path):
    with TestClient(_app(tmp_path)) as client:
        # No SYNC_TOKEN configured -> trigger is disabled.
        assert client.post("/api/sync", params={"token": "x"}).status_code == 503


def test_admin_page_gates_on_token(tmp_path):
    path = str(tmp_path / "web.db")
    _seed(path)
    cfg = Config(database_path=path, sync_token="s3cret",
                 guild_realm="hellfire", guild_region="eu")
    with TestClient(create_app(cfg)) as client:
        assert "enter token" in client.get("/admin").text.lower()
        unlocked = client.get("/admin", params={"token": "s3cret"}).text
        assert "Sync now" in unlocked


def test_sync_trigger_runs(tmp_path, monkeypatch):
    import time
    import types

    import ftabot.web as web

    async def fake_sync(db, config):
        return types.SimpleNamespace(summary=lambda: "fake sync done")

    monkeypatch.setattr(web, "sync_guild", fake_sync)

    path = str(tmp_path / "web.db")
    _seed(path)
    cfg = Config(database_path=path, sync_token="s3cret",
                 guild_realm="hellfire", guild_region="eu")
    with TestClient(web.create_app(cfg)) as client:
        assert client.post("/api/sync", params={"token": "nope"}).status_code == 401
        started = client.post("/api/sync", params={"token": "s3cret"})
        assert started.status_code == 202
        assert started.json()["status"] == "started"

        status = {}
        for _ in range(60):
            status = client.get("/api/sync/status").json()
            if not status["running"] and status["last_summary"]:
                break
            time.sleep(0.05)
        assert status["last_summary"] == "fake sync done"
        assert status["last_error"] is None


def test_sync_unlock_cookie_keeps_token_out_of_url(tmp_path, monkeypatch):
    import time
    import types

    import ftabot.web as web

    async def fake_sync(db, config):
        return types.SimpleNamespace(summary=lambda: "fake sync done")

    monkeypatch.setattr(web, "sync_guild", fake_sync)

    path = str(tmp_path / "web.db")
    _seed(path)
    cfg = Config(database_path=path, sync_token="s3cret",
                 guild_realm="hellfire", guild_region="eu",
                 cookie_secure=False)  # TestClient speaks http://
    with TestClient(web.create_app(cfg)) as client:
        # No credential at all is rejected.
        assert client.post("/api/sync").status_code == 401
        # A wrong token sets no cookie.
        bad = client.post("/api/sync/unlock", json={"token": "nope"})
        assert bad.status_code == 401
        assert "fta_sync_token" not in client.cookies
        # Unlock stores the token in an http-only cookie...
        ok = client.post("/api/sync/unlock", json={"token": "s3cret"})
        assert ok.status_code == 200
        assert client.cookies.get("fta_sync_token") == "s3cret"
        # ...so the sync runs with no token in the URL (the cookie carries it)...
        assert client.post("/api/sync").status_code == 202
        # drain the background sync so it finishes before the loop closes
        for _ in range(60):
            if not client.get("/api/sync/status").json()["running"]:
                break
            time.sleep(0.05)
        # ...and the admin page renders unlocked from the cookie alone.
        assert "Sync now" in client.get("/admin").text


def test_sync_unlock_cookie_is_secure_by_default(tmp_path):
    path = str(tmp_path / "web.db")
    _seed(path)
    cfg = Config(database_path=path, sync_token="s3cret",
                 guild_realm="hellfire", guild_region="eu")  # cookie_secure defaults True
    with TestClient(create_app(cfg)) as client:
        ok = client.post("/api/sync/unlock", json={"token": "s3cret"})
        assert ok.status_code == 200
        # The Set-Cookie header carries the admin secret; it must be Secure so
        # it never rides plaintext http.
        assert "secure" in ok.headers["set-cookie"].lower()


def test_trends_endpoint_and_gm_charts(tmp_path):
    path = str(tmp_path / "web.db")
    _seed(path)
    cfg = Config(database_path=path, guild_name="From the Ashes",
                 guild_realm="hellfire", guild_region="eu")
    app = create_app(cfg)
    with TestClient(app) as client:
        # Seed two snapshots via the API-less path by hitting trends after sync
        # would need network; instead assert the endpoint shape is correct.
        data = client.get("/api/trends").json()
        assert "series" in data and "deltas" in data and "count" in data
