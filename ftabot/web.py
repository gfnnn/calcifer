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

from fastapi import Cookie, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse

from . import views
from .config import Config
from .db import Database
from .models import Member, now_iso
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


def main() -> None:  # pragma: no cover - server entrypoint
    import uvicorn

    config = Config.from_env()
    uvicorn.run(create_app(config), host=config.web_host, port=config.web_port)


if __name__ == "__main__":  # pragma: no cover
    main()
