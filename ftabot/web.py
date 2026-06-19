"""Browser query layer: a FastAPI app over the same SQLite database.

Provides a read-only JSON API plus one server-rendered roster/search page so
the guild can browse the roster from a browser as well as Discord.

Run with:  python -m ftabot.web
"""

from __future__ import annotations

import asyncio
import hmac
import html
import json
from contextlib import asynccontextmanager
from urllib.parse import quote

from fastapi import (
    Cookie,
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    Request,
)
from fastapi.responses import HTMLResponse, JSONResponse

from . import content as content_lib
from . import runtime_settings, views
from .config import Config
from .db import Database
from .models import Member, now_iso
from .pages import MAX_POST_CHARS, PAGES, PageDef
from .sync import sync_guild


def _secret_matches(provided: str | None, expected: str) -> bool:
    """Constant-time secret comparison (avoids leaking length/prefix via timing)."""
    return hmac.compare_digest(provided or "", expected)


# Name of the http-only cookie that holds the sync token after an /admin unlock,
# so the browser flow stops carrying the secret in the URL (logs + history).
_SYNC_COOKIE = "fta_sync_token"


class SyncManager:
    """Runs roster syncs on demand, one at a time, with status tracking.

    Each run uses its own database connection (WAL lets the web app keep
    serving reads meanwhile), so a sync never blocks the site.
    """

    def __init__(self, config: Config):
        self.config = config
        self._lock = asyncio.Lock()
        self.running = False
        self.last_started: str | None = None
        self.last_finished: str | None = None
        self.last_summary: str | None = None
        self.last_error: str | None = None

    async def trigger(self) -> bool:
        """Start a sync in the background. Returns False if one is running."""
        async with self._lock:
            if self.running:
                return False
            self.running = True
            self.last_started = now_iso()
            self.last_error = None
        asyncio.create_task(self._run())
        return True

    async def _run(self) -> None:
        try:
            async with Database(self.config.database_path) as db:
                result = await sync_guild(db, self.config)
            self.last_summary = result.summary()
        except Exception as exc:  # noqa: BLE001 - report, don't crash the server
            self.last_error = str(exc)
            self.last_summary = None
        finally:
            self.running = False
            self.last_finished = now_iso()

    def status(self) -> dict:
        return {
            "running": self.running,
            "last_started": self.last_started,
            "last_finished": self.last_finished,
            "last_summary": self.last_summary,
            "last_error": self.last_error,
        }


class ScoutManager:
    """Runs the recruitment scout on demand, one at a time, with status tracking.

    Mirrors :class:`SyncManager`: the scout is network-bound (Warcraft Logs +
    Raider.IO) so it runs in a background task on its own DB connection, exactly
    as the ``/recruit lead capture`` command does. Disabled unless WoW guild has
    scout credentials (``config.scout_credentials``)."""

    def __init__(self, config: Config):
        self.config = config
        self._lock = asyncio.Lock()
        self.running = False
        self.last_started: str | None = None
        self.last_finished: str | None = None
        self.last_summary: str | None = None
        self.last_error: str | None = None

    async def trigger(self, only_class: str | None = None) -> bool:
        async with self._lock:
            if self.running:
                return False
            self.running = True
            self.last_started = now_iso()
            self.last_error = None
        asyncio.create_task(self._run(only_class))
        return True

    async def _run(self, only_class: str | None) -> None:
        from . import scout

        try:
            async with Database(self.config.database_path) as db:
                reports = await scout.run_scout(db, self.config, only_class=only_class)
                leads = scout.lead_dicts(reports)
                for lead in leads:
                    await db.upsert_lead(lead)
            self.last_summary = (
                f"Captured {len(leads)} lead(s)."
                if leads
                else "Scouted — no upgrades worth tracking."
            )
        except Exception as exc:  # noqa: BLE001 - report, don't crash the server
            self.last_error = str(exc)
            self.last_summary = None
        finally:
            self.running = False
            self.last_finished = now_iso()

    def status(self) -> dict:
        return {
            "running": self.running,
            "last_started": self.last_started,
            "last_finished": self.last_finished,
            "last_summary": self.last_summary,
            "last_error": self.last_error,
        }


