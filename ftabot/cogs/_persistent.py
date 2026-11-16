"""Buttons that survive a restart: the shared persistent-component helper.

A normal ``discord.ui.View`` dies with the process, so its buttons go dead on
the next deploy. Calcifer's buttons instead carry everything needed to handle
a click in their ``custom_id``::

    calcifer:<namespace>:<action>:<arg>:<arg>...

and one :class:`ActionButton` (a ``discord.ui.DynamicItem``) is registered with
the bot in ``setup_hook``. discord.py matches any incoming component whose id
fits the template, rebuilds the item and calls the handler registered for
``(namespace, action)``. No message ids are stored and no state lives in
memory, so a click on a week-old message works straight after a restart.

Using it:

* ``@handler("journey", "greet")`` on ``async def fn(interaction, *args)``;
* ``view([button("journey", "greet", user_id, label="Said hi")])`` when sending;
* ``register(bot)`` once in ``setup_hook``.

Arguments are joined with ``:``, so they must not contain one, and the whole
id must fit Discord's 100-character limit (checked when the button is built).
Handlers are responsible for their own permission checks (``_guards.py``).
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable

import discord

PREFIX = "calcifer"
_MAX_CUSTOM_ID = 100

Handler = Callable[..., Awaitable[None]]
HANDLERS: dict[tuple[str, str], Handler] = {}


def handler(namespace: str, action: str) -> Callable[[Handler], Handler]:
    """Register the coroutine that handles ``calcifer:<namespace>:<action>:...``."""

    def decorate(fn: Handler) -> Handler:
        HANDLERS[(namespace, action)] = fn
        return fn

    return decorate


def custom_id(namespace: str, action: str, *args: object) -> str:
    """The stable id for a button. Raises ``ValueError`` if it cannot be parsed
    back (an argument containing ``:``) or is too long for Discord."""
    parts = [str(a) for a in args]
    if any(":" in p for p in parts):
        raise ValueError("button arguments must not contain ':'")
    value = ":".join([PREFIX, namespace, action, *parts])
    if len(value) > _MAX_CUSTOM_ID:
        raise ValueError(f"custom_id is over {_MAX_CUSTOM_ID} characters: {value!r}")
    return value


async def dispatch(interaction: discord.Interaction, namespace: str, action: str,
                   args: list[str]) -> None:
    """Route a click to its handler. An unknown action (a button from a build
    that has since removed it) gets a quiet reply rather than a failed click."""
    fn = HANDLERS.get((namespace, action))
    if fn is None:
        await interaction.response.send_message(
            "That button is no longer in use.", ephemeral=True
        )
        return
    await fn(interaction, *args)


class ActionButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=rf"{PREFIX}:(?P<ns>[a-z_]+):(?P<action>[a-z_]+)(?::(?P<args>.*))?",
):
    """Every Calcifer button: parsed from its ``custom_id``, handled by name."""

    def __init__(
        self,
        namespace: str,
        action: str,
        args: list[str],
        *,
        label: str = "​",
        style: discord.ButtonStyle = discord.ButtonStyle.secondary,
        disabled: bool = False,
        row: int | None = None,
    ) -> None:
        super().__init__(
            discord.ui.Button(
                label=label,
                style=style,
                disabled=disabled,
                custom_id=custom_id(namespace, action, *args),
                row=row,
            )
        )
        self.namespace, self.action, self.args = namespace, action, args

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Button,
        match: re.Match[str],
    ) -> "ActionButton":
        raw = match["args"]
        return cls(match["ns"], match["action"], raw.split(":") if raw else [])

    async def callback(self, interaction: discord.Interaction) -> None:
        await dispatch(interaction, self.namespace, self.action, self.args)


def button(
    namespace: str,
    action: str,
    *args: object,
    label: str,
    style: discord.ButtonStyle = discord.ButtonStyle.secondary,
    disabled: bool = False,
    row: int | None = None,
) -> ActionButton:
    return ActionButton(
        namespace, action, [str(a) for a in args],
        label=label, style=style, disabled=disabled, row=row,
    )


def view(buttons: list[ActionButton]) -> discord.ui.View:
    """A timeout-less view holding ``buttons`` (Discord lays out 5 per row)."""
    out = discord.ui.View(timeout=None)
    for item in buttons:
        out.add_item(item)
    return out


def rebuilt_view(message, *, disable: str | None = None) -> discord.ui.View:
    """The view already on ``message``, rebuilt from its components, with the
    button whose ``custom_id`` is ``disable`` greyed out.

    This is how a click marks one button done on a message we keep no state
    for: read what Discord shows, change one thing, write it back.
    """
    template = ActionButton.__discord_ui_compiled_template__
    buttons: list[ActionButton] = []
    for row_index, row in enumerate(getattr(message, "components", None) or []):
        for child in getattr(row, "children", None) or []:
            match = template.fullmatch(getattr(child, "custom_id", None) or "")
            if match is None:
                continue
            raw = match["args"]
            buttons.append(ActionButton(
                match["ns"], match["action"], raw.split(":") if raw else [],
                label=child.label or "\u200b",
                style=child.style,
                disabled=bool(child.disabled) or child.custom_id == disable,
                row=row_index,
            ))
    return view(buttons)


def register(bot) -> None:
    """Make every Calcifer button live again after a restart. Call once, in
    ``setup_hook``."""
    bot.add_dynamic_items(ActionButton)
