"""Shared weekly-baseline handling for boards that show movement.

A board that shows "since the last weekly reset" movement keeps one integer per
entity (a rank, a level) in ``board_baselines``. Every such board needs the
same three rules, so they live here once:

* first ever render: seed the baseline with the current values, so the opening
  post is a settled board rather than one stamped NEW against every row;
* otherwise compare against the stored baseline;
* the first render after the EU weekly reset rolls the baseline forward, so the
  *next* render starts counting the new week.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from .hot20 import _parse_dt, weekly_reset_before

if TYPE_CHECKING:
    from ..config import Config
    from ..db import Database


def entity_key(member) -> str:
    """The stable baseline key for a character (name, realm, region)."""
    return f"{member.character_name}|{member.realm_slug}|{member.region}".lower()


async def weekly_baseline(
    db: "Database",
    config: "Config",
    board_type: str,
    current: dict[str, int],
    *,
    now: datetime | None = None,
) -> dict[str, int]:
    """The baseline this render compares against, seeding/rolling as needed."""
    baseline, captured_at = await db.board_baseline(board_type)
    if not baseline:
        await db.roll_board_baseline(board_type, current)
        return dict(current)
    captured = _parse_dt(captured_at)
    last_reset = weekly_reset_before(
        now or datetime.now(timezone.utc), config.raid_timezone
    )
    if captured is None or captured < last_reset:
        await db.roll_board_baseline(board_type, current)
    return baseline