def create_app(config: Config | None = None) -> FastAPI:
    config = config or Config.from_env()
    db = Database(config.database_path)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        await db.connect()
        try:
            yield
        finally:
            await db.close()

    app = FastAPI(title="From the Ashes — Guild Database", lifespan=lifespan)
    app.state.db = db
    app.state.config = config
    # Set by serve.py once the bot is ready: a coroutine (channel_id, embed) ->
    # None. When present, inbound webhooks post instantly; otherwise they queue
    # in feed_outbox for the bot's feed loop to drain.
    app.state.feed_sender = None
    # Set by serve.py to the running bot in the all-in-one deploy, so /healthz can
    # report Discord gateway liveness. Stays None when the web app runs standalone.
    app.state.bot = None
    sync_manager = SyncManager(config)
    app.state.sync = sync_manager
    scout_manager = ScoutManager(config)
    app.state.scout = scout_manager

    def _check_sync_token(provided: str | None) -> None:
        if not config.sync_token:
            raise HTTPException(
                status_code=503,
                detail="Sync trigger is disabled. Set SYNC_TOKEN to enable it.",
            )
        if not _secret_matches(provided, config.sync_token):
            raise HTTPException(status_code=401, detail="Invalid sync token.")

    # -- JSON API ------------------------------------------------------------

    @app.get("/healthz")
    async def healthz() -> dict[str, object]:
        # Liveness probe for the host (Render/Railway point healthCheckPath here):
        # it stays 200 whenever the web process can serve and reach the DB, so a
        # transient Discord gateway reconnect can't trip a restart storm. The bot's
        # gateway state rides in the body instead, so an external monitor (or a
        # future /status) can still catch the "process up, gateway dead" case — a
        # fully-dead bot task is already caught by serve.py's supervisor.
        payload: dict[str, object] = {"status": "ok", "members": await db.count()}
        bot = app.state.bot
        if bot is not None:
            latency = bot.latency  # seconds; nan before the first heartbeat
            ready = bot.is_ready() and not bot.is_closed()
            payload["bot"] = {
                "ready": ready,
                # nan != nan, so this maps "no heartbeat yet" to null cleanly.
                "latency_ms": round(latency * 1000) if latency == latency else None,
            }
            if not ready:
                payload["status"] = "degraded"
        return payload

    # -- integration hub: inbound webhooks -----------------------------------

    @app.post("/webhooks/raidhelper")
    async def raidhelper_webhook(
        request: Request,
        token: str | None = Query(None),
        x_signature: str | None = Header(None),
    ) -> JSONResponse:
        from .feeds.runner import drain_outbox
        from .router import resolve
        from .services.raidhelper import parse_webhook

        secret = config.raidhelper_webhook_secret
        if not secret:
            raise HTTPException(
                status_code=503,
                detail="Raid-Helper webhook disabled. Set RAIDHELPER_WEBHOOK_SECRET.",
            )
        if not _secret_matches(token or x_signature, secret):
            raise HTTPException(status_code=401, detail="Invalid webhook secret.")

        try:
            payload = await request.json()
        except (ValueError, json.JSONDecodeError):
            raise HTTPException(status_code=400, detail="Body must be JSON.")

        event = parse_webhook(payload)
        if event is None:
            raise HTTPException(status_code=400, detail="Unrecognised payload.")

        # Persist the event's current state for the Weekly Events board *before*
        # the alert dedup/route logic, so the board reflects every webhook even
        # when the alert is a duplicate, muted, or has nowhere to post.
        ex = event.extra
        from datetime import datetime, timezone

        now_epoch = int(datetime.now(timezone.utc).timestamp())
        await db.upsert_calendar_event(
            ex["event_id"],
            title=ex.get("title_plain"),
            start_epoch=ex.get("start_epoch"),
            url=ex.get("url"),
            leader=ex.get("leader"),
            signup_count=ex.get("signup_count"),
            channel_id=ex.get("channel_id"),
            status="cancelled" if ex.get("action") == "deleted" else "scheduled",
            raw=payload,
            prune_before_epoch=now_epoch - 14 * 86400,
        )

        if not await db.filter_unseen([event]):
            return JSONResponse({"status": "duplicate"})

        if event.source in await db.muted_sources():
            await db.mark_seen(event.source, event.external_id)
            return JSONResponse({"status": "muted"})

        decision = resolve(
            event, await db.feed_rules(), default_channel_id=config.raidhelper_channel
        )
        await db.mark_seen(event.source, event.external_id)
        if decision is None:
            return JSONResponse({"status": "no_route"})

        await db.enqueue_outbox(
            decision.channel_id, event.to_json(), json.dumps(decision.style)
        )
        sender = app.state.feed_sender
        if sender is not None:
            posted = await drain_outbox(db, config, sender)
            # The guardrail may have held the post back (off / unconfirmed); it
            # stays queued in that case rather than being silently dropped.
            return JSONResponse({"status": "posted" if posted else "queued"})
        return JSONResponse({"status": "queued"})

    # -- data spine: desktop-addon SavedVariables upload ---------------------

    @app.post("/api/upload/savedvars")
    async def upload_savedvars(
        request: Request,
        token: str | None = Query(None),
        x_upload_token: str | None = Header(None),
    ) -> JSONResponse:
        """Ingest a desktop-addon SavedVariables dump (the data-spine intake).

        The GM's companion uploader POSTs the raw ``.lua`` text here. We
        auto-detect which addon(s) it carries by their global table names and
        route each into the spine (RCLootCouncil loot -> ``loot_history``).
        Secret-protected, mirroring the sync trigger and the webhook.
        """
        from . import spine
        from .services import savedvars

        secret = config.savedvars_upload_secret
        if not secret:
            raise HTTPException(
                status_code=503,
                detail="Upload disabled. Set SAVEDVARS_UPLOAD_SECRET to enable it.",
            )
        if not _secret_matches(token or x_upload_token, secret):
            raise HTTPException(status_code=401, detail="Invalid upload token.")

        raw = (await request.body()).decode("utf-8", "replace")
        if not raw.strip():
            raise HTTPException(status_code=400, detail="Empty upload.")
        try:
            data = savedvars.parse_savedvariables(raw)
        except savedvars.LuaParseError as exc:
            raise HTTPException(status_code=400, detail=f"Could not parse SavedVariables: {exc}")

        summary = await spine.ingest_savedvars(
            db, data, default_region=config.guild_region, default_realm=config.guild_realm
        )
        if not summary["detected"]:
            raise HTTPException(
                status_code=422,
                detail="No recognised addon data (expected RCLootCouncilLootDB / GRM).",
            )
        return JSONResponse(summary)

    @app.get("/api/members")
    async def api_members(
        sort: str = Query("mythic_plus_score"),
        wow_class: str | None = Query(None, alias="class"),
        role: str | None = None,
        q: str | None = None,
    ) -> JSONResponse:
        members = await _query_members(db, sort=sort, wow_class=wow_class, role=role, q=q)
        return JSONResponse([m.to_dict() for m in members])

    @app.get("/api/members/{name}")
    async def api_member(name: str, realm: str | None = None) -> JSONResponse:
        realm = realm or config.guild_realm
        member = await db.get_member(name, realm, config.guild_region)
        if member is None:
            raise HTTPException(status_code=404, detail="member not found")
        return JSONResponse(member.to_dict(include_details=True))

    @app.get("/api/identity/{name}")
    async def api_identity(name: str, realm: str | None = None) -> JSONResponse:
        """The GRM identity record (alts, join date, rank, note) for a character."""
        record = await db.get_identity(
            name, realm or config.guild_realm, config.guild_region
        )
        if record is None:
            raise HTTPException(status_code=404, detail="no identity record")
        return JSONResponse(record)

    @app.get("/api/stats")
    async def api_stats() -> JSONResponse:
        return JSONResponse(await _stats(db))

    @app.get("/api/gm")
    async def api_gm() -> JSONResponse:
        return JSONResponse(await views.gm_report(db, config))

    @app.get("/api/raid")
    async def api_raid(team: str | None = None) -> JSONResponse:
        return JSONResponse(await views.raid_board(db, config, team=team))

    @app.get("/api/loot")
    async def api_loot() -> JSONResponse:
        return JSONResponse(await views.loot_overview(db, config))

    @app.get("/api/loot/priority")
    async def api_loot_priority(item: str) -> JSONResponse:
        return JSONResponse(await views.loot_priority(db, config, item_name=item))

    @app.get("/api/trends")
    async def api_trends(limit: int = 30) -> JSONResponse:
        return JSONResponse(await views.trends(db, config, limit=limit))

    # -- manual sync trigger -------------------------------------------------

    @app.post("/api/sync")
    async def api_sync(
        token: str | None = None,
        x_sync_token: str | None = Header(default=None),
        fta_sync_token: str | None = Cookie(default=None),
    ) -> JSONResponse:
        # Accept the token from the header, the (legacy) query param, or the
        # http-only cookie set at unlock, so the admin page can sync without
        # putting the secret in the URL.
        _check_sync_token(token or x_sync_token or fta_sync_token)
        started = await sync_manager.trigger()
        if not started:
            return JSONResponse(
                {"status": "already_running", **sync_manager.status()},
                status_code=409,
            )
        return JSONResponse(
            {"status": "started", **sync_manager.status()}, status_code=202
        )

    @app.post("/api/sync/unlock")
    async def api_sync_unlock(request: Request) -> JSONResponse:
        """Validate the sync token from a JSON body and stash it in an http-only
        cookie, so the browser unlock flow never carries it in the URL."""
        try:
            body = await request.json()
        except (ValueError, json.JSONDecodeError):
            body = {}
        _check_sync_token(body.get("token") if isinstance(body, dict) else None)
        resp = JSONResponse({"status": "ok"})
        resp.set_cookie(
            _SYNC_COOKIE, config.sync_token or "", httponly=True,
            secure=config.cookie_secure,
            samesite="strict", max_age=60 * 60 * 24 * 30, path="/",
        )
        return resp

    @app.get("/api/sync/status")
    async def api_sync_status() -> JSONResponse:
        return JSONResponse(sync_manager.status())

    # -- dashboard content (sticky-page copy) ---------------------------------
    # The GM edits the sticky pages' Discord markdown here; a save stores an
    # override in content_docs (the bundled content/*.json stays the default).
    # Gated by the same sync token / cookie as the manual sync trigger.

    def _page_or_404(page_name: str) -> PageDef:
        page = PAGES.get(page_name)
        if page is None:
            raise HTTPException(status_code=404, detail="Unknown page.")
        return page

    async def _content_payload(page: PageDef) -> dict:
        doc = await db.get_content_doc(page.content_doc)
        source = "custom" if doc is not None else "bundled"
        if doc is None:
            doc = content_lib.load(page.content_doc)
        posts = [p for p in doc.get("posts") or [] if isinstance(p, str)]
        return {
            "page": page.name,
            "title": page.title,
            "source": source,
            "updated_at": await db.content_doc_updated_at(page.content_doc),
            "posts": posts,
        }

    @app.get("/api/content")
    async def api_content_list(
        token: str | None = None,
        x_sync_token: str | None = Header(default=None),
        fta_sync_token: str | None = Cookie(default=None),
    ) -> JSONResponse:
        _check_sync_token(token or x_sync_token or fta_sync_token)
        return JSONResponse([await _content_payload(p) for p in PAGES.values()])

    @app.get("/api/content/{page_name}")
    async def api_content_get(
        page_name: str,
        token: str | None = None,
        x_sync_token: str | None = Header(default=None),
        fta_sync_token: str | None = Cookie(default=None),
    ) -> JSONResponse:
        _check_sync_token(token or x_sync_token or fta_sync_token)
        return JSONResponse(await _content_payload(_page_or_404(page_name)))

    @app.put("/api/content/{page_name}")
    async def api_content_put(
        page_name: str,
        request: Request,
        token: str | None = None,
        x_sync_token: str | None = Header(default=None),
        fta_sync_token: str | None = Cookie(default=None),
    ) -> JSONResponse:
        _check_sync_token(token or x_sync_token or fta_sync_token)
        page = _page_or_404(page_name)
        try:
            body = await request.json()
        except (ValueError, json.JSONDecodeError):
            raise HTTPException(status_code=400, detail="Body must be JSON.")
        posts = body.get("posts") if isinstance(body, dict) else None
        if not isinstance(posts, list) or not posts:
            raise HTTPException(
                status_code=400, detail="Provide a non-empty 'posts' list."
            )
        if len(posts) > 10:
            raise HTTPException(status_code=400, detail="At most 10 messages per page.")
        for i, post in enumerate(posts, start=1):
            if not isinstance(post, str) or not post.strip():
                raise HTTPException(
                    status_code=400, detail=f"Message {i} is empty or not text."
                )
            if len(post) > MAX_POST_CHARS:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Message {i} is {len(post)} characters; Discord allows "
                        f"{MAX_POST_CHARS} per message. Split it into two messages."
                    ),
                )
        await db.set_content_doc(page.content_doc, {"title": page.title, "posts": posts})
        return JSONResponse({"status": "saved", "posts": len(posts)})

    @app.delete("/api/content/{page_name}")
    async def api_content_delete(
        page_name: str,
        token: str | None = None,
        x_sync_token: str | None = Header(default=None),
        fta_sync_token: str | None = Cookie(default=None),
    ) -> JSONResponse:
        _check_sync_token(token or x_sync_token or fta_sync_token)
        page = _page_or_404(page_name)
        removed = await db.delete_content_doc(page.content_doc)
        return JSONResponse({"status": "reverted" if removed else "already_bundled"})

    # -- admin control surfaces (all token-gated) ----------------------------
    # Every endpoint below mutates DB state that the serve.py board/feed loops
    # re-read each tick, so a change goes live within one refresh interval. An
    # on-demand refresh (when the bot shares this process) renders immediately.

    def _require_token(
        token: str | None = None,
        x_sync_token: str | None = Header(default=None),
        fta_sync_token: str | None = Cookie(default=None),
    ) -> None:
        _check_sync_token(token or x_sync_token or fta_sync_token)

    gated = [Depends(_require_token)]

    async def _body(request: Request) -> dict:
        try:
            data = await request.json()
        except (ValueError, json.JSONDecodeError):
            raise HTTPException(status_code=400, detail="Body must be JSON.")
        if not isinstance(data, dict):
            raise HTTPException(status_code=400, detail="Body must be a JSON object.")
        return data

    # -- overview (360 command center) ---------------------------------------

    @app.get("/api/overview", dependencies=gated)
    async def api_overview() -> JSONResponse:
        data = await views.overview(db, config)
        data["sync"] = sync_manager.status()
        return JSONResponse(data)

    # -- boards --------------------------------------------------------------

    async def _refresh_board_now(board_id: int) -> str:
        """Render one board now via the in-process bot; 'queued' when standalone."""
        from .boards import REGISTRY, BotBoardMessenger, refresh_board

        row = await db.get_board(board_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Unknown board.")
        bot = app.state.bot
        board = REGISTRY.get(row["board_type"])
        if bot is None or board is None:
            return "queued"
        gate = await runtime_settings.effective_post_gate(db, config)
        return await refresh_board(
            db, config, board, row, BotBoardMessenger(bot), gate
        )

    @app.get("/api/boards", dependencies=gated)
    async def api_boards() -> JSONResponse:
        return JSONResponse(await views.boards_status(db, config))

    @app.post("/api/boards", dependencies=gated)
    async def api_boards_create(request: Request) -> JSONResponse:
        from .boards import REGISTRY

        data = await _body(request)
        board_type = str(data.get("board_type") or "").strip()
        channel_id = str(data.get("channel_id") or "").strip()
        if board_type not in REGISTRY:
            raise HTTPException(status_code=400, detail="Unknown board type.")
        if not channel_id.isdigit():
            raise HTTPException(status_code=400, detail="channel_id must be numeric.")
        try:
            board_id = await db.add_board(board_type, channel_id)
        except Exception:  # noqa: BLE001 - almost certainly the UNIQUE constraint
            raise HTTPException(
                status_code=409,
                detail="That board type is already registered for this channel.",
            )
        return JSONResponse({"status": "created", "id": board_id}, status_code=201)

    @app.post("/api/boards/{board_id}/{action}", dependencies=gated)
    async def api_boards_action(board_id: int, action: str) -> JSONResponse:
        if action in {"promote", "demote"}:
            ok = await db.set_board_promoted(board_id, action == "promote")
        elif action in {"enable", "disable"}:
            ok = await db.set_board_enabled(board_id, action == "enable")
        elif action == "refresh":
            result = await _refresh_board_now(board_id)
            return JSONResponse({"status": result})
        else:
            raise HTTPException(status_code=404, detail="Unknown board action.")
        if not ok:
            raise HTTPException(status_code=404, detail="Unknown board.")
        return JSONResponse({"status": action + "d"})

    @app.put("/api/boards/{board_id}/config", dependencies=gated)
    async def api_boards_config(board_id: int, request: Request) -> JSONResponse:
        data = await _body(request)
        cfg = data.get("config", data)
        if not isinstance(cfg, dict):
            raise HTTPException(status_code=400, detail="config must be an object.")
        if not await db.set_board_config(board_id, cfg):
            raise HTTPException(status_code=404, detail="Unknown board.")
        return JSONResponse({"status": "saved"})

    @app.delete("/api/boards/{board_id}", dependencies=gated)
    async def api_boards_delete(board_id: int) -> JSONResponse:
        if not await db.delete_board(board_id):
            raise HTTPException(status_code=404, detail="Unknown board.")
        return JSONResponse({"status": "removed"})

    # -- sticky-page placement -----------------------------------------------

    async def _refresh_page_now(page: PageDef, row: dict) -> str:
        from .boards import BotBoardMessenger
        from .pages import refresh_page

        bot = app.state.bot
        if bot is None:
            return "queued"
        gate = await runtime_settings.effective_post_gate(db, config)
        return await refresh_page(
            db, config, page, row, BotBoardMessenger(bot), gate
        )

    @app.get("/api/pages", dependencies=gated)
    async def api_pages() -> JSONResponse:
        return JSONResponse(await views.pages_status(db, config))

    @app.post("/api/pages/{page_name}/place", dependencies=gated)
    async def api_pages_place(page_name: str, request: Request) -> JSONResponse:
        page = _page_or_404(page_name)
        data = await _body(request)
        channel_id = str(data.get("channel_id") or "").strip()
        if not channel_id.isdigit():
            raise HTTPException(status_code=400, detail="channel_id must be numeric.")
        await db.upsert_sticky_page(page.name, channel_id)
        row = await db.get_sticky_page(page.name)
        action = await _refresh_page_now(page, row)
        return JSONResponse({"status": "placed", "refresh": action})

    @app.post("/api/pages/{page_name}/refresh", dependencies=gated)
    async def api_pages_refresh(page_name: str) -> JSONResponse:
        page = _page_or_404(page_name)
        row = await db.get_sticky_page(page.name)
        if row is None:
            raise HTTPException(status_code=404, detail="Page is not placed yet.")
        return JSONResponse({"status": await _refresh_page_now(page, row)})

    @app.delete("/api/pages/{page_name}", dependencies=gated)
    async def api_pages_remove(page_name: str) -> JSONResponse:
        page = _page_or_404(page_name)
        row = await db.get_sticky_page(page.name)
        if row is None:
            return JSONResponse({"status": "not_placed"})
        bot = app.state.bot
        if bot is not None:
            from .boards import BotBoardMessenger
            from .pages import remove_page

            await remove_page(db, row, BotBoardMessenger(bot))
        else:
            await db.delete_sticky_page(page.name)
        return JSONResponse({"status": "removed"})

    # -- feed hub ------------------------------------------------------------

    @app.get("/api/feeds", dependencies=gated)
    async def api_feeds() -> JSONResponse:
        return JSONResponse(await views.feed_health(db, config))

    @app.post("/api/feeds/rules", dependencies=gated)
    async def api_feeds_rule_create(request: Request) -> JSONResponse:
        data = await _body(request)
        source = str(data.get("source") or "").strip()
        channel_id = str(data.get("channel_id") or "").strip()
        if not source:
            raise HTTPException(status_code=400, detail="source is required.")
        if not channel_id.isdigit():
            raise HTTPException(status_code=400, detail="channel_id must be numeric.")
        event_type = (str(data.get("event_type")).strip() or None
                      if data.get("event_type") else None)
        contains = [c.strip() for c in str(data.get("contains") or "").split(",")
                    if c.strip()]
        match = {"contains": contains} if contains else None
        style: dict = {}
        if data.get("color"):
            style["color"] = str(data["color"]).strip()
        if data.get("ping"):
            style["ping"] = str(data["ping"]).strip()
        rule_id = await db.add_feed_rule(
            source, channel_id, event_type=event_type, match=match,
            style=style or None, updated_by="dashboard",
        )
        return JSONResponse({"status": "created", "id": rule_id}, status_code=201)

    @app.post("/api/feeds/rules/{rule_id}/{action}", dependencies=gated)
    async def api_feeds_rule_toggle(rule_id: int, action: str) -> JSONResponse:
        if action not in {"enable", "disable"}:
            raise HTTPException(status_code=404, detail="Unknown rule action.")
        if not await db.set_feed_rule_enabled(rule_id, action == "enable"):
            raise HTTPException(status_code=404, detail="Unknown rule.")
        return JSONResponse({"status": action + "d"})

    @app.delete("/api/feeds/rules/{rule_id}", dependencies=gated)
    async def api_feeds_rule_delete(rule_id: int) -> JSONResponse:
        if not await db.delete_feed_rule(rule_id):
            raise HTTPException(status_code=404, detail="Unknown rule.")
        return JSONResponse({"status": "removed"})

    @app.post("/api/feeds/mute", dependencies=gated)
    async def api_feeds_mute(request: Request) -> JSONResponse:
        data = await _body(request)
        source = str(data.get("source") or "").strip()
        if not source:
            raise HTTPException(status_code=400, detail="source is required.")
        await db.mute_source(source)
        return JSONResponse({"status": "muted"})

    @app.delete("/api/feeds/mute/{source}", dependencies=gated)
    async def api_feeds_unmute(source: str) -> JSONResponse:
        await db.unmute_source(source)
        return JSONResponse({"status": "unmuted"})

    # -- runtime settings (the live config overlay) --------------------------

    @app.get("/api/settings", dependencies=gated)
    async def api_settings() -> JSONResponse:
        return JSONResponse(await runtime_settings.settings_overview(db, config))

    @app.put("/api/settings", dependencies=gated)
    async def api_settings_put(request: Request) -> JSONResponse:
        data = await _body(request)
        updates = data.get("settings", data)
        if not isinstance(updates, dict) or not updates:
            raise HTTPException(status_code=400, detail="Provide settings to save.")
        try:
            for key, raw in updates.items():
                await db.set_setting(key, runtime_settings.normalise(key, raw))
        except runtime_settings.SettingError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return JSONResponse({"status": "saved"})

    @app.delete("/api/settings/{key}", dependencies=gated)
    async def api_settings_delete(key: str) -> JSONResponse:
        reverted = await db.delete_setting(key)
        return JSONResponse({"status": "reverted" if reverted else "already_default"})

    # -- recruiting: lead funnel + board config ------------------------------

    async def _edit_recruit_state(mutate) -> dict:
        """Read-modify-write the recruitment state (mirrors the /recruit cog)."""
        state = await db.recruitment_state()
        mutate(state)
        await db.set_recruitment_state(state)
        return state

    @app.get("/api/recruiting", dependencies=gated)
    async def api_recruiting() -> JSONResponse:
        data = await views.recruiting(db, config)
        data["capture"] = scout_manager.status()
        return JSONResponse(data)

    @app.post("/api/leads/capture", dependencies=gated)
    async def api_leads_capture(request: Request) -> JSONResponse:
        if not config.scout_credentials:
            raise HTTPException(
                status_code=503,
                detail="Scout is disabled. Set Warcraft Logs credentials + zone ids.",
            )
        try:
            body = await request.json()
        except (ValueError, json.JSONDecodeError):
            body = {}
        only_class = (body.get("wow_class") if isinstance(body, dict) else None) or None
        if not await scout_manager.trigger(only_class):
            return JSONResponse(
                {"status": "already_running", **scout_manager.status()}, status_code=409
            )
        return JSONResponse(
            {"status": "started", **scout_manager.status()}, status_code=202
        )

    @app.post("/api/leads/{lead_id}/state", dependencies=gated)
    async def api_lead_state(lead_id: int, request: Request) -> JSONResponse:
        from .recruit import LEAD_STATES

        data = await _body(request)
        state = str(data.get("state") or "").strip()
        if state not in LEAD_STATES:
            raise HTTPException(status_code=400, detail="Unknown lead state.")
        if not await db.set_lead_state(lead_id, state):
            raise HTTPException(status_code=404, detail="Unknown lead.")
        return JSONResponse({"status": "updated"})

    @app.put("/api/leads/{lead_id}/note", dependencies=gated)
    async def api_lead_note(lead_id: int, request: Request) -> JSONResponse:
        data = await _body(request)
        note = str(data.get("note") or "").strip() or None
        if not await db.set_lead_note(lead_id, note):
            raise HTTPException(status_code=404, detail="Unknown lead.")
        return JSONResponse({"status": "saved"})

    @app.delete("/api/leads/{lead_id}", dependencies=gated)
    async def api_lead_delete(lead_id: int) -> JSONResponse:
        if not await db.delete_lead(lead_id):
            raise HTTPException(status_code=404, detail="Unknown lead.")
        return JSONResponse({"status": "removed"})

    @app.put("/api/recruiting/slots/{role}", dependencies=gated)
    async def api_recruit_slots(role: str, request: Request) -> JSONResponse:
        from .boards.recruitment import ROLE_ORDER

        if role not in ROLE_ORDER:
            raise HTTPException(status_code=404, detail="Unknown role.")
        data = await _body(request)
        slot: dict = {}
        try:
            slot["open"] = max(0, int(data.get("open") or 0))
            if data.get("ilvl") not in (None, ""):
                slot["ilvl"] = int(data["ilvl"])
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="open / ilvl must be numbers.")
        note = str(data.get("note") or "").strip()
        if note:
            slot["note"] = note
        await _edit_recruit_state(lambda s: s.setdefault("slots", {}).update({role: slot}))
        return JSONResponse({"status": "saved"})

    @app.post("/api/recruiting/rules", dependencies=gated)
    async def api_recruit_rule_add(request: Request) -> JSONResponse:
        data = await _body(request)
        text = str(data.get("text") or "").strip()
        if not text:
            raise HTTPException(status_code=400, detail="Rule text is required.")
        await _edit_recruit_state(lambda s: s.setdefault("rules", []).append(text))
        return JSONResponse({"status": "added"})

    @app.delete("/api/recruiting/rules/{index}", dependencies=gated)
    async def api_recruit_rule_del(index: int) -> JSONResponse:
        state = await db.recruitment_state()
        rules = state.get("rules") or []
        if not 0 <= index < len(rules):
            raise HTTPException(status_code=404, detail="No such rule.")
        rules.pop(index)
        state["rules"] = rules
        await db.set_recruitment_state(state)
        return JSONResponse({"status": "removed"})

    @app.put("/api/recruiting/message", dependencies=gated)
    async def api_recruit_message(request: Request) -> JSONResponse:
        data = await _body(request)
        msg = str(data.get("text") or "").strip() or None

        def _mut(s: dict) -> None:
            if msg:
                s["message"] = msg
            else:
                s.pop("message", None)

        await _edit_recruit_state(_mut)
        return JSONResponse({"status": "saved"})

    @app.post("/api/recruiting/reset", dependencies=gated)
    async def api_recruit_reset() -> JSONResponse:
        await db.set_recruitment_state({})
        return JSONResponse({"status": "reset"})

    # -- member admin (the six manual-owned columns) -------------------------

    @app.get("/api/admin/members", dependencies=gated)
    async def api_admin_members(q: str | None = None) -> JSONResponse:
        return JSONResponse(await views.members_admin(db, config, q=q))

    @app.put("/api/admin/members/{member_id}", dependencies=gated)
    async def api_admin_member_edit(member_id: int, request: Request) -> JSONResponse:
        member = await db.get_member_by_id(member_id)
        if member is None:
            raise HTTPException(status_code=404, detail="Unknown member.")
        data = await _body(request)

        def _clean(value: object) -> str | None:
            return (str(value).strip() or None) if value is not None else None

        if "discord_id" in data:
            await db.set_discord_link(member_id, _clean(data["discord_id"]))
        if "notes" in data:
            await db.set_note(member_id, _clean(data["notes"]))
        if "raid_team" in data:
            await db.set_raid_team(member_id, _clean(data["raid_team"]))
        if "officer_role" in data:
            await db.set_officer_role(member_id, _clean(data["officer_role"]))
        if "main_character" in data:
            main_name = _clean(data["main_character"])
            if main_name is None:  # clear the alt link → this character is a main
                await db.set_main_alt(member_id, is_main=True, main_character_id=None)
            else:
                realm = _clean(data.get("realm")) or member.realm_slug
                main = await db.get_member(main_name, realm, member.region)
                if main is None:
                    raise HTTPException(
                        status_code=400,
                        detail=f"No member '{main_name}' on {realm}.",
                    )
                if main.id == member_id:
                    raise HTTPException(
                        status_code=400, detail="A character can't be its own main."
                    )
                await db.set_main_alt(
                    member_id, is_main=False, main_character_id=main.id
                )
        return JSONResponse({"status": "saved"})

    # -- HTML pages ----------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    async def index(
        sort: str = Query("mythic_plus_score"),
        wow_class: str | None = Query(None, alias="class"),
        role: str | None = None,
        q: str | None = None,
    ) -> HTMLResponse:
        members = await _query_members(db, sort=sort, wow_class=wow_class, role=role, q=q)
        return HTMLResponse(
            _render_roster(config.guild_name, members, sort=sort, role=role, q=q)
        )

    @app.get("/gm", response_class=HTMLResponse)
    async def gm_page() -> HTMLResponse:
        return HTMLResponse(_render_gm(config.guild_name, await views.gm_report(db, config)))

    @app.get("/raid", response_class=HTMLResponse)
    async def raid_page(team: str | None = None) -> HTMLResponse:
        report = await views.raid_board(db, config, team=team)
        return HTMLResponse(_render_raid(config.guild_name, report))

    @app.get("/loot", response_class=HTMLResponse)
    async def loot_page() -> HTMLResponse:
        return HTMLResponse(_render_loot(config.guild_name, await views.loot_overview(db, config)))

    @app.get("/member/{name}", response_class=HTMLResponse)
    async def member_page(name: str, realm: str | None = None) -> HTMLResponse:
        data = await views.member_detail(db, config, name, realm=realm)
        if data is None:
            return HTMLResponse(
                _layout(
                    config.guild_name,
                    "Not found",
                    f"<h2>{html.escape(name)} not found</h2>"
                    "<p><a href='/'>← back to roster</a></p>",
                    "roster",
                ),
                status_code=404,
            )
        return HTMLResponse(_render_member(config.guild_name, data))

    @app.get("/content", response_class=HTMLResponse)
    async def content_page(
        token: str | None = None,
        fta_sync_token: str | None = Cookie(default=None),
    ) -> HTMLResponse:
        supplied = token if token is not None else fta_sync_token
        authed = bool(config.sync_token) and _secret_matches(supplied, config.sync_token)
        if not authed:
            return HTMLResponse(
                _render_content_locked(config, error=bool(token) and not authed)
            )
        items = []
        for page in PAGES.values():
            item = await _content_payload(page)
            item["placement"] = await db.get_sticky_page(page.name)
            items.append(item)
        return HTMLResponse(_render_content(config, items))

    @app.get("/admin", response_class=HTMLResponse)
    async def admin_page(
        token: str | None = None,
        fta_sync_token: str | None = Cookie(default=None),
    ) -> HTMLResponse:
        # Authenticate via the cookie (preferred) or a legacy ?token= query.
        supplied = token if token is not None else fta_sync_token
        authed = bool(config.sync_token) and _secret_matches(supplied, config.sync_token)
        resp = HTMLResponse(
            _render_admin(
                config, sync_manager, authed=authed, error=bool(token) and not authed
            )
        )
        if authed:
            # A valid ?token= unlock also seeds the cookie, so the token leaves
            # the URL on every subsequent request and the sync call.
            resp.set_cookie(
                _SYNC_COOKIE, config.sync_token or "", httponly=True,
                secure=config.cookie_secure,
                samesite="strict", max_age=60 * 60 * 24 * 30, path="/",
            )
        return resp

    # -- gated admin HTML pages (client-rendered from the JSON APIs) ----------

    def _authed(token: str | None, cookie: str | None) -> bool:
        supplied = token if token is not None else cookie
        return bool(config.sync_token) and _secret_matches(supplied, config.sync_token)

    def _gated_html(token, cookie, active, title, render) -> HTMLResponse:
        if not _authed(token, cookie):
            return HTMLResponse(
                _admin_locked(config, active=active, title=title, error=bool(token))
            )
        resp = HTMLResponse(render())
        resp.set_cookie(
            _SYNC_COOKIE, config.sync_token or "", httponly=True,
            secure=config.cookie_secure,
            samesite="strict", max_age=60 * 60 * 24 * 30, path="/",
        )
        return resp

    @app.get("/overview", response_class=HTMLResponse)
    async def overview_page(
        token: str | None = None,
        fta_sync_token: str | None = Cookie(default=None),
    ) -> HTMLResponse:
        return _gated_html(
            token, fta_sync_token, "overview", "Overview",
            lambda: _render_overview(config),
        )

    @app.get("/boards", response_class=HTMLResponse)
    async def boards_page(
        token: str | None = None,
        fta_sync_token: str | None = Cookie(default=None),
    ) -> HTMLResponse:
        return _gated_html(
            token, fta_sync_token, "boards", "Boards",
            lambda: _render_boards(config),
        )

    @app.get("/feeds", response_class=HTMLResponse)
    async def feeds_page(
        token: str | None = None,
        fta_sync_token: str | None = Cookie(default=None),
    ) -> HTMLResponse:
        return _gated_html(
            token, fta_sync_token, "feeds", "Feeds",
            lambda: _render_feeds(config),
        )

    @app.get("/settings", response_class=HTMLResponse)
    async def settings_page(
        token: str | None = None,
        fta_sync_token: str | None = Cookie(default=None),
    ) -> HTMLResponse:
        return _gated_html(
            token, fta_sync_token, "settings", "Settings",
            lambda: _render_settings(config),
        )

    @app.get("/recruiting", response_class=HTMLResponse)
    async def recruiting_page(
        token: str | None = None,
        fta_sync_token: str | None = Cookie(default=None),
    ) -> HTMLResponse:
        return _gated_html(
            token, fta_sync_token, "recruiting", "Recruiting",
            lambda: _render_recruiting(config),
        )

    @app.get("/members", response_class=HTMLResponse)
    async def members_page(
        token: str | None = None,
        fta_sync_token: str | None = Cookie(default=None),
    ) -> HTMLResponse:
        return _gated_html(
            token, fta_sync_token, "members", "Members",
            lambda: _render_members(config),
        )

    return app


