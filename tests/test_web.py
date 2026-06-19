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
        # Standalone web app (no bot wired in): body is unchanged.
        assert resp.json() == {"status": "ok", "members": 2}


class _FakeBot:
    def __init__(self, *, ready: bool, latency: float):
        self._ready = ready
        self._latency = latency

    def is_ready(self) -> bool:
        return self._ready

    def is_closed(self) -> bool:
        return False

    @property
    def latency(self) -> float:
        return self._latency


def test_healthz_reports_gateway_liveness_when_ready(tmp_path):
    app = _app(tmp_path)
    app.state.bot = _FakeBot(ready=True, latency=0.042)  # 42 ms
    with TestClient(app) as client:
        body = client.get("/healthz").json()
        assert body["status"] == "ok"
        assert body["bot"] == {"ready": True, "latency_ms": 42}


def test_healthz_degrades_but_stays_200_when_gateway_down(tmp_path):
    # The "process up, gateway dead" case: the host probe must still see 200 (no
    # restart storm on a transient reconnect), but the body flags it degraded.
    app = _app(tmp_path)
    app.state.bot = _FakeBot(ready=False, latency=float("nan"))
    with TestClient(app) as client:
        resp = client.get("/healthz")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "degraded"
        assert body["bot"] == {"ready": False, "latency_ms": None}


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


# --- admin control surfaces (boards / feeds / pages / settings / overview) ---


def _admin_app(tmp_path, **extra):
    path = str(tmp_path / "admin.db")
    _seed(path)
    cfg = Config(database_path=path, sync_token="s3cret", cookie_secure=False,
                 guild_realm="hellfire", guild_region="eu", **extra)
    return create_app(cfg)


def test_admin_endpoints_require_the_token(tmp_path):
    with TestClient(_admin_app(tmp_path)) as client:
        for path in ("/api/overview", "/api/boards", "/api/feeds", "/api/pages",
                     "/api/settings"):
            assert client.get(path).status_code == 401, path
        # And the gated HTML pages render the unlock form, not the data.
        assert "enter token" in client.get("/boards").text.lower()
        # With the token they open (200) and seed the cookie.
        assert client.get("/api/boards", params={"token": "s3cret"}).status_code == 200


def test_board_lifecycle_create_promote_config_delete(tmp_path):
    with TestClient(_admin_app(tmp_path)) as client:
        tok = {"token": "s3cret"}
        # An unknown type / non-numeric channel are rejected.
        assert client.post("/api/boards", params=tok,
                           json={"board_type": "nope", "channel_id": "1"}).status_code == 400
        assert client.post("/api/boards", params=tok,
                           json={"board_type": "hot20", "channel_id": "x"}).status_code == 400
        created = client.post("/api/boards", params=tok,
                              json={"board_type": "hot20", "channel_id": "555"})
        assert created.status_code == 201
        bid = created.json()["id"]
        # A duplicate (same type+channel) collides.
        assert client.post("/api/boards", params=tok,
                           json={"board_type": "hot20", "channel_id": "555"}).status_code == 409
        listing = client.get("/api/boards", params=tok).json()
        board = next(b for b in listing["boards"] if b["id"] == bid)
        assert board["state"] == "sandboxed" and board["promoted"] is False
        # Promote → state flips to live in the listing.
        assert client.post(f"/api/boards/{bid}/promote", params=tok).status_code == 200
        board = next(b for b in client.get("/api/boards", params=tok).json()["boards"]
                     if b["id"] == bid)
        assert board["state"] == "live"
        # Per-board config edit round-trips.
        assert client.put(f"/api/boards/{bid}/config", params=tok,
                          json={"config": {"highlights": 12}}).status_code == 200
        board = next(b for b in client.get("/api/boards", params=tok).json()["boards"]
                     if b["id"] == bid)
        assert board["config"] == {"highlights": 12}
        # Refresh with no bot wired in reports 'queued' (no immediate render).
        assert client.post(f"/api/boards/{bid}/refresh", params=tok).json()["status"] == "queued"
        # Delete, and a second delete 404s.
        assert client.delete(f"/api/boards/{bid}", params=tok).status_code == 200
        assert client.delete(f"/api/boards/{bid}", params=tok).status_code == 404


