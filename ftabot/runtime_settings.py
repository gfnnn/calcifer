"""The runtime settings overlay: a small env + DB merge so the dashboard can
change a few operational values live, without a redeploy.

Frozen :class:`~ftabot.config.Config` is built once from the environment and
threaded everywhere, so a live edit can't mutate it. Instead the dashboard saves
an override into the ``settings`` table and the surfaces that consume these
values resolve them *through here* each time they post: the override wins, and
its absence falls back to the env-built ``Config`` (the boot default).

Scope is deliberately narrow — only values the loops re-read each tick belong
here: the posting guardrail (``posting_mode``) and its channels. Loop-launch
toggles (``board_refresh_minutes`` …) decide *at startup* whether a task spawns,
so they stay env-only and the dashboard shows them read-only.

``posting.py`` stays pure (no I/O); the single DB read lives in this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .posting import PostGate, PostingMode

if TYPE_CHECKING:
    from .config import Config
    from .db import Database


@dataclass(frozen=True)
class Overridable:
    """A single dashboard-editable setting: how to show, store and validate it."""

    key: str
    label: str
    kind: str  # "choice" | "channel" | "text"
    config_attr: str  # the matching frozen-Config field (the env default)
    choices: tuple[str, ...] = ()
    help: str = ""


# The whitelist of live-editable keys. The Settings UI and the write validator
# are both derived from this, so adding a key is a one-line change here.
OVERRIDABLE: tuple[Overridable, ...] = (
    Overridable(
        key="posting_mode",
        label="Posting mode",
        kind="choice",
        config_attr="posting_mode",
        choices=("off", "test", "live"),
        help="off = mute; test = everything to the test channel; "
        "live = promoted surfaces reach their real channel.",
    ),
    Overridable(
        key="test_channel_id",
        label="Test channel ID",
        kind="channel",
        config_attr="test_channel_id",
        help="Where test / un-promoted posts land. Required for test & live.",
    ),
    Overridable(
        key="feed_default_channel_id",
        label="Default feed channel ID",
        kind="channel",
        config_attr="feed_default_channel_id",
        help="Catches feed alerts with no specific routing rule.",
    ),
)

_BY_KEY: dict[str, Overridable] = {o.key: o for o in OVERRIDABLE}


class SettingError(ValueError):
    """A dashboard-supplied setting value failed validation."""


def normalise(key: str, raw: object) -> str | None:
    """Validate a raw dashboard value against the manifest, returning the value
    to store (a ``None``/empty channel clears it). Raises :class:`SettingError`."""
    spec = _BY_KEY.get(key)
    if spec is None:
        raise SettingError(f"Unknown setting '{key}'.")
    if raw is None:
        return None
    value = str(raw).strip()
    if spec.kind == "choice":
        value = value.lower()
        if value not in spec.choices:
            raise SettingError(
                f"{spec.label} must be one of {', '.join(spec.choices)}."
            )
        return value
    if spec.kind == "channel":
        if value == "":
            return None
        if not value.isdigit():
            raise SettingError(f"{spec.label} must be a numeric channel ID.")
        return value
    return value or None


async def overrides(db: "Database") -> dict[str, object]:
    """The saved overrides, limited to known keys (ignores stale rows)."""
    saved = await db.get_settings()
    return {k: v for k, v in saved.items() if k in _BY_KEY}


def _env_value(config: "Config", key: str) -> object:
    return getattr(config, _BY_KEY[key].config_attr, None)


async def effective(db: "Database", config: "Config", key: str) -> object:
    """The live value for a key: dashboard override if set, else the env default."""
    saved = await overrides(db)
    return saved[key] if key in saved else _env_value(config, key)


async def effective_post_gate(db: "Database", config: "Config") -> PostGate:
    """The posting guardrail with any live overrides applied."""
    mode = await effective(db, config, "posting_mode")
    test_channel = await effective(db, config, "test_channel_id")
    return PostGate(PostingMode.parse(mode if isinstance(mode, str) else None),
                    test_channel or None)


async def effective_feed_channel(db: "Database", config: "Config") -> str | None:
    """The default feed channel with any live override applied."""
    value = await effective(db, config, "feed_default_channel_id")
    return value or None


async def settings_overview(db: "Database", config: "Config") -> list[dict]:
    """For the Settings page: each key's env default, override and effective value."""
    saved = await overrides(db)
    out: list[dict] = []
    for spec in OVERRIDABLE:
        override = saved.get(spec.key)
        out.append(
            {
                "key": spec.key,
                "label": spec.label,
                "kind": spec.kind,
                "choices": list(spec.choices),
                "help": spec.help,
                "env": _env_value(config, spec.key),
                "override": override,
                "effective": override if spec.key in saved else _env_value(
                    config, spec.key
                ),
            }
        )
    return out