# --- query helpers ----------------------------------------------------------


async def _query_members(
    db: Database,
    *,
    sort: str,
    wow_class: str | None,
    role: str | None,
    q: str | None,
) -> list[Member]:
    if wow_class or role or q:
        members = await db.search_members(name=q, class_name=wow_class, role=role)
        members = _sort_members(members, sort)
    else:
        members = await db.all_members(order_by=sort)
    return members


_SORT_KEYS = {
    "mythic_plus_score": (lambda m: m.mythic_plus_score, True),
    "item_level": (lambda m: m.item_level, True),
    "average_item_level": (lambda m: m.average_item_level, True),
    "best_parse_avg": (lambda m: m.best_parse_avg, True),
    "guild_rank": (lambda m: m.guild_rank, False),
    "name": (lambda m: m.character_name.lower(), False),
}


def _sort_members(members: list[Member], sort: str) -> list[Member]:
    key_fn, descending = _SORT_KEYS.get(sort, _SORT_KEYS["mythic_plus_score"])

    def sort_key(m: Member):
        value = key_fn(m)
        if value is None:  # missing values always sort last
            return (1, 0)
        # Descending keys are numeric, so negate; ascending keys sort naturally.
        return (0, -value) if descending else (0, value)

    return sorted(members, key=sort_key)