def test_feed_rule_and_mute_management(tmp_path):
    with TestClient(_admin_app(tmp_path)) as client:
        tok = {"token": "s3cret"}
        created = client.post("/api/feeds/rules", params=tok,
                              json={"source": "wowhead", "channel_id": "777",
                                    "contains": "hotfix, ptr"})
        assert created.status_code == 201
        rid = created.json()["id"]
        feeds = client.get("/api/feeds", params=tok).json()
        rule = next(r for r in feeds["rules"] if r["id"] == rid)
        assert rule["match"]["contains"] == ["hotfix", "ptr"]
        assert rule["enabled"] is True
        # Disable, then remove.
        assert client.post(f"/api/feeds/rules/{rid}/disable", params=tok).status_code == 200
        rule = next(r for r in client.get("/api/feeds", params=tok).json()["rules"]
                    if r["id"] == rid)
        assert rule["enabled"] is False
        assert client.delete(f"/api/feeds/rules/{rid}", params=tok).status_code == 200
        # Mute / unmute a source.
        client.post("/api/feeds/mute", params=tok, json={"source": "raiderio_mplus"})
        assert "raiderio_mplus" in client.get("/api/feeds", params=tok).json()["muted_sources"]
        client.delete("/api/feeds/mute/raiderio_mplus", params=tok)
        assert "raiderio_mplus" not in client.get("/api/feeds", params=tok).json()["muted_sources"]


def test_settings_override_and_revert(tmp_path):
    app = _admin_app(tmp_path, posting_mode="off")
    with TestClient(app) as client:
        tok = {"token": "s3cret"}
        # Default reflects the env config.
        rows = {r["key"]: r for r in client.get("/api/settings", params=tok).json()}
        assert rows["posting_mode"]["effective"] == "off"
        # Save a live override; it becomes effective and the overview reflects it.
        assert client.put("/api/settings", params=tok,
                          json={"settings": {"posting_mode": "live",
                                             "test_channel_id": "321"}}).status_code == 200
        ov = client.get("/api/overview", params=tok).json()
        assert ov["posting"]["mode"] == "live"
        assert ov["posting"]["test_channel_id"] == "321"
        # Invalid values are rejected.
        assert client.put("/api/settings", params=tok,
                          json={"settings": {"posting_mode": "sideways"}}).status_code == 400
        # Revert drops the override.
        assert client.delete("/api/settings/posting_mode", params=tok).status_code == 200
        rows = {r["key"]: r for r in client.get("/api/settings", params=tok).json()}
        assert rows["posting_mode"]["effective"] == "off"


def test_page_placement_endpoints_standalone(tmp_path):
    with TestClient(_admin_app(tmp_path)) as client:
        tok = {"token": "s3cret"}
        pages = client.get("/api/pages", params=tok).json()["pages"]
        name = pages[0]["name"]
        # Place it (no bot in this process → the render is queued, not posted).
        placed = client.post(f"/api/pages/{name}/place", params=tok,
                             json={"channel_id": "888"})
        assert placed.status_code == 200 and placed.json()["refresh"] == "queued"
        page = next(p for p in client.get("/api/pages", params=tok).json()["pages"]
                    if p["name"] == name)
        assert page["channel_id"] == "888" and page["placed"] is True
        # Remove forgets the placement.
        assert client.delete(f"/api/pages/{name}", params=tok).json()["status"] == "removed"
        page = next(p for p in client.get("/api/pages", params=tok).json()["pages"]
                    if p["name"] == name)
        assert page["placed"] is False


def test_overview_reports_counts_and_features(tmp_path):
    with TestClient(_admin_app(tmp_path)) as client:
        data = client.get("/api/overview", params={"token": "s3cret"}).json()
        assert data["counts"]["members"] == 2
        assert set(data["features"]) >= {"feeds", "boards", "scores", "wowaudit"}
        assert "mode" in data["posting"]
        assert "sync" in data


