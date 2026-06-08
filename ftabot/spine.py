"""Data-spine ingestion: route a parsed SavedVariables dump into the database.

One entry point shared by the web upload endpoint and any CLI: it detects which
desktop-addon globals a parsed SavedVariables file carries and feeds each into
the spine, returning a summary. It has no HTTP knowledge and takes an already
open ``Database``, so it is unit-tested offline against a temp DB.

Today it ingests RCLootCouncil loot into ``loot_history``; GRM identity merges
into ``members`` join here as that parser lands.
"""

from __future__ import annotations

from typing import Any

from .db import Database
from .identity import build_accounts
from .services import grm, rclootcouncil


async def ingest_savedvars(
    db: Database,
    parsed: dict[str, Any],
    *,
    default_region: str = "eu",
    default_realm: str = "hellfire",
) -> dict[str, Any]:
    """Ingest every recognised addon table in ``parsed``; return a summary.

    The summary lists which addons were ``detected`` and how many rows each
    contributed, so the uploader (and the GM, from their phone) gets a concrete
    receipt rather than a silent 200.
    """
    summary: dict[str, Any] = {"detected": []}
    if not isinstance(parsed, dict):
        return summary

    if isinstance(parsed.get("RCLootCouncilLootDB"), dict):
        summary["detected"].append("rclootcouncil")
        entries = rclootcouncil.loot_from_savedvars(parsed, default_region=default_region)
        summary["loot_parsed"] = len(entries)
        summary["loot_imported"] = await db.add_loot(entries)

    if isinstance(parsed.get("GRM_GuildMemberHistory_Save"), dict):
        summary["detected"].append("grm")
        records = grm.identities_from_savedvars(
            parsed, default_region=default_region, default_realm=default_realm
        )
        summary["identities_parsed"] = len(records)
        summary["identities_stored"] = await db.upsert_identities(records)
        alt_groups = grm.alt_groups_from_savedvars(parsed, default_realm=default_realm)
        summary["alt_groups"] = len(alt_groups)
        accounts = build_accounts(
            records, default_realm=default_realm, extra_alt_groups=alt_groups
        )
        summary["accounts"] = len(accounts)
        summary.update(await db.apply_accounts(accounts, default_region=default_region))

    return summary