async def _stats(db: Database) -> dict[str, object]:
    members = await db.all_members()
    by_role: dict[str, int] = {}
    by_class: dict[str, int] = {}
    scores = [m.mythic_plus_score for m in members if m.mythic_plus_score]
    for m in members:
        if m.role:
            by_role[m.role] = by_role.get(m.role, 0) + 1
        if m.class_name:
            by_class[m.class_name] = by_class.get(m.class_name, 0) + 1
    return {
        "total": len(members),
        "by_role": by_role,
        "by_class": by_class,
        "avg_mythic_plus_score": (sum(scores) / len(scores)) if scores else None,
        "max_mythic_plus_score": max(scores) if scores else None,
    }


# --- HTML rendering (no template engine, just escaped f-strings) -----------

_CSS = """
  :root { color-scheme: dark; }
  body { font-family: system-ui, sans-serif; margin: 0; background:#16110f; color:#eee; }
  header { padding: 1.1rem 1.5rem .8rem; background:#2a1812; border-bottom:2px solid #cc4422; }
  header h1 { margin:0; font-size:1.3rem; color:#ff7043; }
  nav { margin-top:.5rem; display:flex; gap:1rem; font-size:.9rem; }
  nav a { color:#ffab91; text-decoration:none; padding:.15rem 0; }
  nav a.active { color:#fff; border-bottom:2px solid #cc4422; }
  .wrap { padding: 1rem 1.5rem; }
  .cards { display:flex; flex-wrap:wrap; gap:1rem; margin-bottom:1.2rem; }
  .card { background:#221712; border:1px solid #3a2820; border-radius:10px; padding:.8rem 1rem; min-width:140px; }
  .card .big { font-size:1.8rem; font-weight:700; color:#ff7043; }
  .card .lbl { font-size:.75rem; color:#bbb; text-transform:uppercase; letter-spacing:.04em; }
  h2 { font-size:1rem; color:#ffab91; margin:1.4rem 0 .5rem; }
  form { margin-bottom: 1rem; }
  input[type=search] { padding:.4rem .6rem; border-radius:6px; border:1px solid #543; background:#221; color:#eee; }
  button { padding:.4rem .8rem; border-radius:6px; border:0; background:#cc4422; color:#fff; cursor:pointer; }
  table { border-collapse: collapse; width:100%; font-size:.9rem; }
  th, td { text-align:left; padding:.45rem .6rem; border-bottom:1px solid #332620; }
  th { position:sticky; top:0; background:#241712; }
  th a { color:#ffab91; text-decoration:none; }
  td.num, th.num { text-align:right; font-variant-numeric: tabular-nums; }
  tr:hover td { background:#1f1713; }
  a { color:#ffab91; }
  .bar { background:#3a2820; border-radius:5px; height:9px; overflow:hidden; min-width:80px; }
  .bar > span { display:block; height:100%; background:#cc4422; }
  .pill { display:inline-block; background:#3a2820; border-radius:10px; padding:.05rem .5rem; margin:.1rem; font-size:.8rem; }
  .ok { color:#9ccc65; } .bad { color:#ef5350; } .warn { color:#ffca28; }
  .flags { font-size:.8rem; color:#ddd; }
  textarea { width:100%; min-height:180px; box-sizing:border-box; padding:.6rem .7rem;
    border-radius:8px; border:1px solid #543; background:#1c1310; color:#eee;
    font-family: ui-monospace, monospace; font-size:.85rem; line-height:1.4; resize:vertical; }
  .doc { background:#221712; border:1px solid #3a2820; border-radius:10px;
    padding:1rem 1.2rem; margin-bottom:1.4rem; }
  .doc h2 { margin-top:0; }
  .doc label { display:flex; justify-content:space-between; font-size:.8rem;
    color:#bbb; margin:.8rem 0 .25rem; }
  .count.over { color:#ef5350; font-weight:700; }
  button.ghost { background:#3a2820; }
  .status { margin-left:.6rem; font-size:.85rem; }
  .navsep { color:#5a4034; }
  button.sm { padding:.2rem .5rem; font-size:.8rem; margin:0 .1rem; }
  button.danger { background:#7a2018; }
  .tag { display:inline-block; border-radius:6px; padding:.05rem .45rem; font-size:.75rem;
    text-transform:uppercase; letter-spacing:.03em; }
  .tag.live { background:#1e4620; color:#9ccc65; }
  .tag.sandboxed { background:#4a3a12; color:#ffca28; }
  .tag.disabled { background:#3a2820; color:#999; }
  .tag.on { background:#1e4620; color:#9ccc65; }
  .tag.off { background:#4a1818; color:#ef9a9a; }
  .panel { background:#221712; border:1px solid #3a2820; border-radius:10px;
    padding:1rem 1.2rem; margin-bottom:1.4rem; }
  .panel h2 { margin-top:0; }
  .field { display:flex; flex-direction:column; gap:.2rem; margin:.5rem .8rem .5rem 0; }
  .field label { font-size:.75rem; color:#bbb; text-transform:uppercase; letter-spacing:.03em; }
  .field input, .field select { padding:.4rem .6rem; border-radius:6px; border:1px solid #543;
    background:#1c1310; color:#eee; min-width:160px; }
  .row { display:flex; flex-wrap:wrap; align-items:flex-end; gap:.3rem; }
  .muted { color:#bbb; font-size:.85rem; }
  code.chan::before { content:'#'; color:#888; }
"""


def _fmt(value: object) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.0f}" if value.is_integer() else f"{value:.1f}"
    return html.escape(str(value))


def _safe_url(url: str | None) -> str | None:
    """Return ``url`` only if it is an ``http(s)`` link, else ``None``.

    ``html.escape`` stops attribute-breakout but not a ``javascript:``/``data:``
    scheme, so links built from API/roster data (a character's armory URL, an
    avatar) are scheme-checked before they reach an ``href``/``src``.
    """
    if url and url.lower().startswith(("http://", "https://")):
        return url
    return None


def _layout(guild_name: str, subtitle: str, body: str, active: str) -> str:
    def link(href: str, label: str, key: str) -> str:
        cls = " class='active'" if key == active else ""
        return f"<a href='{href}'{cls}>{label}</a>"

    nav = (
        link("/", "Roster", "roster")
        + link("/gm", "GM", "gm")
        + link("/raid", "Raid Leader", "raid")
        + link("/loot", "Loot", "loot")
        + "<span class='navsep'>·</span>"
        + link("/overview", "Overview", "overview")
        + link("/boards", "Boards", "boards")
        + link("/feeds", "Feeds", "feeds")
        + link("/recruiting", "Recruiting", "recruiting")
        + link("/members", "Members", "members")
        + link("/content", "Content", "content")
        + link("/settings", "Settings", "settings")
        + link("/admin", "Sync", "admin")
    )
    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(guild_name)} — {html.escape(subtitle)}</title>
<style>{_CSS}</style>
</head><body>
<header>
  <h1>{html.escape(guild_name)}</h1>
  <div style="font-size:.82rem;color:#bbb">{subtitle}</div>
  <nav>{nav}</nav>