def test_gated_html_pages_render_when_unlocked(tmp_path):
    with TestClient(_admin_app(tmp_path)) as client:
        # Unlock once; the cookie then carries auth for the HTML pages.
        client.post("/api/sync/unlock", json={"token": "s3cret"})
        for path, marker in (
            ("/overview", "System overview"),
            ("/boards", "Create a board"),
            ("/feeds", "Add a routing rule"),
            ("/settings", "Runtime settings"),
        ):
            page = client.get(path)
            assert page.status_code == 200
            assert marker in page.text
            # The shared admin fetch helpers are wired into each page.
            assert "_api" in page.text


def test_gated_html_pages_lock_without_token(tmp_path):
    with TestClient(_admin_app(tmp_path)) as client:
        for path in ("/overview", "/boards", "/feeds", "/settings"):
            assert "enter token" in client.get(path).text.lower()


def test_admin_controls_disabled_without_sync_token(tmp_path):
    # No SYNC_TOKEN configured at all: the controls advertise how to enable them.
    path = str(tmp_path / "nodash.db")
    _seed(path)
    cfg = Config(database_path=path, guild_realm="hellfire", guild_region="eu")
    with TestClient(create_app(cfg)) as client:
        assert client.get("/api/boards").status_code == 503
        assert "disabled" in client.get("/settings").text.lower()


# --- Phase 2: recruiting funnel + member admin ------------------------------


def _seed_lead(path, **fields):
    async def run() -> None:
        db = Database(path)
        await db.connect()
        lead = {"name": "Newbie", "realm": "hellfire", "region": "eu",
                "class_name": "Mage", "role": "DPS", "score": 80, "source": "scout"}
        lead.update(fields)
        await db.upsert_lead(lead)
        await db.close()

    asyncio.run(run())


def test_recruiting_board_config_round_trips(tmp_path):
    with TestClient(_admin_app(tmp_path)) as client:
        tok = {"token": "s3cret"}
        # Set a role's slots, add then remove a rule, set then clear the message.
        assert client.put("/api/recruiting/slots/tank", params=tok,
                          json={"open": 2, "ilvl": 480, "note": "prefer survival"}).status_code == 200
        assert client.put("/api/recruiting/slots/nope", params=tok,
                          json={"open": 1}).status_code == 404
        assert client.post("/api/recruiting/rules", params=tok,
                           json={"text": "Be on time"}).status_code == 200
        assert client.post("/api/recruiting/rules", params=tok,
                           json={"text": ""}).status_code == 400
        client.put("/api/recruiting/message", params=tok, json={"text": "Join us"})
        data = client.get("/api/recruiting", params=tok).json()
        assert data["config"]["slots"]["tank"] == {"open": 2, "ilvl": 480, "note": "prefer survival"}
        assert data["config"]["rules"] == ["Be on time"]
        assert data["config"]["message"] == "Join us"
        assert data["roles"] == ["tank", "healer", "dps"]
        # Remove the rule by index, then reset wipes everything.
        assert client.delete("/api/recruiting/rules/0", params=tok).status_code == 200
        assert client.delete("/api/recruiting/rules/9", params=tok).status_code == 404
        assert client.post("/api/recruiting/reset", params=tok).status_code == 200
        assert client.get("/api/recruiting", params=tok).json()["config"] == {}


def test_lead_pipeline_state_note_delete(tmp_path):
    path = str(tmp_path / "admin.db")
    _seed(path)
    _seed_lead(path)
    cfg = Config(database_path=path, sync_token="s3cret", cookie_secure=False,
                 guild_realm="hellfire", guild_region="eu")
    with TestClient(create_app(cfg)) as client:
        tok = {"token": "s3cret"}
        lead = client.get("/api/recruiting", params=tok).json()["leads"][0]
        lid = lead["id"]
        assert lead["state"] == "new"
        # An unknown state is rejected; a valid one moves the lead.
        assert client.post(f"/api/leads/{lid}/state", params=tok,
                           json={"state": "sideways"}).status_code == 400
        assert client.post(f"/api/leads/{lid}/state", params=tok,
                           json={"state": "contacted"}).status_code == 200
        client.put(f"/api/leads/{lid}/note", params=tok, json={"note": "DM sent"})
        lead = client.get("/api/recruiting", params=tok).json()["leads"][0]
        assert lead["state"] == "contacted" and lead["notes"] == "DM sent"
        assert client.delete(f"/api/leads/{lid}", params=tok).status_code == 200
        assert client.delete(f"/api/leads/{lid}", params=tok).status_code == 404


