"""Tests for the headless sync CLI (scripts/sync_cli.py).

The network-bound ``sync_guild`` is replaced with a fake that seeds the passed
database, so we can verify the CLI wiring: config/db plumbing, the summary
print, and the optional ``--list`` roster dump — all offline.
"""

from __future__ import annotations

import argparse

from ftabot.config import Config
from ftabot.models import Member
from ftabot.sync import SyncResult
from scripts import sync_cli


def _args(tmp_path, **overrides):
    base = dict(name=None, realm=None, region=None, no_prune=False, list=False)
    base.update(overrides)
    return argparse.Namespace(**base)


async def test_run_prints_summary_and_roster(tmp_path, monkeypatch, capsys):
    db_path = str(tmp_path / "cli.db")
    monkeypatch.setattr(
        sync_cli.Config, "from_env",
        classmethod(lambda cls: Config(database_path=db_path)),
    )

    captured = {}

    async def _fake_sync(db, config, *, name, realm, region, prune):
        captured["prune"] = prune
        await db.upsert_synced(
            Member("Ashbringer", class_name="Paladin", item_level=632,
                   mythic_plus_score=3000, active_spec="Protection")
        )
        return SyncResult(fetched=1, upserted=1, sources=("Raider.IO",))

    monkeypatch.setattr(sync_cli, "sync_guild", _fake_sync)

    await sync_cli._run(_args(tmp_path, list=True))

    out = capsys.readouterr().out
    assert "Synced 1 member(s)" in out
    assert "Roster (by M+ score):" in out
    assert "Ashbringer" in out
    assert captured["prune"] is True  # prune defaults on


async def test_run_no_prune_flag_inverts_prune(tmp_path, monkeypatch, capsys):
    db_path = str(tmp_path / "cli2.db")
    monkeypatch.setattr(
        sync_cli.Config, "from_env",
        classmethod(lambda cls: Config(database_path=db_path)),
    )

    seen = {}

    async def _fake_sync(db, config, *, name, realm, region, prune):
        seen["prune"] = prune
        return SyncResult()

    monkeypatch.setattr(sync_cli, "sync_guild", _fake_sync)

    await sync_cli._run(_args(tmp_path, no_prune=True))
    assert seen["prune"] is False