</header>
<div class="wrap">{body}</div>
</body></html>"""


def _card(label: str, value: str) -> str:
    return f"<div class='card'><div class='big'>{value}</div><div class='lbl'>{html.escape(label)}</div></div>"


def _bar(pct: float | None) -> str:
    width = 0 if pct is None else max(0, min(100, pct))
    return f"<div class='bar'><span style='width:{width:.0f}%'></span></div>"


def _sort_link(label: str, key: str, current: str) -> str:
    arrow = " ▾" if key == current else ""
    return f'<a href="?sort={key}">{html.escape(label)}{arrow}</a>'


def _member_link(m: Member) -> str:
    href = f"/member/{quote(m.character_name)}?realm={quote(m.realm_slug)}"
    inner = f"<a href='{href}'>{html.escape(m.character_name)}</a>"
    profile = _safe_url(m.profile_url)
    if profile:
        inner += (
            f" <a href='{html.escape(profile)}' target='_blank' "
            "rel='noopener' title='Armory / Raider.IO'>↗</a>"
        )
    return inner


def _name_link(name: str, realm: str | None = None) -> str:
    href = f"/member/{quote(name)}" + (f"?realm={quote(realm)}" if realm else "")
    return f"<a href='{href}'>{html.escape(name)}</a>"


def _sparkline(
    values: list, *, width: int = 240, height: int = 40, stroke: str = "#ff7043"
) -> str:
    """A dependency-free inline-SVG sparkline. None values are skipped."""
    known = [v for v in values if v is not None]
    if len(known) < 2:
        return "<div style='color:#888;font-size:.8rem'>not enough data yet</div>"
    lo, hi = min(known), max(known)
    span = (hi - lo) or 1
    step = width / max(1, len(values) - 1)
    coords = [
        f"{i * step:.1f},{height - ((v - lo) / span) * (height - 4) - 2:.1f}"
        for i, v in enumerate(values)
        if v is not None
    ]
    return (
        f"<svg width='{width}' height='{height}' viewBox='0 0 {width} {height}' "
        f"preserveAspectRatio='none'><polyline fill='none' stroke='{stroke}' "
        f"stroke-width='2' points='{' '.join(coords)}'/></svg>"
    )


def _trend_card(label: str, values: list, delta: float | None) -> str:
    known = [v for v in values if v is not None]
    latest = f"{known[-1]:.0f}" if known else "—"
    d = ""
    if delta is not None and round(delta, 1) != 0:
        sign = "▲" if delta >= 0 else "▼"
        cls = "ok" if delta >= 0 else "bad"
        d = f" <span class='{cls}'>{sign} {abs(delta):.0f}</span>"
    return (
        f"<div class='card'><div class='lbl'>{html.escape(label)}</div>"
        f"{_sparkline(values)}<div style='font-size:.9rem'>{latest}{d}</div></div>"
    )


def _render_roster(
    guild_name: str,
    members: list[Member],
    *,
    sort: str,
    role: str | None,
    q: str | None,
) -> str:
    rows = []
    for i, m in enumerate(members, start=1):
        rows.append(
            "<tr>"
            f"<td class='num'>{i}</td>"
            f"<td>{_member_link(m)}</td>"
            f"<td>{_fmt(m.active_spec)} {_fmt(m.class_name)}</td>"
            f"<td>{_fmt(m.role)}</td>"
            f"<td class='num'>{_fmt(m.item_level)}</td>"
            f"<td class='num'>{_fmt(m.mythic_plus_score)}</td>"
            f"<td class='num'>{_fmt(m.best_parse_avg)}</td>"
            f"<td>{_fmt(m.raid_progression)}</td>"
            "</tr>"
        )
    table_body = "\n".join(rows) or "<tr><td colspan='8'>No members yet.</td></tr>"
    body = f"""
  <form method="get">
    <input type="search" name="q" placeholder="Search name…" value="{html.escape(q or '')}">
    <input type="hidden" name="sort" value="{html.escape(sort)}">
    <button type="submit">Search</button>
  </form>
  <table><thead><tr>
    <th class="num">#</th>
    <th>{_sort_link('Character', 'name', sort)}</th>
    <th>Spec / Class</th><th>Role</th>
    <th class="num">{_sort_link('ilvl', 'item_level', sort)}</th>
    <th class="num">{_sort_link('M+', 'mythic_plus_score', sort)}</th>
    <th class="num">{_sort_link('Parse', 'best_parse_avg', sort)}</th>
    <th>Raid</th>
  </tr></thead><tbody>{table_body}</tbody></table>"""
    return _layout(guild_name, f"{len(members)} members · sorted by {html.escape(sort)}", body, "roster")


def _render_gm(guild_name: str, r: dict) -> str:
    h = r["health"]
    score = h["score"]
    score_str = f"{score:.0f}" if score is not None else "—"
    delta = ""
    if score is not None and h.get("previous") is not None:
        diff = score - h["previous"]
        sign = "▲" if diff >= 0 else "▼"
        cls = "ok" if diff >= 0 else "bad"
        delta = f" <span class='{cls}' style='font-size:.9rem'>{sign} {abs(diff):.0f}</span>"

    cards = (
        f"<div class='card'><div class='big'>{score_str}{delta}</div><div class='lbl'>Guild Health</div></div>"
        + _card("Members", str(r["counts"]["total"]))
        + _card("Active", str(r["counts"]["active"]))
        + _card("Inactive", str(r["counts"]["inactive"]))
        + _card("Prep ready", _pct(r["prep_rate"]))
        + _card("Vault engaged", _pct(r["vault_rate"]))
    )

    comp_rows = "".join(
        f"<tr><td>{html.escape(k.title())}</td><td style='width:160px'>{_bar(v*100)}</td>"
        f"<td class='num'>{v*100:.0f}%</td></tr>"
        for k, v in h["components"].items()
    )

    off_rows = "".join(
        "<tr>"
        f"<td>{_name_link(o['name'])}</td>"
        f"<td>{_fmt(o['class'])}</td>"
        f"<td class='num'>{_fmt(o['guild_rank'])}</td>"
        f"<td>{_fmt(o['officer_role'])}</td>"
        f"<td class='num'>{_pct(o['attendance_pct'])}</td>"
        f"<td>{'<span class=ok>active</span>' if o['active'] else '<span class=bad>inactive</span>'}</td>"
        "</tr>"
        for o in r["officers"]
    ) or "<tr><td colspan='6'>No officers detected.</td></tr>"

    roles = " ".join(f"<span class='pill'>{html.escape(k)}: {v}</span>" for k, v in sorted(r["role_counts"].items()))
    classes = " ".join(f"<span class='pill'>{html.escape(k)}: {v}</span>" for k, v in sorted(r["class_counts"].items(), key=lambda kv: -kv[1]))
    buffs = " ".join(
        f"<span class='pill {'ok' if info['covered'] else 'bad'}'>{html.escape(b.split(' (')[0])}</span>"
        for b, info in r["buff_coverage"].items()
    )
    inactive = ", ".join(html.escape(m["name"]) for m in r["inactive_members"]) or "none"

    trends_html = ""
    t = r.get("trends")
    if t and t["count"] >= 2:
        s, d = t["series"], t["deltas"]
        cards_t = (
            _trend_card("Health", s["health_score"], d.get("health_score"))
            + _trend_card("Members", s["member_count"], d.get("member_count"))
            + _trend_card("Active", s["active_count"], d.get("active_count"))
            + _trend_card("Avg ilvl", s["avg_item_level"], d.get("avg_item_level"))
            + _trend_card("Avg M+", s["avg_mythic_plus"], d.get("avg_mythic_plus"))
        )
        trends_html = (
            f"<h2>Trends (last {t['count']} snapshots)</h2>"
            f"<div class='cards'>{cards_t}</div>"
        )

    body = f"""
  <div class="cards">{cards}</div>
  {trends_html}
  <h2>Health components</h2>
  <table><tbody>{comp_rows}</tbody></table>
  <h2>Officer corps</h2>
  <table><thead><tr><th>Officer</th><th>Class</th><th class="num">Rank</th><th>Role</th><th class="num">Attendance</th><th>Status</th></tr></thead>
  <tbody>{off_rows}</tbody></table>
  <h2>Composition</h2>
  <p>Roles: {roles}</p>
  <p>Classes: {classes}</p>
  <h2>Raid buff coverage</h2>
  <p>{buffs}</p>
  <h2>Progression</h2>
  <p>{_fmt(r['progression'])}</p>
  <h2>Inactive members</h2>
  <p style="color:#bbb">{inactive}</p>"""
    return _layout(guild_name, "GM dashboard — guild health", body, "gm")


def _render_raid(guild_name: str, r: dict) -> str:
    rows = ""
    for i, row in enumerate(r["rows"], start=1):
        flags = " ".join(f"<span class='pill'>{html.escape(f)}</span>" for f in row["flags"])
        rows += (
            "<tr>"
            f"<td class='num'>{i}</td>"
            f"<td>{_name_link(row['name'])}</td>"
            f"<td>{_fmt(row['spec'])} {_fmt(row['class'])}</td>"
            f"<td>{_fmt(row['role'])}</td>"
            f"<td class='num'>{_fmt(row['item_level'])}</td>"
            f"<td class='num'>{_fmt(row['best_parse_avg'])}</td>"
            f"<td class='num'>{_pct(row['attendance_pct'])}</td>"
            f"<td class='num'>{_pct(row['wishlist_pct'])}</td>"
            f"<td style='width:120px'>{_bar(row['readiness'])}</td>"
            f"<td class='num'>{row['readiness']:.0f}</td>"
            f"<td class='flags'>{flags}</td>"
            "</tr>"
        )
    rows = rows or "<tr><td colspan='11'>No raiders.</td></tr>"

    missing = r["missing_buffs"]
    buff_line = (
        "<span class='ok'>all raid buffs covered</span>"
        if not missing
        else "missing: " + " ".join(f"<span class='pill bad'>{html.escape(b.split(' (')[0])}</span>" for b in missing)
    )
    team_label = f" — team {html.escape(r['team'])}" if r.get("team") else ""

    body = f"""
  <h2>Buff coverage</h2><p>{buff_line}</p>
  <h2>Readiness board</h2>
  <table><thead><tr>
    <th class="num">#</th><th>Raider</th><th>Spec / Class</th><th>Role</th>
    <th class="num">ilvl</th><th class="num">Parse</th><th class="num">Att%</th>
    <th class="num">Wish%</th><th>Readiness</th><th class="num">Score</th><th>Flags</th>
  </tr></thead><tbody>{rows}</tbody></table>"""
    return _layout(guild_name, f"Raid Leader — {r['count']} raiders{team_label}", body, "raid")


def _render_loot(guild_name: str, r: dict) -> str:
    fair = "".join(
        "<tr>"
        f"<td>{_name_link(f['name'])}</td>"
        f"<td class='num'>{f['items_won']}</td>"
        f"<td class='num'>{_fmt(f['raids_attended'])}</td>"
        f"<td class='num'>{_fmt(f['items_per_raid'])}</td>"
        f"<td class='num'>{_pct(f['attendance_pct'])}</td>"
        f"<td class='num'>{_fmt(round(f['days_since_last_loot']) if f['days_since_last_loot'] is not None else None)}</td>"
        "</tr>"
        for f in r["fairness"]
    ) or "<tr><td colspan='6'>No loot recorded.</td></tr>"

    recent = "".join(
        "<tr>"
        f"<td>{_fmt(e['awarded_at'])}</td>"
        f"<td>{_name_link(e['character_name'])}</td>"
        f"<td>{_fmt(e['item_name'])}</td>"
        f"<td>{_fmt(e['boss'])}</td>"
        f"<td>{_fmt(e['difficulty'])}</td>"
        f"<td>{_fmt(e['response'])}</td>"
        "</tr>"
        for e in r["recent"]
    ) or "<tr><td colspan='6'>No loot recorded.</td></tr>"

    body = f"""
  <p style="color:#bbb">Tip: <code>/api/loot/priority?item=&lt;name&gt;</code> ranks who has the strongest case for a drop.</p>
  <h2>Fairness</h2>
  <table><thead><tr><th>Raider</th><th class="num">Items</th><th class="num">Raids</th>
  <th class="num">Items/raid</th><th class="num">Att%</th><th class="num">Days since</th></tr></thead>
  <tbody>{fair}</tbody></table>
  <h2>Recent awards</h2>
  <table><thead><tr><th>When</th><th>Raider</th><th>Item</th><th>Boss</th><th>Diff</th><th>Response</th></tr></thead>
  <tbody>{recent}</tbody></table>"""
    return _layout(guild_name, f"Loot — {r['total_awards']} awards tracked", body, "loot")


def _render_member(guild_name: str, d: dict) -> str:
    m = d["member"]
    rd = d["readiness"]
    att = d["attendance"]
    wl = d["wishlist"]

    subtitle_bits = [
        b
        for b in (
            f"{_fmt(m['spec'])} {_fmt(m['class'])}".strip(),
            m["role"],
            f"rank {m['guild_rank']}" if m["guild_rank"] is not None else None,
            f"team {m['raid_team']}" if m["raid_team"] else None,
            "🟢 active" if d["active"] else "🔴 inactive",
        )
        if b
    ]
    cards = (
        _card("Readiness", f"{rd['score']:.0f}" if rd["score"] is not None else "—")
        + _card("Item level", _fmt(m["item_level"]))
        + _card("Avg ilvl", _fmt(m["average_item_level"]))
        + _card("M+ score", _fmt(m["mythic_plus_score"]))
        + _card("Best parse", _fmt(m["best_parse_avg"]))
        + _card("Attendance", _pct(att["pct"]))
    )

    flags = (
        " ".join(f"<span class='pill'>{html.escape(f)}</span>" for f in rd["flags"])
        or "<span class='ok'>no issues flagged</span>"
    )

    prep_bits = []
    if m["missing_enchants"] is not None:
        cls = "ok" if not m["missing_enchants"] else "bad"
        prep_bits.append(f"<span class='{cls}'>enchants missing: {m['missing_enchants']}</span>")
    if m["missing_gems"] is not None:
        cls = "ok" if not m["missing_gems"] else "bad"
        prep_bits.append(f"<span class='{cls}'>gems missing: {m['missing_gems']}</span>")
    if m["vault_slots_filled"] is not None:
        prep_bits.append(f"vault slots: {m['vault_slots_filled']}/9")
    prep = " · ".join(prep_bits) or "no prep data"

    wl_list = []
    for w in wl["items"]:
        up = f"+{w['upgrade_pct']:.1f}%" if w["upgrade_pct"] else "—"
        check = "✅" if w["obtained"] else "⬜"
        star = " ⭐" if w["is_bis"] else ""
        wl_list.append(
            f"<tr><td>{check}</td>"
            f"<td>{_fmt(w['item_name'] or w['item_id'])}{star}</td>"
            f"<td class='num'>{up}</td>"
            f"<td>{_fmt(w['boss'])}</td><td>{_fmt(w['difficulty'])}</td></tr>"
        )
    wl_rows = "".join(wl_list) or "<tr><td colspan='5'>No wishlist uploaded.</td></tr>"

    loot_rows = "".join(
        "<tr>"
        f"<td>{_fmt((e['awarded_at'] or '')[:10])}</td>"
        f"<td>{_fmt(e['item_name'] or e['item_id'])}</td>"
        f"<td>{_fmt(e['boss'])}</td><td>{_fmt(e['difficulty'])}</td>"
        f"<td>{_fmt(e['response'])}</td>"
        "</tr>"
        for e in d["loot"]
    ) or "<tr><td colspan='5'>No loot recorded.</td></tr>"

    thumb_url = _safe_url(m.get("thumbnail_url"))
    thumb = (
        f"<img src='{html.escape(thumb_url)}' alt='' "
        "style='border-radius:8px;width:84px;height:84px;object-fit:cover;float:right'>"
        if thumb_url
        else ""
    )
    alts = ", ".join(_name_link(a) for a in d["alts"]) or "none"
    discord = f"<@{m['discord_id']}> linked" if m["discord_id"] else "not linked"
    notes = html.escape(m["notes"]) if m["notes"] else "—"

    # GRM-sourced identity (join date, rank, account standing) from the spine.
    ident = d.get("identity") or {}
    standing = None
    if ident.get("is_main"):
        standing = "main character"
    elif ident.get("is_main") is False and ident.get("main_name"):
        standing = f"alt of {_name_link(str(ident['main_name']).split('-')[0])}"
    identity_bits = [
        b
        for b in (
            f"joined {html.escape(ident['join_date'])}" if ident.get("join_date") else None,
            f"rank {html.escape(ident['rank_name'])}" if ident.get("rank_name") else None,
            standing,
        )
        if b
    ]
    identity_line = f"<p>{' · '.join(identity_bits)}</p>" if identity_bits else ""

    profile_url = _safe_url(m["profile_url"])
    armory = (
        f" · <a href='{html.escape(profile_url)}' target='_blank' rel='noopener'>Armory / Raider.IO ↗</a>"
        if profile_url
        else ""
    )

    comp_rows = "".join(
        f"<tr><td>{html.escape(k.title())}</td>"
        f"<td style='width:140px'>{_bar(v*100)}</td>"
        f"<td class='num'>{v*100:.0f}%</td></tr>"
        for k, v in rd["components"].items()
    )

    body = f"""
  {thumb}
  <p style="color:#bbb;margin-top:0">{' · '.join(html.escape(b) for b in subtitle_bits)}{armory}</p>
  <div class="cards">{cards}</div>
  <h2>Readiness</h2>
  <p class="flags">{flags}</p>
  <table><tbody>{comp_rows}</tbody></table>
  <h2>Preparedness</h2><p>{prep}</p>
  <h2>Attendance</h2>
  <p>{att['present']}/{att['scheduled']} raids ({_pct(att['pct'])})</p>
  <h2>Wishlist ({wl['obtained']}/{wl['total']}, {_pct(wl['pct'])})</h2>
  <table><thead><tr><th></th><th>Item</th><th class="num">Upgrade</th><th>Boss</th><th>Diff</th></tr></thead>
  <tbody>{wl_rows}</tbody></table>
  <h2>Loot history</h2>
  <table><thead><tr><th>When</th><th>Item</th><th>Boss</th><th>Diff</th><th>Response</th></tr></thead>
  <tbody>{loot_rows}</tbody></table>
  <h2>Identity</h2>
  {identity_line}
  <p>Alts: {alts}</p>
  <p>Discord: {discord}</p>
  <p>Notes: {notes}</p>
  <p style="color:#888;font-size:.8rem">Source: {_fmt(m['source'])} · synced {_fmt(m['last_synced'])} · full JSON at <code>/api/members/{html.escape(m['character_name'])}</code></p>
  <p><a href="/">← roster</a></p>"""
    return _layout(guild_name, m["display"], body, "roster")


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value:.0f}%"


# JS for the Content editor: gather the section's textareas (dropping empty
# ones), PUT them as JSON (the auth cookie rides along), and keep a live
# per-message character count against the Discord limit. A plain string, not an
# f-string, so the JS braces stay readable.
_CONTENT_SCRIPT = """
<script>
async function savePage(name){
  const sec=document.querySelector('[data-page="'+name+'"]');
  const posts=Array.from(sec.querySelectorAll('textarea'))
    .map(t=>t.value).filter(v=>v.trim().length);
  const st=document.getElementById('st-'+name);
  st.className='status'; st.textContent='Saving…';
  try{
    const r=await fetch('/api/content/'+name,{method:'PUT',
      headers:{'Content-Type':'application/json'},body:JSON.stringify({posts:posts})});
    const j=await r.json().catch(()=>({}));
    if(r.ok){st.className='status ok';
      st.textContent='Saved. Goes live on the next refresh, or /page refresh in Discord.';}
    else{st.className='status bad'; st.textContent=j.detail||'Save failed.';}
  }catch(e){st.className='status bad'; st.textContent='Save failed: '+e;}
}
async function revertPage(name){
  if(!confirm('Discard the custom copy and return to the bundled default?'))return;
  await fetch('/api/content/'+name,{method:'DELETE'});
  location.reload();
}
document.querySelectorAll('.doc textarea').forEach(t=>{
  const count=t.closest('.post').querySelector('.count');
  const update=()=>{count.textContent=t.value.length+'/'+t.maxLength;
    count.classList.toggle('over', t.value.length>=t.maxLength);};
  t.addEventListener('input',update); update();
});
</script>
"""


def _render_content(config: Config, items: list[dict]) -> str:
    sections = []
    for item in items:
        source = (
            "<span class='pill ok'>custom copy</span>"
            if item["source"] == "custom"
            else "<span class='pill'>bundled copy</span>"
        )
        updated = (
            f" · saved {html.escape(item['updated_at'])}" if item["updated_at"] else ""
        )
        placement = item.get("placement")
        if placement and placement["message_ids"]:
            where = (
                f"Posted in channel <code>{html.escape(placement['posted_channel_id'] or '?')}</code>"
                f" ({len(placement['message_ids'])} message(s))."
            )
        elif placement:
            where = "Registered but not posted yet (guardrail or pending refresh)."
        else:
            where = "Not placed in Discord yet. Use <code>/page post</code>."

        posts_html = ""
        for i, post in enumerate(item["posts"], start=1):
            posts_html += (
                "<div class='post'>"
                f"<label>Message {i} <span class='count'></span></label>"
                f"<textarea maxlength='{MAX_POST_CHARS}'>{html.escape(post)}</textarea>"
                "</div>"
            )
        posts_html += (
            "<div class='post'>"
            "<label>New message (optional) <span class='count'></span></label>"
            f"<textarea maxlength='{MAX_POST_CHARS}' "
            "placeholder='Leave empty, or add another message to the page.'></textarea>"
            "</div>"
        )
        name = html.escape(item["page"])
        sections.append(
            f"<section class='doc' data-page='{name}'>"
            f"<h2>{html.escape(item['title'])} {source}<span style='font-size:.75rem;"
            f"color:#888;font-weight:400'>{updated}</span></h2>"
            f"<p style='color:#bbb;font-size:.85rem'>{where}</p>"
            f"{posts_html}"
            f"<p><button onclick=\"savePage('{name}')\">Save</button> "
            f"<button class='ghost' onclick=\"revertPage('{name}')\">Revert to bundled copy</button>"
            f"<span class='status' id='st-{name}'></span></p>"
            "</section>"
        )

    body = (
        "<p style='color:#bbb'>The copy for Calcifer's sticky pages. Each box is one "
        "Discord message (markdown headers, <code>-#</code> subtext, emoji and "
        "<code>{tokens}</code> like <code>{member_count}</code> all work; pages post "
        "without pinging). An empty box is dropped on save. Edits go live on the "
        "next page refresh, or instantly with <code>/page refresh</code> in Discord.</p>"
        + "".join(sections)
        + _CONTENT_SCRIPT
    )
    return _layout(config.guild_name, "Content — sticky page copy", body, "content")


def _render_content_locked(config: Config, *, error: bool = False) -> str:
    if not config.sync_token:
        body = (
            "<h2>Content editing</h2>"
            "<p class='bad'>The dashboard editor is disabled.</p>"
            "<p>Set the <code>SYNC_TOKEN</code> environment variable to enable it "
            "(it shares the Sync page's unlock).</p>"
        )
        return _layout(config.guild_name, "Content", body, "content")
    wrong = "<p class='bad'>Invalid token.</p>" if error else ""
    body = f"""
  <h2>Content — enter token</h2>
  <p>Provide the sync token to edit the sticky pages' copy.</p>
  <form id="unlock" onsubmit="return unlock(event)">
    <input id="tok" type="password" placeholder="SYNC_TOKEN" autofocus>
    <button type="submit">Unlock</button>
  </form>{wrong}
  <p id="err" class="bad"></p>
  <script>
  async function unlock(e){{e.preventDefault();
    const r=await fetch('/api/sync/unlock',{{method:'POST',
      headers:{{'Content-Type':'application/json'}},
      body:JSON.stringify({{token:document.getElementById('tok').value}})}});
    if(r.ok){{location.href='/content';}}
    else{{document.getElementById('err').textContent='Invalid token.';}}
    return false;}}
  </script>"""
    return _layout(config.guild_name, "Content", body, "content")


def _render_admin(
    config: Config, manager: "SyncManager", *, authed: bool, error: bool = False
) -> str:
    if not config.sync_token:
        body = (
            "<h2>Manual sync</h2>"
            "<p class='bad'>The browser sync trigger is disabled.</p>"
            "<p>Set the <code>SYNC_TOKEN</code> environment variable to enable it. "
            "You can still sync with the Discord <code>/sync</code> command or "
            "<code>python -m scripts.sync_cli</code>.</p>"
        )
        return _layout(config.guild_name, "Admin", body, "admin")

    if not authed:
        wrong = "<p class='bad'>Invalid token.</p>" if error else ""
        # Unlock posts the token as JSON (never in the URL); the server stores it
        # in an http-only cookie and the page reloads into the unlocked view.
        body = f"""
  <h2>Admin — enter token</h2>
  <p>Provide the sync token to access the manual sync trigger.</p>
  <form id="unlock" onsubmit="return unlock(event)">
    <input id="tok" type="password" placeholder="SYNC_TOKEN" autofocus>
    <button type="submit">Unlock</button>
  </form>{wrong}
  <p id="err" class="bad"></p>
  <script>
  async function unlock(e){{e.preventDefault();
    const r=await fetch('/api/sync/unlock',{{method:'POST',
      headers:{{'Content-Type':'application/json'}},
      body:JSON.stringify({{token:document.getElementById('tok').value}})}});
    if(r.ok){{location.href='/admin';}}
    else{{document.getElementById('err').textContent='Invalid token.';}}
    return false;}}
  </script>"""
        return _layout(config.guild_name, "Admin", body, "admin")

    st = manager.status()
    current = (
        "Sync running…"
        if st["running"]
        else (st["last_summary"] or st["last_error"] or "idle")
    )
    last = f" · last finished {st['last_finished']}" if st["last_finished"] else ""
    # The sync call carries the http-only cookie automatically, so the token is
    # never placed in the URL.
    script = (
        "<script>\n"
        "async function doSync(){document.getElementById('go').disabled=true;"
        "await fetch('/api/sync',{method:'POST'});poll();}\n"
        "async function poll(){try{const s=await (await fetch('/api/sync/status')).json();"
        "document.getElementById('st').textContent=s.running?'Sync running…':"
        "(s.last_summary||s.last_error||'idle');"
        "document.getElementById('go').disabled=s.running;"
        "if(s.running)setTimeout(poll,2000);}catch(e){}}\n"
        "poll();\n"
        "</script>"
    )
    body = (
        "<h2>Manual sync</h2>"
        "<p>Pull the latest guild character data &amp; details from the configured "
        "sources (WoW Audit, Raider.IO, …).</p>"
        "<p><button id='go' onclick='doSync()'>Sync now</button></p>"
        f"<p>Status: <span id='st'>{html.escape(current)}</span>{last}</p>"
        "<p style='color:#bbb;font-size:.85rem'>You are unlocked via a secure "
        "http-only cookie, so the token stays out of the URL. Scripts can "
        "<code>POST /api/sync</code> with an <code>X-Sync-Token</code> header.</p>"
        + script
    )
    return _layout(config.guild_name, "Admin — manual sync", body, "admin")


def _admin_locked(
    config: Config, *, active: str, title: str, error: bool = False
) -> str:
    """The shared unlock form for a gated admin page (cookie absent/invalid)."""
    if not config.sync_token:
        body = (
            f"<h2>{html.escape(title)}</h2>"
            "<p class='bad'>Admin controls are disabled.</p>"
            "<p>Set the <code>SYNC_TOKEN</code> environment variable to enable "
            "the dashboard's controls.</p>"
        )
        return _layout(config.guild_name, title, body, active)
    wrong = "<p class='bad'>Invalid token.</p>" if error else ""
    body = f"""
  <h2>{html.escape(title)} — enter token</h2>
  <p>Provide the sync token to unlock the admin dashboard.</p>
  <form id="unlock" onsubmit="return unlock(event)">
    <input id="tok" type="password" placeholder="SYNC_TOKEN" autofocus>
    <button type="submit">Unlock</button>
  </form>{wrong}
  <p id="err" class="bad"></p>
  <script>
  async function unlock(e){{e.preventDefault();
    const r=await fetch('/api/sync/unlock',{{method:'POST',
      headers:{{'Content-Type':'application/json'}},
      body:JSON.stringify({{token:document.getElementById('tok').value}})}});
    if(r.ok){{location.reload();}}
    else{{document.getElementById('err').textContent='Invalid token.';}}
    return false;}}
  </script>"""
    return _layout(config.guild_name, title, body, active)


# Shared fetch helpers for the client-rendered admin pages. A 401 (expired
# cookie) reloads into the unlock form; other errors surface an alert. Plain
# (non-f) string so the JS braces need no escaping.
_ADMIN_JS = """
<script>
async function _api(p){const r=await fetch(p,{headers:{'Accept':'application/json'}});
  if(r.status===401){location.reload();return null;}
  if(!r.ok)throw new Error(await r.text());return r.json();}
