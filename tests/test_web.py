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
    cfg = Config(database_path=path, guild_name="From the Ashes",
                 sync_token="s3cret", cookie_secure=False)
    with TestClient(create_app(cfg)) as client:
        client.post("/api/sync/unlock", json={"token": "s3cret"})
        d = client.get("/api/members/Calcifer/detail").json()
        # GRM identity rides in the rich profile payload.
        assert d["identity"]["join_date"] == "2020-01-15"
        assert d["identity"]["rank_name"] == "Raider"
        assert d["identity"]["is_main"] is True
        # And the real RCLootCouncil ledger drives the loot history.
        assert any(e["item_name"] == "Ember Greatsword" for e in d["loot"])
        assert any(e["boss"] == "Nexus-King" for e in d["loot"])


def test_healthz(tmp_path):
    with TestClient(_app(tmp_path)) as client:
        resp = client.get("/healthz")
        assert resp.status_code == 200
        # Standalone web app (no bot wired in): body is unchanged.
        assert resp.json() == {"status": "ok", "members": 2}


def test_static_and_shell_revalidate_so_deploys_are_visible(tmp_path):
    # A redeploy must be picked up without a manual hard refresh: the shell and
    # its assets carry Cache-Control: no-cache (revalidate), while StaticFiles
    # still sends an ETag so an unchanged asset is a cheap 304, not a re-download.
    with TestClient(_app(tmp_path)) as client:
        css = client.get("/static/css/app.css")
        assert css.status_code == 200
        assert css.headers["cache-control"] == "no-cache"
        assert css.headers.get("etag")

        shell = client.get("/app")
        assert shell.status_code == 200
        assert shell.headers["cache-control"] == "no-cache"


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
    with TestClient(_admin_app(tmp_path)) as client:
        assert client.get("/api/members").status_code == 401  # now gated
        client.post("/api/sync/unlock", json={"token": "s3cret"})
        resp = client.get("/api/members")
        names = [m["character_name"] for m in resp.json()]
        assert names == ["Emberlyn", "Ashbringer"]  # M+ score descending


def test_api_member_detail_and_404(tmp_path):
    with TestClient(_admin_app(tmp_path)) as client:
        assert client.get("/api/members/Ashbringer").status_code == 401
        client.post("/api/sync/unlock", json={"token": "s3cret"})
        resp = client.get("/api/members/Ashbringer")
        assert resp.status_code == 200
        body = resp.json()
        assert body["class"] == "Paladin"
        assert body["source"] == "wowaudit"
        assert body["details"]["gear"] == ["headpiece"]

        assert client.get("/api/members/Nobody").status_code == 404


def test_api_member_detail_rich_payload(tmp_path):
    # The Roster drill-down endpoint: one call → readiness/attendance/wishlist/
    # loot/alts/identity. Gated like the rest of the member APIs.
    with TestClient(_admin_app(tmp_path)) as client:
        assert client.get("/api/members/Ashbringer/detail").status_code == 401
        client.post("/api/sync/unlock", json={"token": "s3cret"})
        d = client.get("/api/members/Ashbringer/detail").json()
        assert d["member"]["class"] == "Paladin"
        assert "readiness" in d and "components" in d["readiness"]
        assert "attendance" in d and "wishlist" in d and "loot" in d
        assert client.get("/api/members/Nobody/detail").status_code == 404


def test_api_identity_gated(tmp_path):
    with TestClient(_admin_app(tmp_path)) as client:
        assert client.get("/api/identity/Ashbringer").status_code == 401


def test_api_search_by_class(tmp_path):
    with TestClient(_admin_app(tmp_path)) as client:
        client.post("/api/sync/unlock", json={"token": "s3cret"})
        resp = client.get("/api/members", params={"class": "Mage"})
        names = [m["character_name"] for m in resp.json()]
        assert names == ["Emberlyn"]


def test_api_stats(tmp_path):
    with TestClient(_admin_app(tmp_path)) as client:
        assert client.get("/api/stats").status_code == 401
        client.post("/api/sync/unlock", json={"token": "s3cret"})
        body = client.get("/api/stats").json()
        assert body["total"] == 2
        assert body["by_role"] == {"TANK": 1, "DPS": 1}
        assert body["max_mythic_plus_score"] == 3200


def test_index_redirects_to_app_shell(tmp_path):
    # The legacy public roster page is gone; the root now sends browsers to the
    # unified single-shell dashboard.
    with TestClient(_app(tmp_path)) as client:
        resp = client.get("/", follow_redirects=False)
        assert resp.status_code in (307, 308)
        assert resp.headers["location"] == "/app"


def test_gm_endpoints(tmp_path):
    with TestClient(_admin_app(tmp_path)) as client:
        assert client.get("/api/gm").status_code == 401  # now gated
        client.post("/api/sync/unlock", json={"token": "s3cret"})
        api = client.get("/api/gm").json()
        assert api["counts"]["total"] == 2
        assert "health" in api


def test_raid_endpoints(tmp_path):
    with TestClient(_admin_app(tmp_path)) as client:
        assert client.get("/api/raid").status_code == 401
        client.post("/api/sync/unlock", json={"token": "s3cret"})
        api = client.get("/api/raid").json()
        assert api["count"] == 2
        assert {row["name"] for row in api["rows"]} == {"Ashbringer", "Emberlyn"}
        # The new Sign-ups endpoint is gated and returns grouped buckets.
        roster = client.get("/api/roster").json()
        assert "groups" in roster and "count" in roster


