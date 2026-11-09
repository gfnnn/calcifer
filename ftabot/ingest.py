"""Inbound ingest listener: the only HTTP surface the bot hosts.

There is no dashboard and no browsable API. This is a tiny aiohttp listener
for the two data sources that *push* to us, plus a liveness probe for the host:

  * ``GET  /healthz``              liveness (DB reachable + Discord gateway state)
  * ``POST /webhooks/raidhelper``  Raid-Helper event pushes (events board + alerts)
  * ``POST /api/upload/savedvars`` the GM's addon SavedVariables dump (data spine)

Both POST routes are secret-protected and answer 503 until their secret is
configured, so an unconfigured deploy exposes nothing but ``/healthz``.
Everything else is controlled through Discord commands.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
from datetime import datetime, timezone

from aiohttp import web

from .config import Config
from .db import Database

log = logging.getLogger("ftabot.ingest")

# SavedVariables dumps (RCLootCouncil history, GRM) run to several MB; aiohttp's
# 1 MB default would reject them.
_MAX_BODY_BYTES = 64 * 1024 * 1024

_EVENT_RETENTION_SECONDS = 14 * 86400


def _secret_matches(provided: str | None, expected: str) -> bool:
    """Constant-time secret comparison (avoids leaking length/prefix via timing)."""
    return hmac.compare_digest((provided or "").encode(), expected.encode())


def _error(status: int, detail: str) -> web.Response:
    return web.json_response({"detail": detail}, status=status)


class Ingest:
    """The listener and the state its handlers share.

    ``feed_sender`` and ``bot`` are set by ``serve.py`` once the bot exists: with
    a sender, an inbound webhook posts instantly; without one it stays queued in
    ``feed_outbox`` for the feed loop to drain.
    """

    def __init__(self, config: Config):
        self.config = config
        self.db = Database(config.database_path)
        self.feed_sender = None
        self.bot = None

    # -- app wiring ------------------------------------------------------------

    def app(self) -> web.Application:
        app = web.Application(client_max_size=_MAX_BODY_BYTES)
        app.router.add_get("/healthz", self.healthz)
        app.router.add_post("/webhooks/raidhelper", self.raidhelper_webhook)
        app.router.add_post("/api/upload/savedvars", self.upload_savedvars)
        app.on_startup.append(self._on_startup)
        app.on_cleanup.append(self._on_cleanup)
        return app

    async def _on_startup(self, _app: web.Application) -> None:
        await self.db.connect()

    async def _on_cleanup(self, _app: web.Application) -> None:
        await self.db.close()

    async def serve(self) -> None:
        """Listen until cancelled (a long-running task for ``serve.py``)."""
        runner = web.AppRunner(self.app(), access_log=None)
        await runner.setup()
        try:
            await web.TCPSite(runner, self.config.web_host, self.config.web_port).start()
            await asyncio.Event().wait()
        finally:
            await runner.cleanup()

    # -- handlers --------------------------------------------------------------

    async def healthz(self, _request: web.Request) -> web.Response:
        # Stays 200 whenever the process can serve and reach the DB, so a
        # transient Discord gateway reconnect can't trip a host restart storm.
        # The gateway state rides in the body instead; a fully dead bot task is
        # already caught by serve.py's supervisor.
        payload: dict[str, object] = {"status": "ok", "members": await self.db.count()}
        bot = self.bot
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
        return web.json_response(payload)

    async def raidhelper_webhook(self, request: web.Request) -> web.Response:
        from .feeds.runner import drain_outbox
        from .router import resolve
        from .services.raidhelper import parse_webhook

        config, db = self.config, self.db
        secret = config.raidhelper_webhook_secret
        if not secret:
            return _error(503, "Raid-Helper webhook disabled. Set RAIDHELPER_WEBHOOK_SECRET.")
        provided = request.query.get("token") or request.headers.get("X-Signature")
        if not _secret_matches(provided, secret):
            return _error(401, "Invalid webhook secret.")

        try:
            payload = await request.json()
        except (ValueError, UnicodeDecodeError):
            return _error(400, "Body must be JSON.")

        event = parse_webhook(payload)
        if event is None:
            return _error(400, "Unrecognised payload.")

        # Persist the event's current state for the Weekly Events board *before*
        # the alert dedup/route logic, so the board reflects every webhook even
        # when the alert is a duplicate, muted, or has nowhere to post.
        ex = event.extra
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
            prune_before_epoch=now_epoch - _EVENT_RETENTION_SECONDS,
        )

        if not await db.filter_unseen([event]):
            return web.json_response({"status": "duplicate"})

        if event.source in await db.muted_sources():
            await db.mark_seen(event.source, event.external_id)
            return web.json_response({"status": "muted"})

        decision = resolve(
            event, await db.feed_rules(), default_channel_id=config.raidhelper_channel
        )
        await db.mark_seen(event.source, event.external_id)
        if decision is None:
            return web.json_response({"status": "no_route"})

        await db.enqueue_outbox(
            decision.channel_id, event.to_json(), json.dumps(decision.style)
        )
        sender = self.feed_sender
        if sender is not None:
            posted = await drain_outbox(db, config, sender)
            # The guardrail may have held the post back (off / unconfirmed); it
            # stays queued in that case rather than being silently dropped.
            return web.json_response({"status": "posted" if posted else "queued"})
        return web.json_response({"status": "queued"})

    async def upload_savedvars(self, request: web.Request) -> web.Response:
        """Ingest a desktop-addon SavedVariables dump (the data-spine intake).

        The GM's uploader (``scripts/upload_savedvars.py``) POSTs the raw
        ``.lua`` text here. We auto-detect which addon(s) it carries by their
        global table names and route each into the spine.
        """
        from . import spine
        from .services import savedvars

        config = self.config
        secret = config.savedvars_upload_secret
        if not secret:
            return _error(503, "Upload disabled. Set SAVEDVARS_UPLOAD_SECRET to enable it.")
        provided = request.query.get("token") or request.headers.get("X-Upload-Token")
        if not _secret_matches(provided, secret):
            return _error(401, "Invalid upload token.")

        raw = (await request.read()).decode("utf-8", "replace")
        if not raw.strip():
            return _error(400, "Empty upload.")
        try:
            data = savedvars.parse_savedvariables(raw)
        except savedvars.LuaParseError as exc:
            return _error(400, f"Could not parse SavedVariables: {exc}")

        summary = await spine.ingest_savedvars(
            self.db,
            data,
            default_region=config.guild_region,
            default_realm=config.guild_realm,
        )
        if not summary["detected"]:
            return _error(
                422, "No recognised addon data (expected RCLootCouncilLootDB / GRM)."
            )
        return web.json_response(summary)


def main() -> None:  # pragma: no cover - standalone entrypoint
    logging.basicConfig(level=logging.INFO)
    asyncio.run(Ingest(Config.from_env()).serve())


if __name__ == "__main__":  # pragma: no cover
    main()