async function _send(m,p,b){const o={method:m};
  if(b!==undefined){o.headers={'Content-Type':'application/json'};o.body=JSON.stringify(b);}
  const r=await fetch(p,o);
  if(r.status===401){location.reload();return null;}
  if(!r.ok){let d=r.statusText;try{d=(await r.json()).detail||d;}catch(e){}alert('Error: '+d);return null;}
  try{return await r.json();}catch(e){return {};}}
function _esc(s){const d=document.createElement('div');d.textContent=(s==null?'':String(s));return d.innerHTML;}
function _code(s){return s?('<code>'+_esc(s)+'</code>'):'<span class=muted>—</span>';}
</script>
"""


def _render_overview(config: Config) -> str:
    body = "<h2>System overview</h2><div id=app class=muted>Loading…</div>" + _ADMIN_JS + """
<script>
(async function(){
 const app=document.getElementById('app');
 try{
  const d=await _api('/api/overview'); if(!d)return;
  const c=d.counts;
  const cards=[['Members',c.members],
    ['Boards',c.boards+' ('+c.boards_promoted+' live)'],
    ['Feed rules',c.feed_rules+' ('+c.feed_rules_enabled+' on)'],
    ['Leads',c.leads],['Health',d.health==null?'—':Math.round(d.health)],
    ['Posting',d.posting.mode]]
    .map(x=>'<div class=card><div class=big>'+_esc(x[1])+'</div><div class=lbl>'+_esc(x[0])+'</div></div>').join('');
  const feat=Object.keys(d.features).map(k=>
    '<span class="tag '+(d.features[k]?'on':'off')+'">'+_esc(k)+'</span>').join(' ');
  const s=d.sync||{};
  const sl=s.running?'Sync running…':_esc(s.last_summary||s.last_error||'idle');
  app.classList.remove('muted');
  app.innerHTML='<div class=cards>'+cards+'</div>'+
    '<div class=panel><h2>Integrations &amp; loops</h2><p>'+feat+'</p>'+
    '<p class=muted>Green = enabled. Loop toggles come from the environment and need a restart to change.</p></div>'+
    '<div class=panel><h2>Sync</h2><p>'+sl+(s.last_finished?(' · last finished '+_esc(s.last_finished)):'')+
    '</p><p><a href="/admin">Manual sync →</a></p></div>'+
    '<div class=panel><h2>Posting guardrail</h2><p>Mode <b>'+_esc(d.posting.mode)+'</b> · test channel '+
    _code(d.posting.test_channel_id)+' · <a href="/settings">change →</a></p></div>';
 }catch(e){app.textContent='Failed to load: '+e;}
})();
</script>"""
    return _layout(config.guild_name, "Overview", body, "overview")


def _render_boards(config: Config) -> str:
    body = """
