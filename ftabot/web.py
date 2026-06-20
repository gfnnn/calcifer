"""Browser query layer: a FastAPI app over the same SQLite database.

Provides a read-only JSON API plus one server-rendered roster/search page so
the guild can browse the roster from a browser as well as Discord.

Run with:  python -m ftabot.web
"""

from __future__ import annotations

import asyncio
import hmac
import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import (
    Cookie,
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    Request,
)
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

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

# The rebuilt single-shell dashboard's static assets (HTML shell + CSS + JS).
# Served at /static; the shell itself is served at /app. Auth is enforced by the
# JSON APIs, so the shell loads unconditionally and reveals an unlock overlay on
# the first 401.
_STATIC_DIR = Path(__file__).parent / "static"


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

    # The shared token gate, as a FastAPI dependency. Defined here (before any
    # route) so every endpoint — including the member/roster reads — can opt in
    # with `dependencies=gated`.
    def _require_token(
        token: str | None = None,
        x_sync_token: str | None = Header(default=None),
        fta_sync_token: str | None = Cookie(default=None),
    ) -> None:
        _check_sync_token(token or x_sync_token or fta_sync_token)

    gated = [Depends(_require_token)]

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

    @app.get("/api/members", dependencies=gated)
    async def api_members(
        sort: str = Query("mythic_plus_score"),
        wow_class: str | None = Query(None, alias="class"),
        role: str | None = None,
        q: str | None = None,
    ) -> JSONResponse:
        members = await _query_members(db, sort=sort, wow_class=wow_class, role=role, q=q)
        return JSONResponse([m.to_dict() for m in members])

    @app.get("/api/members/{name}/detail", dependencies=gated)
    async def api_member_detail(name: str, realm: str | None = None) -> JSONResponse:
        """The rich profile payload (readiness, attendance, wishlist, loot, alts,
        identity) for the Roster drill-down — one call per character."""
        detail = await views.member_detail(db, config, name, realm=realm)
        if detail is None:
            raise HTTPException(status_code=404, detail="member not found")
        return JSONResponse(detail)

    @app.get("/api/members/{name}", dependencies=gated)
    async def api_member(name: str, realm: str | None = None) -> JSONResponse:
        realm = realm or config.guild_realm
        member = await db.get_member(name, realm, config.guild_region)
        if member is None:
            raise HTTPException(status_code=404, detail="member not found")
        return JSONResponse(member.to_dict(include_details=True))

    @app.get("/api/identity/{name}", dependencies=gated)
    async def api_identity(name: str, realm: str | None = None) -> JSONResponse:
        """The GRM identity record (alts, join date, rank, note) for a character."""
        record = await db.get_identity(
            name, realm or config.guild_realm, config.guild_region
        )
        if record is None:
            raise HTTPException(status_code=404, detail="no identity record")
        return JSONResponse(record)

    @app.get("/api/stats", dependencies=gated)
    async def api_stats() -> JSONResponse:
        return JSONResponse(await _stats(db))

    @app.get("/api/gm", dependencies=gated)
    async def api_gm() -> JSONResponse:
        return JSONResponse(await views.gm_report(db, config))

    @app.get("/api/roster", dependencies=gated)
    async def api_roster() -> JSONResponse:
        """Next raid's sign-ups, grouped into buckets, for the Raid Sign-ups tab."""
        return JSONResponse(await views.roster_board(db, config))

    @app.get("/api/raid", dependencies=gated)
    async def api_raid(team: str | None = None) -> JSONResponse:
        return JSONResponse(await views.raid_board(db, config, team=team))

    @app.get("/api/loot", dependencies=gated)
    async def api_loot() -> JSONResponse:
        return JSONResponse(await views.loot_overview(db, config))

    @app.get("/api/loot/priority", dependencies=gated)
    async def api_loot_priority(item: str) -> JSONResponse:
        return JSONResponse(await views.loot_priority(db, config, item_name=item))

    @app.get("/api/trends", dependencies=gated)
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

    @app.get("/api/home", dependencies=gated)
    async def api_home() -> JSONResponse:
        # The opinionated landing view: guild-health pulse + triage queue. Live
        # sync status (held in the app, not the DB) is merged on, mirroring
        # /api/overview.
        data = await views.home(db, config)
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

    # -- deeper analytics (enrich Raid / Loot / Recruitment) -----------------

    @app.get("/api/analytics/attendance", dependencies=gated)
    async def api_analytics_attendance() -> JSONResponse:
        return JSONResponse(await views.attendance_report(db, config))

    @app.get("/api/analytics/loot", dependencies=gated)
    async def api_analytics_loot() -> JSONResponse:
        return JSONResponse(await views.loot_analytics(db, config))

    @app.get("/api/analytics/wishlist", dependencies=gated)
    async def api_analytics_wishlist() -> JSONResponse:
        return JSONResponse(await views.wishlist_analytics(db, config))

    @app.get("/api/analytics/funnel", dependencies=gated)
    async def api_analytics_funnel() -> JSONResponse:
        return JSONResponse(await views.recruitment_funnel(db, config))

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

    # -- HTML root: the legacy pages are gone; send browsers to the shell --
    @app.get("/")
    async def index() -> RedirectResponse:
        return RedirectResponse(url="/app")

    # -- rebuilt single-shell dashboard --------------------------------------
    # One static shell, hash-routed and hydrated from the JSON APIs above. It is
    # served unconditionally; the unlock overlay appears on the first 401 and
    # posts to /api/sync/unlock (reusing the existing cookie flow).
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

    @app.get("/app", response_class=HTMLResponse)
    async def dashboard_shell() -> HTMLResponse:
        return HTMLResponse((_STATIC_DIR / "index.html").read_text(encoding="utf-8"))

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


def _safe_url(url: str | None) -> str | None:
    """Return ``url`` only if it is an ``http(s)`` link, else ``None``.

    ``html.escape`` stops attribute-breakout but not a ``javascript:``/``data:``
    scheme, so links built from API/roster data (a character's armory URL, an
    avatar) are scheme-checked before they reach an ``href``/``src``.
    """
    if url and url.lower().startswith(("http://", "https://")):
        return url
    return None



def main() -> None:  # pragma: no cover - server entrypoint
    import uvicorn

    config = Config.from_env()
    uvicorn.run(create_app(config), host=config.web_host, port=config.web_port)


if __name__ == "__main__":  # pragma: no cover
    main()