def test_api_roster_gated(tmp_path):
    with TestClient(_admin_app(tmp_path)) as client:
        assert client.get("/api/roster").status_code == 401


def test_loot_endpoints(tmp_path):
    with TestClient(_admin_app(tmp_path)) as client:
        assert client.get("/api/loot").status_code == 401
        assert client.get("/api/loot/priority", params={"item": "x"}).status_code == 401
        client.post("/api/sync/unlock", json={"token": "s3cret"})
        api = client.get("/api/loot").json()
        assert "fairness" in api and "recent" in api
        prio = client.get("/api/loot/priority", params={"item": "Nothing"}).json()
        assert prio["ranked"] == []


def test_sync_trigger_disabled_without_token(tmp_path):
    with TestClient(_app(tmp_path)) as client:
        # No SYNC_TOKEN configured -> trigger is disabled.
        assert client.post("/api/sync", params={"token": "x"}).status_code == 503


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
        # ...and a gated API authorises from the cookie alone.
        assert client.get("/api/overview").status_code == 200


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
                 guild_realm="hellfire", guild_region="eu",
                 sync_token="s3cret", cookie_secure=False)
    app = create_app(cfg)
    with TestClient(app) as client:
        assert client.get("/api/trends").status_code == 401  # now gated
        client.post("/api/sync/unlock", json={"token": "s3cret"})
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


def test_admin_controls_disabled_without_sync_token(tmp_path):
    # No SYNC_TOKEN configured at all: the gated APIs report the feature is off.
    path = str(tmp_path / "nodash.db")
    _seed(path)
    cfg = Config(database_path=path, guild_realm="hellfire", guild_region="eu")
    with TestClient(create_app(cfg)) as client:
        assert client.get("/api/boards").status_code == 503
        assert client.get("/api/settings").status_code == 503


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


def test_recruiting_and_members_apis_gated(tmp_path):
    # The /recruiting and /members surfaces are now the single-shell sections;
    # their data APIs are token-gated.
    with TestClient(_admin_app(tmp_path)) as client:
        assert client.get("/api/recruiting").status_code == 401
        assert client.get("/api/admin/members").status_code == 401
        client.post("/api/sync/unlock", json={"token": "s3cret"})
        assert client.get("/api/recruiting").status_code == 200
        assert client.get("/api/admin/members").status_code == 200


# --- rebuilt single-shell dashboard (Phase 0 scaffolding) -------------------

def test_app_shell_serves_unconditionally(tmp_path):
    # The shell loads without a token; auth is enforced by the JSON APIs, which
    # the client hydrates from (a 401 reveals the unlock overlay client-side).
    with TestClient(_app(tmp_path)) as client:
        resp = client.get("/app")
        assert resp.status_code == 200
        body = resp.text
        assert '<main id="app">' in body
        assert "/static/js/app.js" in body
        assert "/static/css/app.css" in body


def test_app_static_assets_served(tmp_path):
    with TestClient(_app(tmp_path)) as client:
        css = client.get("/static/css/app.css")
        assert css.status_code == 200
        assert "--ember" in css.text
        for module in ("app.js", "router.js", "api.js", "components.js"):
            asset = client.get(f"/static/js/{module}")
            assert asset.status_code == 200, module
        # The router exposes the seven planned sections.
        router = client.get("/static/js/router.js").text
        for key in ("home", "roster", "raid", "loot", "recruitment", "comms", "settings"):
            assert f"key: '{key}'" in router, key


# --- /api/home (Phase 1: pulse + triage) ------------------------------------

def test_api_home_gated_and_shaped(tmp_path):
    with TestClient(_admin_app(tmp_path)) as client:
        # Gated: no token -> 401.
        assert client.get("/api/home").status_code == 401
        client.post("/api/sync/unlock", json={"token": "s3cret"})
        data = client.get("/api/home").json()
        assert "pulse" in data and "attention" in data
        assert "sparklines" in data["pulse"]["trends"]
        assert data["attention_total"] == sum(it["count"] for it in data["attention"])
        assert "sync" in data  # live SyncManager status merged on by the endpoint


def test_app_phase4_section_assets_served(tmp_path):
    # Recruitment / Comms / Settings sections are pure JS ports over existing
    # (gated) APIs; assert their modules are served by the shell.
    with TestClient(_app(tmp_path)) as client:
        for module in ("recruitment.js", "comms.js", "settings.js"):
            assert client.get(f"/static/js/sections/{module}").status_code == 200, module


# --- deeper analytics endpoints (gated; enrich Raid/Loot/Recruitment) -------

def test_analytics_endpoints_gated_and_shaped(tmp_path):
    with TestClient(_admin_app(tmp_path)) as client:
        paths = ("/api/analytics/attendance", "/api/analytics/loot",
                 "/api/analytics/wishlist", "/api/analytics/funnel")
        for p in paths:
            assert client.get(p).status_code == 401, p
        client.post("/api/sync/unlock", json={"token": "s3cret"})
        att = client.get("/api/analytics/attendance").json()
        assert "characters" in att and "raids" in att and "turnout" in att
        loot = client.get("/api/analytics/loot").json()
        assert "drought" in loot and "breakdown" in loot and "avg_per_player" in loot
        wish = client.get("/api/analytics/wishlist").json()
        assert "contention" in wish and "coverage" in wish
        funnel = client.get("/api/analytics/funnel").json()
        assert "stages" in funnel and "close_rate" in funnel and "by_class" in funnel