<h2>Boards</h2>
<div class=panel>
  <h2>Create a board</h2>
  <div class=row>
    <div class=field><label>Type</label><select id=btype></select></div>
    <div class=field><label>Production channel ID</label><input id=bchan placeholder="123456789012345678"></div>
    <button id=bcreate>Create</button>
  </div>
  <p class=muted>New boards start sandboxed; promote one to send it to its real channel (needs posting mode <b>live</b>).</p>
</div>
<div id=blist class=muted>Loading…</div>
<div class=panel><h2>Sticky pages</h2>
  <p class=muted>Placement only — edit the copy under <a href="/content">Content</a>.</p>
  <div id=plist class=muted>Loading…</div>
</div>
""" + _ADMIN_JS + """
<script>
const CFG={};
async function loadBoards(){
 const d=await _api('/api/boards'); if(!d)return;
 document.getElementById('btype').innerHTML=d.available_types
   .map(t=>'<option value="'+_esc(t.type)+'">'+_esc(t.name)+'</option>').join('');
 const wrap=document.getElementById('blist'); wrap.classList.remove('muted');
 if(!d.boards.length){wrap.innerHTML='<p class=muted>No boards yet.</p>';return;}
 let rows='';
 d.boards.forEach(b=>{CFG[b.id]=b.config;
   const dest=b.effective_destination?_code(b.effective_destination):'<span class=muted>nowhere</span>';
   rows+='<tr><td>#'+b.id+'</td><td>'+_esc(b.name)+'</td><td>'+_code(b.channel_id)+'</td>'+
     '<td><span class="tag '+b.state+'">'+b.state+'</span></td>'+
     '<td>'+(b.posted?'posted':'—')+'</td><td>'+dest+'</td><td>'+
     (b.promoted?'<button class="sm" data-id="'+b.id+'" data-act="demote">Demote</button>'
                :'<button class="sm" data-id="'+b.id+'" data-act="promote">Promote</button>')+
     (b.enabled?'<button class="sm ghost" data-id="'+b.id+'" data-act="disable">Disable</button>'
               :'<button class="sm" data-id="'+b.id+'" data-act="enable">Enable</button>')+
     '<button class="sm" data-id="'+b.id+'" data-act="refresh">Refresh</button>'+
     '<button class="sm" data-id="'+b.id+'" data-act="config">Config</button>'+
     '<button class="sm danger" data-id="'+b.id+'" data-act="remove">Remove</button></td></tr>';
 });
 wrap.innerHTML='<table><thead><tr><th>#</th><th>Board</th><th>Channel</th><th>State</th>'+
   '<th>Posted</th><th>Goes to</th><th>Actions</th></tr></thead><tbody>'+rows+'</tbody></table>';
}
async function loadPages(){
 const d=await _api('/api/pages'); if(!d)return;
 const wrap=document.getElementById('plist'); wrap.classList.remove('muted');
 wrap.innerHTML='<table><thead><tr><th>Page</th><th>Lives in</th><th>Posted</th><th>Actions</th></tr></thead><tbody>'+
   d.pages.map(p=>'<tr><td>'+_esc(p.title)+'</td><td>'+_code(p.channel_id)+'</td>'+
     '<td>'+(p.placed?(p.message_count+' msg'):'—')+'</td><td>'+
     '<button class="sm" data-page="'+_esc(p.name)+'" data-pact="place">Place</button>'+
     '<button class="sm" data-page="'+_esc(p.name)+'" data-pact="refresh">Refresh</button>'+
     '<button class="sm danger" data-page="'+_esc(p.name)+'" data-pact="remove">Remove</button>'+
     '</td></tr>').join('')+'</tbody></table>';
}
document.getElementById('bcreate').addEventListener('click',async()=>{
  const board_type=document.getElementById('btype').value;
  const channel_id=document.getElementById('bchan').value.trim();
  if(!channel_id){alert('Enter a channel ID');return;}
  if(await _send('POST','/api/boards',{board_type,channel_id})){document.getElementById('bchan').value='';loadBoards();}
});
document.getElementById('blist').addEventListener('click',async(e)=>{
  const b=e.target.closest('button[data-act]'); if(!b)return;
  const id=b.dataset.id, act=b.dataset.act;
  if(act==='remove'){if(!confirm('Remove board #'+id+'?'))return;
    if(await _send('DELETE','/api/boards/'+id))loadBoards();return;}
  if(act==='config'){const v=prompt('Board config (JSON):',JSON.stringify(CFG[id]||{}));
    if(v===null)return;let cfg;try{cfg=JSON.parse(v);}catch(x){alert('Invalid JSON');return;}
    if(await _send('PUT','/api/boards/'+id+'/config',{config:cfg}))loadBoards();return;}
  const r=await _send('POST','/api/boards/'+id+'/'+act);
  if(r){if(act==='refresh'&&r.status)b.title=r.status;loadBoards();}
});
document.getElementById('plist').addEventListener('click',async(e)=>{
  const b=e.target.closest('button[data-pact]'); if(!b)return;
  const name=b.dataset.page, act=b.dataset.pact;
  if(act==='place'){const ch=prompt('Channel ID to post '+name+' in:'); if(!ch)return;
    if(await _send('POST','/api/pages/'+name+'/place',{channel_id:ch.trim()}))loadPages();return;}
  if(act==='remove'){if(!confirm('Remove '+name+'?'))return;
    if(await _send('DELETE','/api/pages/'+name))loadPages();return;}
  if(await _send('POST','/api/pages/'+name+'/refresh'))loadPages();
});
loadBoards();loadPages();
</script>"""
    return _layout(config.guild_name, "Boards", body, "boards")


def _render_feeds(config: Config) -> str:
    body = """
<h2>Feed hub</h2>
<div class=panel>
  <h2>Add a routing rule</h2>
  <div class=row>
    <div class=field><label>Source</label><input id=fsource placeholder="wowhead"></div>
    <div class=field><label>Event type (optional)</label><input id=ftype placeholder="wowhead.patch-notes"></div>
    <div class=field><label>Contains (comma-sep, optional)</label><input id=fcontains placeholder="hotfix, ptr"></div>
    <div class=field><label>Channel ID</label><input id=fchan placeholder="123456789012345678"></div>
    <div class=field><label>Colour (optional)</label><input id=fcolor placeholder="blue"></div>
    <div class=field><label>Ping (optional)</label><input id=fping placeholder="&lt;@&amp;roleid&gt;"></div>
    <button id=fcreate>Add rule</button>
  </div>
</div>
<div id=health class=muted>Loading…</div>
<div id=frules class=muted>Loading…</div>
<div class=panel>
  <h2>Muted sources</h2>
  <div class=row>
    <div class=field><label>Mute a source</label><input id=fmute placeholder="raiderio_mplus"></div>
    <button id=fmutebtn>Mute</button>
  </div>
  <div id=mutes class=muted>Loading…</div>
</div>
""" + _ADMIN_JS + """
<script>
async function load(){
 const d=await _api('/api/feeds'); if(!d)return;
 document.getElementById('health').classList.remove('muted');
 document.getElementById('health').innerHTML='<div class=cards>'+
   '<div class=card><div class=big>'+d.pending_outbox+'</div><div class=lbl>Outbox pending</div></div>'+
   '<div class=card><div class=big>'+d.pending_digests+'</div><div class=lbl>Digest pending</div></div>'+
   '<div class=card><div class=big>'+_esc(d.default_channel_id||'—')+'</div><div class=lbl>Default channel</div></div>'+
   '</div>';
 const fr=document.getElementById('frules'); fr.classList.remove('muted');
 if(!d.rules.length){fr.innerHTML='<p class=muted>No routing rules — feeds fall back to the default channel.</p>';}
 else{fr.innerHTML='<table><thead><tr><th>#</th><th>Source</th><th>Event type</th><th>Match</th>'+
   '<th>Channel</th><th>State</th><th>Actions</th></tr></thead><tbody>'+
   d.rules.map(r=>{const m=(r.match&&r.match.contains)?r.match.contains.join(', '):'';
     return '<tr><td>#'+r.id+'</td><td>'+_esc(r.source)+'</td><td>'+_esc(r.event_type||'—')+'</td>'+
     '<td>'+_esc(m||'—')+'</td><td>'+_code(r.channel_id)+'</td>'+
     '<td><span class="tag '+(r.enabled?'on':'off')+'">'+(r.enabled?'on':'off')+'</span></td><td>'+
     (r.enabled?'<button class="sm ghost" data-id="'+r.id+'" data-act="disable">Disable</button>'
               :'<button class="sm" data-id="'+r.id+'" data-act="enable">Enable</button>')+
     '<button class="sm danger" data-id="'+r.id+'" data-act="remove">Remove</button></td></tr>';
   }).join('')+'</tbody></table>';}
 const mu=document.getElementById('mutes'); mu.classList.remove('muted');
 mu.innerHTML=d.muted_sources.length?d.muted_sources.map(s=>
   '<span class=pill>'+_esc(s)+' <button class="sm" data-src="'+_esc(s)+'">unmute</button></span>').join(' ')
   :'<p class=muted>No sources muted.</p>';
}
document.getElementById('fcreate').addEventListener('click',async()=>{
  const body={source:document.getElementById('fsource').value.trim(),
    channel_id:document.getElementById('fchan').value.trim(),
    event_type:document.getElementById('ftype').value.trim(),
    contains:document.getElementById('fcontains').value.trim(),
    color:document.getElementById('fcolor').value.trim(),
    ping:document.getElementById('fping').value.trim()};
  if(!body.source||!body.channel_id){alert('Source and channel ID are required');return;}
  if(await _send('POST','/api/feeds/rules',body)){
    ['fsource','ftype','fcontains','fchan','fcolor','fping'].forEach(i=>document.getElementById(i).value='');load();}
});
document.getElementById('frules').addEventListener('click',async(e)=>{
  const b=e.target.closest('button[data-act]'); if(!b)return;
  const id=b.dataset.id, act=b.dataset.act;
  if(act==='remove'){if(!confirm('Remove rule #'+id+'?'))return;
    if(await _send('DELETE','/api/feeds/rules/'+id))load();return;}
  if(await _send('POST','/api/feeds/rules/'+id+'/'+act))load();
});
document.getElementById('fmutebtn').addEventListener('click',async()=>{
  const s=document.getElementById('fmute').value.trim(); if(!s)return;
  if(await _send('POST','/api/feeds/mute',{source:s})){document.getElementById('fmute').value='';load();}
});
document.getElementById('mutes').addEventListener('click',async(e)=>{
  const b=e.target.closest('button[data-src]'); if(!b)return;
  if(await _send('DELETE','/api/feeds/mute/'+encodeURIComponent(b.dataset.src)))load();
});
load();
</script>"""
    return _layout(config.guild_name, "Feeds", body, "feeds")


def _render_settings(config: Config) -> str:
    body = ("<h2>Runtime settings</h2>"
            "<p class=muted>These override the environment defaults live (no redeploy). "
            "Clearing a value reverts it to the environment.</p>"
            "<div id=app class=muted>Loading…</div>") + _ADMIN_JS + """