def test_lead_capture_gated_on_credentials(tmp_path):
    # Without WCL credentials the capture trigger reports it's disabled.
    with TestClient(_admin_app(tmp_path)) as client:
        assert client.post("/api/leads/capture", params={"token": "s3cret"}).status_code == 503


def test_lead_capture_runs_the_scout_in_background(tmp_path, monkeypatch):
    from ftabot import scout

    async def fake_run_scout(db, config, *, only_class=None):
        return ["one-report"]

    def fake_lead_dicts(reports):
        return [{"name": "Scouted", "realm": "hellfire", "region": "eu",
                 "class_name": "Priest", "role": "HEALER", "score": 91, "source": "scout"}]

    monkeypatch.setattr(scout, "run_scout", fake_run_scout)
    monkeypatch.setattr(scout, "lead_dicts", fake_lead_dicts)

    path = str(tmp_path / "admin.db")
    _seed(path)
    cfg = Config(database_path=path, sync_token="s3cret", cookie_secure=False,
                 warcraftlogs_client_id="id", warcraftlogs_client_secret="sec",
                 warcraftlogs_zone_id=42, guild_realm="hellfire", guild_region="eu")
    with TestClient(create_app(cfg)) as client:
        tok = {"token": "s3cret"}
        started = client.post("/api/leads/capture", params=tok)
        assert started.status_code == 202
        # Wait for the background task to finish, then the lead is captured.
        for _ in range(50):
            cap = client.get("/api/recruiting", params=tok).json()["capture"]
            if not cap["running"]:
                break
        leads = client.get("/api/recruiting", params=tok).json()["leads"]
        assert any(le["name"] == "Scouted" for le in leads)


def test_member_admin_edits_manual_fields_and_alt_link(tmp_path):
    with TestClient(_admin_app(tmp_path)) as client:
        tok = {"token": "s3cret"}
        members = {m["name"]: m for m in
                   client.get("/api/admin/members", params=tok).json()["members"]}
        ember = members["Emberlyn"]
        # Set the plain manual fields.
        assert client.put(f"/api/admin/members/{ember['id']}", params=tok,
                          json={"raid_team": "Cinder", "officer_role": "Recruiter",
                                "notes": "trial", "discord_id": "42"}).status_code == 200
        # Link Emberlyn as an alt of Ashbringer (resolved by name).
        assert client.put(f"/api/admin/members/{ember['id']}", params=tok,
                          json={"main_character": "Ashbringer"}).status_code == 200
        # A non-existent main is rejected.
        assert client.put(f"/api/admin/members/{ember['id']}", params=tok,
                          json={"main_character": "Ghost"}).status_code == 400
        after = {m["name"]: m for m in
                 client.get("/api/admin/members", params=tok).json()["members"]}["Emberlyn"]
        assert after["raid_team"] == "Cinder" and after["officer_role"] == "Recruiter"
        assert after["discord_id"] == "42" and after["is_main"] is False
        assert after["main_name"] == "Ashbringer"
        # Search narrows the list.
        only = client.get("/api/admin/members", params={**tok, "q": "Ash"}).json()["members"]
        assert [m["name"] for m in only] == ["Ashbringer"]


def test_phase2_gated_pages_render_and_lock(tmp_path):
    with TestClient(_admin_app(tmp_path)) as client:
        client.post("/api/sync/unlock", json={"token": "s3cret"})
        assert "Recruitment funnel" in client.get("/recruiting").text
        assert "Member admin" in client.get("/members").text
    with TestClient(_admin_app(tmp_path)) as client2:
        assert "enter token" in client2.get("/recruiting").text.lower()
        assert "enter token" in client2.get("/members").text.lower()