<script>
async function load(){
 const items=await _api('/api/settings'); if(!items)return;
 const app=document.getElementById('app'); app.classList.remove('muted');
 app.innerHTML=items.map(s=>{
  let input;
  if(s.kind==='choice'){input='<select id="in_'+s.key+'">'+
    s.choices.map(c=>'<option value="'+_esc(c)+'"'+(String(s.effective)===c?' selected':'')+'>'+_esc(c)+'</option>').join('')+'</select>';}
  else{input='<input id="in_'+s.key+'" value="'+_esc(s.override==null?'':s.override)+'" placeholder="'+_esc(s.env==null?'(unset)':s.env)+'">';}
  const ov=s.override!=null?'<span class="tag on">override</span>':'<span class=muted>env default</span>';
  return '<div class=panel><h2>'+_esc(s.label)+' '+ov+'</h2>'+
    '<p class=muted>'+_esc(s.help)+'</p>'+
    '<div class=row><div class=field><label>Value</label>'+input+'</div>'+
    '<button data-key="'+s.key+'" data-act="save">Save</button>'+
    '<button class="ghost" data-key="'+s.key+'" data-act="revert">Revert to env</button></div>'+
    '<p class=muted>Environment default: '+_code(s.env)+' · effective now: '+_code(s.effective)+'</p></div>';
 }).join('');
}
document.getElementById('app').addEventListener('click',async(e)=>{
  const b=e.target.closest('button[data-act]'); if(!b)return;
  const key=b.dataset.key;
  if(b.dataset.act==='revert'){if(await _send('DELETE','/api/settings/'+key))load();return;}
  const val=document.getElementById('in_'+key).value;
  const o={}; o[key]=val;
  if(await _send('PUT','/api/settings',{settings:o}))load();
});
load();
</script>"""
    return _layout(config.guild_name, "Settings", body, "settings")


def _render_recruiting(config: Config) -> str:
    body = """
<h2>Recruitment funnel</h2>
<div class=panel id=capture></div>
<div class=panel><h2>Leads</h2><div id=leads class=muted>Loading…</div></div>
<div class=panel><h2>Board config</h2><div id=cfg class=muted>Loading…</div></div>
""" + _ADMIN_JS + """
<script>
let DATA={};
function leadRow(l){
 const opts=DATA.lead_states.map(s=>'<option value="'+s+'"'+(s===l.state?' selected':'')+'>'+s+'</option>').join('');
 const who=_esc(l.name)+' <span class=muted>'+_esc(l.class_name||'')+' '+_esc(l.role||'')+'</span>'+(l.lfg?' <span class="tag on">LFG</span>':'');
 return '<tr><td>#'+l.id+'</td><td>'+who+'</td><td class=num>'+_esc(l.score==null?'—':Math.round(l.score))+'</td>'+
   '<td><select data-id="'+l.id+'" data-act="state">'+opts+'</select></td>'+
   '<td><input data-id="'+l.id+'" data-act="note" value="'+_esc(l.notes||'')+'" placeholder="note"></td>'+
   '<td><button class="sm danger" data-id="'+l.id+'" data-act="remove">Remove</button></td></tr>';
}
function slotForm(role){
 const s=(DATA.config.slots||{})[role]||{};
 return '<div class=row data-role="'+role+'"><div class=field><label>'+role.toUpperCase()+' open</label>'+
   '<input class=s_open value="'+_esc(s.open==null?'':s.open)+'" size=4></div>'+
   '<div class=field><label>min ilvl</label><input class=s_ilvl value="'+_esc(s.ilvl==null?'':s.ilvl)+'" size=5></div>'+
   '<div class=field><label>note</label><input class=s_note value="'+_esc(s.note||'')+'"></div>'+
   '<button class=sm data-role="'+role+'" data-act="slot">Save</button></div>';
}
async function load(){
 DATA=await _api('/api/recruiting'); if(!DATA)return;
 // capture panel
 const cap=DATA.capture||{};
 let capHtml='<h2>Capture</h2>';
 if(DATA.scout_available){
   capHtml+='<div class=row><div class=field><label>Class (optional)</label><input id=capclass placeholder="Paladin"></div>'+
     '<button id=capbtn'+(cap.running?' disabled':'')+'>Capture leads</button></div>'+
     '<p class=muted id=capst>'+_esc(cap.running?'Scouting…':(cap.last_summary||cap.last_error||'idle'))+'</p>';
 } else { capHtml+='<p class=muted>Live scouting needs Warcraft Logs credentials. Manage existing leads below.</p>'; }
 document.getElementById('capture').innerHTML=capHtml;
 if(DATA.scout_available){document.getElementById('capbtn').onclick=capture;}
 // leads
 const lw=document.getElementById('leads'); lw.classList.remove('muted');
 lw.innerHTML=DATA.leads.length?('<table><thead><tr><th>#</th><th>Lead</th><th class=num>Score</th>'+
   '<th>State</th><th>Note</th><th></th></tr></thead><tbody>'+DATA.leads.map(leadRow).join('')+'</tbody></table>')
   :'<p class=muted>No leads yet. Capture some (or run <code>/recruit lead capture</code> in Discord).</p>';
 // config
 const rules=(DATA.config.rules||[]).map((r,i)=>'<li>'+_esc(r)+
   ' <button class="sm danger" data-idx="'+i+'" data-act="delrule">×</button></li>').join('');
 document.getElementById('cfg').classList.remove('muted');
 document.getElementById('cfg').innerHTML=
   DATA.roles.map(slotForm).join('')+
   '<h2>Trial rules</h2><ul id=rules>'+(rules||'<li class=muted>None (board shows defaults)</li>')+'</ul>'+
   '<div class=row><div class=field><label>Add rule</label><input id=newrule></div><button id=addrule>Add</button></div>'+
   '<h2>Pitch message</h2><div class=row><div class=field><label>Override (blank = default)</label>'+
   '<input id=msg value="'+_esc(DATA.config.message||'')+'" size=50></div><button id=savemsg>Save</button></div>'+
   '<p><button class="ghost" id=reset>Reset all recruitment config</button></p>';
 document.getElementById('addrule').onclick=async()=>{const t=document.getElementById('newrule').value.trim();
   if(t&&await _send('POST','/api/recruiting/rules',{text:t}))load();};
 document.getElementById('savemsg').onclick=async()=>{
   if(await _send('PUT','/api/recruiting/message',{text:document.getElementById('msg').value}))load();};
 document.getElementById('reset').onclick=async()=>{if(confirm('Clear all recruitment config?')&&
   await _send('POST','/api/recruiting/reset'))load();};
}
async function capture(){
 const wow_class=document.getElementById('capclass').value.trim();
 const r=await _send('POST','/api/leads/capture',wow_class?{wow_class}:{});
 if(r){document.getElementById('capbtn').disabled=true;poll();}
}
async function poll(){try{const d=await _api('/api/recruiting');const c=d.capture||{};
 document.getElementById('capst').textContent=c.running?'Scouting…':(c.last_summary||c.last_error||'idle');
 if(c.running){setTimeout(poll,2500);}else{load();}}catch(e){}}
document.getElementById('leads').addEventListener('change',async(e)=>{
 const el=e.target.closest('[data-act="state"]'); if(!el)return;
 await _send('POST','/api/leads/'+el.dataset.id+'/state',{state:el.value});});
document.getElementById('leads').addEventListener('click',async(e)=>{
 const b=e.target.closest('button[data-act="remove"]'); if(!b)return;
 if(confirm('Remove lead #'+b.dataset.id+'?')&&await _send('DELETE','/api/leads/'+b.dataset.id))load();});
document.getElementById('leads').addEventListener('keydown',async(e)=>{
 const el=e.target.closest('[data-act="note"]'); if(!el||e.key!=='Enter')return;
 if(await _send('PUT','/api/leads/'+el.dataset.id+'/note',{note:el.value}))el.blur();});
document.getElementById('cfg').addEventListener('click',async(e)=>{
 const slot=e.target.closest('button[data-act="slot"]');
 if(slot){const row=slot.closest('[data-role]');
   const body={open:row.querySelector('.s_open').value,ilvl:row.querySelector('.s_ilvl').value,
     note:row.querySelector('.s_note').value};
   if(await _send('PUT','/api/recruiting/slots/'+slot.dataset.role,body))load();return;}
 const del=e.target.closest('button[data-act="delrule"]');
 if(del&&await _send('DELETE','/api/recruiting/rules/'+del.dataset.idx))load();});
load();
</script>"""
    return _layout(config.guild_name, "Recruiting", body, "recruiting")


def _render_members(config: Config) -> str:
    body = """
<h2>Member admin</h2>
<p class=muted>Edit the officer-owned fields (discord link, notes, team, officer role,
main/alt). Roster sync never touches these. Press Enter or Save on a row.</p>
<div class=row>
  <div class=field><label>Search by name</label><input id=q placeholder="all members"></div>
  <button id=go>Search</button>
</div>
<div id=list class=muted>Loading…</div>
""" + _ADMIN_JS + """
<script>
function row(m){
 const main=m.is_main?'main':('alt of '+_esc(m.main_name||('#'+m.main_character_id)));
 return '<tr data-id="'+m.id+'"><td>'+_esc(m.name)+' <span class=muted>'+_esc(m.class||'')+'</span><br>'+
   '<span class=muted>'+main+'</span></td>'+
   '<td><input class=f_discord value="'+_esc(m.discord_id||'')+'" placeholder="discord id" size=12></td>'+
   '<td><input class=f_team value="'+_esc(m.raid_team||'')+'" placeholder="team" size=8></td>'+
   '<td><input class=f_officer value="'+_esc(m.officer_role||'')+'" placeholder="officer role" size=10></td>'+
   '<td><input class=f_main value="'+_esc(m.main_name||'')+'" placeholder="main char (blank=is main)" size=12></td>'+
   '<td><input class=f_notes value="'+_esc(m.notes||'')+'" placeholder="notes" size=18></td>'+
   '<td><button class=sm data-act=save>Save</button></td></tr>';
}
async function load(){
 const q=document.getElementById('q').value.trim();
 const d=await _api('/api/admin/members'+(q?('?q='+encodeURIComponent(q)):'')); if(!d)return;
 const w=document.getElementById('list'); w.classList.remove('muted');
 w.innerHTML=d.members.length?('<table><thead><tr><th>Character</th><th>Discord</th><th>Team</th>'+
   '<th>Officer</th><th>Main</th><th>Notes</th><th></th></tr></thead><tbody>'+d.members.map(row).join('')+'</tbody></table>')
   :'<p class=muted>No members match.</p>';
}
async function save(tr){
 const body={discord_id:tr.querySelector('.f_discord').value,raid_team:tr.querySelector('.f_team').value,
   officer_role:tr.querySelector('.f_officer').value,notes:tr.querySelector('.f_notes').value,
   main_character:tr.querySelector('.f_main').value};
 if(await _send('PUT','/api/admin/members/'+tr.dataset.id,body))load();
}
document.getElementById('go').onclick=load;
document.getElementById('q').addEventListener('keydown',e=>{if(e.key==='Enter')load();});
document.getElementById('list').addEventListener('click',e=>{
 const b=e.target.closest('button[data-act=save]'); if(b)save(b.closest('tr'));});
load();
</script>"""
    return _layout(config.guild_name, "Members", body, "members")


def main() -> None:  # pragma: no cover - server entrypoint
    import uvicorn

    config = Config.from_env()
    uvicorn.run(create_app(config), host=config.web_host, port=config.web_port)


if __name__ == "__main__":  # pragma: no cover
    main()
